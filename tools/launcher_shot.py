#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Screenshot the local launcher (serve_report.py) — with a fake HOME, never yours.

The launcher lists whatever sessions are on the machine, titles included, so photographing it
against a real ~/.claude would publish someone's work. This builds a throwaway home directory
with invented sessions and shoots that instead.

    python3 tools/launcher_shot.py        # -> docs/img/launcher.png
"""
import json, os, re, shutil, subprocess, sys, tempfile, time, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, 'web', 'img')
PORT = '8831'
CHROME = os.environ.get('CHROME', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')

# (project path as the session ran in, session title, how many sub-agents, days ago)
FAKE = [
    ('/Users/demo/projects/checkout-service', 'Refactor the checkout flow into modules', 19, 0),
    ('/Users/demo/projects/checkout-service', 'Add contract tests for the payment adapter', 6, 1),
    ('/Users/demo/projects/design-system', 'Migrate the button tokens to CSS variables', 11, 2),
    ('/Users/demo/projects/design-system', 'Audit the icon set for duplicates', 3, 4),
    ('/Users/demo/projects/data-pipeline', 'Trace the nightly job timeout', 8, 6),
]


def enc(path):
    return re.sub(r'[^A-Za-z0-9]', '-', path)


def write_session(pdir, uid, title, n_agents, when):
    main = os.path.join(pdir, uid + '.jsonl')
    lines = [json.dumps({'type': 'ai-title', 'aiTitle': title, 'sessionId': uid}),
             json.dumps({'type': 'user', 'timestamp': when.isoformat() + 'Z',
                         'cwd': os.path.dirname(pdir), 'message': {'content': 'start'}})]
    for i in range(40):
        lines.append(json.dumps({'type': 'assistant', 'timestamp': (when + dt.timedelta(minutes=i)).isoformat() + 'Z',
                                 'message': {'model': 'claude-opus-5',
                                             'usage': {'input_tokens': 20, 'cache_creation_input_tokens': 9000,
                                                       'cache_read_input_tokens': 180000, 'output_tokens': 1200}}}))
    open(main, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    subs = os.path.join(pdir, uid, 'subagents')
    os.makedirs(subs, exist_ok=True)
    for i in range(n_agents):
        json.dump({'agentType': ['explorer', 'implementer', 'reviewer'][i % 3],
                   'description': 'task %d' % (i + 1), 'spawnDepth': 1},
                  open(os.path.join(subs, 'agent-%03d.meta.json' % i), 'w', encoding='utf-8'))
        open(os.path.join(subs, 'agent-%03d.jsonl' % i), 'w', encoding='utf-8').write(
            json.dumps({'type': 'assistant', 'timestamp': (when + dt.timedelta(minutes=i)).isoformat() + 'Z',
                        'message': {'model': 'claude-sonnet-5',
                                    'usage': {'input_tokens': 5, 'cache_creation_input_tokens': 2000,
                                              'cache_read_input_tokens': 40000, 'output_tokens': 300}}}) + '\n')
    os.utime(main, (time.time() - 86400 * 0, time.time() - 86400 * 0))


def main():
    home = tempfile.mkdtemp(prefix='fake-home-')
    base = dt.datetime(2026, 3, 12, 9, 0)
    for i, (proj, title, n, days) in enumerate(FAKE):
        pdir = os.path.join(home, '.claude', 'projects', enc(proj))
        os.makedirs(pdir, exist_ok=True)
        uid = 'demo%04d-1111-2222-3333-44445555%04d' % (i, i)
        write_session(pdir, uid, title, n, base - dt.timedelta(days=days))
        os.utime(os.path.join(pdir, uid + '.jsonl'),
                 (time.time() - days * 86400, time.time() - days * 86400))

    env = dict(os.environ, HOME=home, PORT=PORT)
    srv = subprocess.Popen([sys.executable, os.path.join(ROOT, 'serve_report.py'), '--no-open'],
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(2.5)
        os.makedirs(OUT, exist_ok=True)
        out = os.path.join(OUT, 'launcher.png')
        prof = tempfile.mkdtemp(prefix='shot-')
        subprocess.run([CHROME, '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
                        '--no-first-run', '--no-default-browser-check', '--force-device-scale-factor=1',
                        '--window-size=1440,1000', '--virtual-time-budget=6000', '--user-data-dir=' + prof,
                        '--screenshot=' + out, 'http://127.0.0.1:%s/' % PORT],
                       check=False, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(prof, ignore_errors=True)
        ok = os.path.isfile(out) and os.path.getsize(out) > 5000
        print(('  ok   ' if ok else '  FAIL ') + out + (' (%.0f KB)' % (os.path.getsize(out) / 1024) if ok else ''))
    finally:
        srv.terminate()
        shutil.rmtree(home, ignore_errors=True)


if __name__ == '__main__':
    main()
