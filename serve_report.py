#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Drag-and-drop launcher for analyze_and_report.py.

    python3 serve_report.py            # opens http://127.0.0.1:8799 in the browser

The page lets you pick sessions Claude Code already stored on this machine
(~/.claude/projects), or drag in a copied log folder / .jsonl. Pressing Run executes
analyze_and_report.py on them and hands back the generated report.

Stdlib only, binds to 127.0.0.1 (never expose it — it reads local paths you give it).
"""
import glob, http.server, json, os, re, shutil, socketserver, subprocess, sys, tempfile, threading, time, urllib.parse, webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ANALYZER = os.path.join(HERE, 'analyze_and_report.py')
PROJECTS = os.path.join(os.path.expanduser('~'), '.claude', 'projects')
WORK = os.path.join(tempfile.gettempdir(), 'session-report-ui')
PORT = int(os.environ.get('PORT', '8799'))
KEEP = {'.jsonl', '.meta.json'}                        # everything the analyzer actually reads


def safe_join(root, rel):
    rel = rel.replace('\\', '/').lstrip('/')
    p = os.path.normpath(os.path.join(root, rel))
    if not p.startswith(os.path.normpath(root) + os.sep):
        raise ValueError('path escape: %r' % rel)
    return p


def n_agents(jsonl):
    d = os.path.splitext(jsonl)[0]
    try:
        return len([f for f in os.listdir(os.path.join(d, 'subagents')) if f.endswith('.meta.json')])
    except OSError:
        return 0


def session_title(jsonl, head=400):
    """Claude Code names its own sessions — the `ai-title` records sit in the first ~20 lines
    and get refreshed as the session goes on. Read a small head window and take the newest
    title in it; fall back to the first thing the user actually typed."""
    title = prompt = None
    try:
        with open(jsonl, encoding='utf-8', errors='replace') as fh:
            for i, line in enumerate(fh):
                if i > head:
                    break
                if '"aiTitle"' not in line and '"summary"' not in line and '"user"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                t = d.get('aiTitle') or (d.get('summary') if isinstance(d.get('summary'), str) else None)
                if t:
                    title = t
                elif prompt is None and d.get('type') == 'user':
                    c = (d.get('message') or {}).get('content')
                    txt = c if isinstance(c, str) else ' '.join(
                        p.get('text', '') for p in (c or []) if isinstance(p, dict) and p.get('type') == 'text')
                    txt = (txt or '').strip().replace('\n', ' ')
                    if txt and not txt.startswith('<'):
                        prompt = txt
    except OSError:
        pass
    out = title or prompt or ''
    return out[:80] + ('…' if len(out) > 80 else '')


def list_sessions():
    out = []
    if not os.path.isdir(PROJECTS):
        return out
    for name in os.listdir(PROJECTS):
        pdir = os.path.join(PROJECTS, name)
        if not os.path.isdir(pdir):
            continue
        ss = []
        for f in os.listdir(pdir):
            if not f.endswith('.jsonl'):
                continue
            full = os.path.join(pdir, f)
            try:
                st = os.stat(full)
            except OSError:
                continue
            ss.append({'id': f[:-6], 'path': full, 'size': st.st_size, 'mtime': st.st_mtime,
                       'agents': n_agents(full), 'title': session_title(full)})
        if ss:
            ss.sort(key=lambda s: -s['mtime'])
            out.append({'project': name, 'sessions': ss[:50]})
    out.sort(key=lambda p: -p['sessions'][0]['mtime'])
    return out


def find_transcripts(root):
    """Every transcript in an uploaded/dropped tree. One piece of work often spans several
    sessions, so all of them are returned; the ones carrying a subagents/ dir win, and only
    if there are none do the bare .jsonl files count."""
    withsub, bare = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        if 'subagents' in dirpath.split(os.sep):
            continue
        for f in filenames:
            if not f.endswith('.jsonl'):
                continue
            full = os.path.join(dirpath, f)
            (withsub if os.path.isdir(os.path.join(dirpath, f[:-6], 'subagents')) else bare).append(full)
    found = withsub or bare
    return sorted(found, key=os.path.getmtime)


# ---------------------------------------------------------------- smart linking
def transcript_cwds(jsonl, max_lines=4000, first_only=False):
    """The working directories a session ran in — Claude Code stamps `cwd` on its records."""
    seen, out = set(), []
    try:
        with open(jsonl, encoding='utf-8', errors='replace') as fh:
            for i, line in enumerate(fh):
                if i > max_lines:
                    break
                if '"cwd"' not in line:
                    continue
                try:
                    c = json.loads(line).get('cwd')
                except ValueError:
                    continue
                if c and c not in seen:
                    seen.add(c)
                    out.append(c)
                    if first_only:                 # the launch cwd is what a project dir encodes
                        break
    except OSError:
        pass
    return out


def project_root_of(path):
    """The project a working directory belongs to: the nearest folder up the tree that looks
    like a project root. Returns None when nothing on this machine matches."""
    p = os.path.abspath(path)
    marks = ('.git', 'package.json', 'pyproject.toml', 'go.mod', 'Cargo.toml', 'CLAUDE.md')
    while True:
        if any(os.path.exists(os.path.join(p, m)) for m in marks):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def detect_project(paths):
    """Where did these sessions run? The transcript stamps its cwd on every record."""
    for p in paths:
        p = os.path.expanduser(p)
        mains = find_transcripts(p) if os.path.isdir(p) else [p]
        for m in mains:
            for cwd in transcript_cwds(m):
                if not os.path.isdir(cwd):
                    continue                     # the run happened on another machine
                return project_root_of(cwd) or cwd, cwd
    return None, None


def encode_project(path):
    return re.sub(r'[^A-Za-z0-9]', '-', os.path.abspath(path))


def under(path, root):
    path, root = os.path.normpath(path), os.path.normpath(root)
    return path == root or path.startswith(root + os.sep)


def sessions_for_project(project):
    """Sessions Claude Code recorded for that folder (or any folder inside it).

    Claude Code names a project directory after the absolute path with every non-alphanumeric
    character turned into '-', which makes the name AMBIGUOUS: '/x/app' and '/x/app-ios' both
    encode to a name starting with '-x-app'. So the name is only a cheap pre-filter — each
    candidate is confirmed against the `cwd` the transcript itself recorded, which is not."""
    project = os.path.abspath(os.path.expanduser(project))
    enc = encode_project(project)
    hits = []
    if not os.path.isdir(PROJECTS):
        return hits
    for name in sorted(os.listdir(PROJECTS)):
        if not name.startswith(enc):                  # superset: a subdir encodes to enc + '-...'
            continue
        pdir = os.path.join(PROJECTS, name)
        if not os.path.isdir(pdir):
            continue
        exact = (name == enc)
        for f in sorted(os.listdir(pdir)):
            if not f.endswith('.jsonl'):
                continue
            full = os.path.join(pdir, f)
            cwds = transcript_cwds(full, max_lines=2000, first_only=True)
            if cwds:
                if any(under(c, project) for c in cwds):
                    hits.append(full)
            elif exact:                               # no cwd recorded — trust the exact name only
                hits.append(full)
    return hits


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *a):        # keep the console readable
        if '/api/upload' not in self.path:
            sys.stderr.write('  %s\n' % (fmt % a))

    # ---------- helpers ----------
    def send(self, code, body, ctype='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def query(self):
        q = urllib.parse.urlparse(self.path).query
        return {k: v[0] for k, v in urllib.parse.parse_qs(q).items()}

    # ---------- routes ----------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ('/', '/index.html'):
            try:
                html = open(os.path.join(HERE, 'index.html'), encoding='utf-8').read()
            except OSError:
                return self.send(500, 'index.html missing next to serve_report.py', 'text/plain; charset=utf-8')
            return self.send(200, html, 'text/html; charset=utf-8')
        if path == '/api/sessions':
            return self.send(200, {'projects': list_sessions(), 'projectsDir': PROJECTS})
        if path == '/api/detect':                       # session(s) -> project folder
            q = self.query()
            paths = [p for p in (q.get('paths', '')).split('\n') if p.strip()]
            root, cwd = detect_project(paths)
            return self.send(200, {'project': root, 'cwd': cwd,
                                   'note': None if root else 'that folder is not on this machine'})
        if path == '/api/match':                        # project folder -> its sessions
            q = self.query()
            eng = (q.get('project') or '').strip()
            if not eng:
                return self.send(400, {'error': 'project required'})
            hits = sessions_for_project(eng)
            return self.send(200, {'sessions': hits, 'agents': {h: n_agents(h) for h in hits}})
        if path.startswith('/out/'):
            rel = urllib.parse.unquote(path[len('/out/'):])
            try:
                full = safe_join(WORK, rel)
            except ValueError:
                return self.send(400, {'error': 'bad path'})
            if not os.path.isfile(full):
                return self.send(404, 'not found', 'text/plain; charset=utf-8')
            ctype = 'text/html; charset=utf-8' if full.endswith('.html') else 'application/json; charset=utf-8'
            with open(full, 'rb') as fh:
                return self.send(200, fh.read(), ctype)
        return self.send(404, {'error': 'no route'})

    def do_PUT(self):
        if not urllib.parse.urlparse(self.path).path == '/api/upload':
            return self.send(404, {'error': 'no route'})
        q = self.query()
        job, rel = q.get('job', ''), q.get('rel', '')
        if not re.fullmatch(r'[a-z0-9]{6,32}', job) or not rel:
            return self.send(400, {'error': 'job/rel required'})
        try:
            dest = safe_join(os.path.join(WORK, job, 'in'), urllib.parse.unquote(rel))
        except ValueError as e:
            return self.send(400, {'error': str(e)})
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        n = int(self.headers.get('Content-Length') or 0)
        with open(dest, 'wb') as fh:
            left = n
            while left > 0:
                chunk = self.rfile.read(min(1 << 20, left))
                if not chunk:
                    break
                fh.write(chunk)
                left -= len(chunk)
        return self.send(200, {'ok': True, 'bytes': n})

    def do_POST(self):
        route = urllib.parse.urlparse(self.path).path
        if route == '/api/resolve':
            # what did the browser just drop? It cannot hand over an absolute path, so the files
            # are uploaded first and this turns them back into paths: a transcript is matched to
            # the real one under ~/.claude/projects when it is the same session, else the upload
            # itself is the path.
            n = int(self.headers.get('Content-Length') or 0)
            try:
                req = json.loads(self.rfile.read(n) or b'{}')
            except ValueError:
                return self.send(400, {'error': 'bad json'})
            job = re.sub(r'[^a-z0-9]', '', str(req.get('job', '')).lower())[:32]
            root = os.path.join(WORK, job, 'in', 'log')
            if not job or not os.path.isdir(root):
                return self.send(400, {'error': 'nothing uploaded'})
            paths, local = [], True
            for m in find_transcripts(root):
                uuid = os.path.basename(m)[:-6]
                real = glob.glob(os.path.join(PROJECTS, '*', uuid + '.jsonl'))
                if real:
                    paths.append(real[0])
                else:
                    paths.append(m)
                    local = False
            return self.send(200, {'paths': paths, 'local': local})
        if route != '/api/run':
            return self.send(404, {'error': 'no route'})
        n = int(self.headers.get('Content-Length') or 0)
        try:
            req = json.loads(self.rfile.read(n) or b'{}')
        except ValueError:
            return self.send(400, {'error': 'bad json'})

        job = req.get('job') or ('j%d' % int(time.time() * 1000))
        job = re.sub(r'[^a-z0-9]', '', str(job).lower())[:32] or 'job'
        indir = os.path.join(WORK, job, 'in')
        outdir = os.path.join(WORK, job, 'out')
        os.makedirs(outdir, exist_ok=True)

        # sessions already on this machine (one or many — a run can span several sessions)
        wanted = req.get('paths') or ([req['path']] if req.get('path') else [])
        mains = []
        for p in wanted:
            p = os.path.expanduser(str(p).strip())
            if not p:
                continue
            mains += find_transcripts(p) if os.path.isdir(p) else [p]
        if not mains and os.path.isdir(indir):
            mains = find_transcripts(os.path.join(indir, 'log'))
        mains = [m for m in mains if os.path.isfile(m)]
        if not mains:
            return self.send(400, {'error': 'no transcript (.jsonl) found — drop the session '
                                            'folder or its <uuid>.jsonl, or pick one from the list'})

        cmd = [sys.executable, ANALYZER] + mains + ['--out', outdir]
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
        log = (p.stdout or '') + (p.stderr or '')
        if p.returncode != 0 or not os.path.isfile(os.path.join(outdir, 'report.html')):
            return self.send(200, {'ok': False, 'log': log or 'analyzer produced no report'})

        meta = {}
        try:
            meta = json.load(open(os.path.join(outdir, 'report-data.json'), encoding='utf-8'))['meta']
        except Exception:
            pass
        return self.send(200, {'ok': True, 'log': log, 'job': job, 'sessions': len(mains),
                               'url': '/out/%s/out/report.html' % job,
                               'file': os.path.join(outdir, 'report.html'),
                               'meta': {k: meta.get(k) for k in
                                        ('session', 'n_subagents', 'n_main_assistant', 'grand_total')}})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    if not os.path.isfile(ANALYZER):
        sys.exit('analyze_and_report.py must sit next to serve_report.py')
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    port = PORT
    for _ in range(20):                                   # port busy -> try the next one
        try:
            srv = Server(('127.0.0.1', port), Handler)
            break
        except OSError:
            port += 1
    else:
        sys.exit('no free port near %d' % PORT)
    url = 'http://127.0.0.1:%d/' % port
    print('session-report UI  ->', url)
    print('work dir:', WORK, '(cleared on start)')
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\nbye')


if __name__ == '__main__':
    main()
