#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-file Claude Code session telemetry report generator.

Analyzes a Claude Code session transcript (from ~/.claude/projects/<encoded-repo-path>/)
and builds a self-contained, trilingual (English default / Türkçe / Simplified Chinese)
report.html with interactive token-time charts, the parallel-agent flow timeline, and a
searchable + sortable per-task breakdown.

Deterministic: the same transcript always produces the same report. Nothing is inferred by a
model and nothing leaves the machine — it is a plain read of the JSONL the CLI already wrote.

Fully portable and dependency-free: no hardcoded machine paths or session ids, standard
library only. Works on any session, whatever the project was about.

Usage:
    python3 analyze_and_report.py                 # auto: CWD's project → the run with the most sub-agents
    python3 analyze_and_report.py <session-uuid>  # a specific session in the CWD's project
    python3 analyze_and_report.py <project>/<uuid>
    python3 analyze_and_report.py /abs/path/to/transcript.jsonl
    python3 analyze_and_report.py --list          # list the sessions available for the CWD's project

Optional flags:
    --out DIR        where to write the three output files (default: next to this script)

Or run the drag-and-drop UI:  python3 serve_report.py

Outputs (written next to this script):
    report-data.json   raw aggregated telemetry
    viewdata.json      enriched view model
    report.html        the standalone report — open this in a browser
"""
import json, glob, os, sys, re, collections, datetime as dt

IS_WINDOWS = sys.platform == 'win32'

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.getcwd()                     # project = CWD (run from the repo you want to analyze)
PROJECTS = os.path.join(os.path.expanduser('~'), '.claude', 'projects')

def encode_project(path):
    # Claude Code names a project dir after its abs path, non-alphanumerics -> '-'
    return re.sub(r'[^A-Za-z0-9]', '-', os.path.abspath(path))

def find_project_dir():
    # 1) Exact CWD match
    cand = os.path.join(PROJECTS, encode_project(REPO_ROOT))
    if os.path.isdir(cand): return cand
    # 2) Walk up the directory tree — handles running from a subdirectory
    path = REPO_ROOT
    while True:
        parent = os.path.dirname(path)
        if parent == path:          # reached filesystem root
            break
        cand = os.path.join(PROJECTS, encode_project(path))
        if os.path.isdir(cand):
            return cand
        path = parent
    # 3) Fallback: trailing directory name match
    base = os.path.basename(REPO_ROOT.rstrip('/\\'))
    hits = [d for d in glob.glob(os.path.join(PROJECTS, '*')) if os.path.isdir(d) and d.rstrip('/\\').endswith(base)]
    return hits[0] if len(hits) == 1 else cand

def sessions_in(pdir):   # top-level <uuid>.jsonl transcripts, newest first
    return sorted(glob.glob(os.path.join(pdir, '*.jsonl')), key=os.path.getmtime, reverse=True)

def n_subagents(jsonl):  # how many task-subagents this session spawned
    return len(glob.glob(os.path.join(os.path.splitext(jsonl)[0], 'subagents', '*.meta.json')))

def run_analysis(argv):
    PROJECT_DIR = find_project_dir()
    arg = argv[0] if argv else None

    if arg in ('--list', '-l'):
        print('project dir:', PROJECT_DIR)
        ss = sessions_in(PROJECT_DIR)
        if not ss: print('  (no sessions found)')
        for f in ss:
            mt = dt.datetime.fromtimestamp(os.path.getmtime(f)).strftime('%Y-%m-%d %H:%M')
            print('  %-38s %7.1f MB  %4d agents  %s'
                  % (os.path.basename(f)[:-6], os.path.getsize(f) / 1e6, n_subagents(f), mt))
        print('\n(default picks the session with the most agents; pass a uuid to override)')
        return None

    # one argument = one session; several = one report over all of them, because a single
    # pipeline run is often spread over several Claude Code sessions (/resume, a crash, a
    # new day). They are merged on the time axis, so the 20-minute idle-gap segmentation
    # keeps the per-session boundaries visible on its own.
    def resolve(a):
        if a.endswith('.jsonl'):
            m = os.path.expanduser(a); return m, m[:-6]
        if os.sep in a or '/' in a:                 # <project>/<uuid> or an absolute path
            r = a if os.path.isabs(a) else os.path.join(PROJECTS, a)
            r = os.path.expanduser(r)
            if os.path.isdir(r) and not os.path.isfile(r + '.jsonl'):   # a session dir was given
                inner = sorted(glob.glob(os.path.join(os.path.dirname(r), os.path.basename(r) + '.jsonl')))
                if inner: return inner[0], r
            return r + '.jsonl', r
        return os.path.join(PROJECT_DIR, a) + '.jsonl', os.path.join(PROJECT_DIR, a)  # bare uuid

    if argv:
        MAINS = [resolve(a) for a in argv]
    else:                                           # auto: the busiest session = most agents
        ss = sessions_in(PROJECT_DIR)
        if not ss:
            sys.exit('no transcripts found in %s\n(run this from inside the repo you worked in, '
                     'or pass a session uuid / .jsonl path; use --list to list)' % PROJECT_DIR)
        m = max(ss, key=lambda f: (n_subagents(f), os.path.getsize(f)))  # tiebreak: bigger
        MAINS = [(m, m[:-6])]

    missing = [m for m, _ in MAINS if not os.path.exists(m)]
    if missing:
        sys.exit('transcript not found: %s\n(pass a session uuid, <project>/<uuid>, a .jsonl path, or --list)'
                 % ', '.join(missing))
    MAINS.sort(key=lambda mr: os.path.getmtime(mr[0]))          # chronological
    ids = [os.path.basename(r)[:8] for _, r in MAINS]
    SESSION_SHORT = ids[0] if len(ids) == 1 else '%s +%d' % (ids[0], len(ids) - 1)
    for m, _ in MAINS:
        print('analyzing session', os.path.basename(m)[:8], '->', m)

    def parse_ts(s):
        if not s: return None
        return dt.datetime.fromisoformat(s.replace('Z','+00:00'))

    def usage_tokens(u):
        if not u: return None
        return {
            'in':    u.get('input_tokens',0) or 0,
            'cc':    u.get('cache_creation_input_tokens',0) or 0,
            'cr':    u.get('cache_read_input_tokens',0) or 0,
            'out':   u.get('output_tokens',0) or 0,
        }
    def zero(): return {'in':0,'cc':0,'cr':0,'out':0}
    def add(a,b):
        for k in a: a[k]+=b[k]
    def total(t): return t['in']+t['cc']+t['cr']+t['out']

    # ---------- MAIN THREAD ----------
    main_events = []  # (dt, tokens|None, kind, text)
    events = []       # (dt, tokens) for every assistant message (main + subagents) -> time chart
    for _MAIN,_ in MAINS:
      for line in open(_MAIN, encoding='utf-8', errors='replace'):
        try: d=json.loads(line)
        except: continue
        t=parse_ts(d.get('timestamp'))
        typ=d.get('type'); msg=d.get('message',{})
        if not t: continue
        if typ=='assistant' and isinstance(msg,dict):
            tok=usage_tokens(msg.get('usage'))
            main_events.append((t,tok,'assistant',''))
            if tok: events.append((t,tok))
        elif typ=='user' and isinstance(msg,dict):
            c=msg.get('content'); txt=''
            if isinstance(c,str): txt=c
            elif isinstance(c,list):
                for p in c:
                    if isinstance(p,dict) and p.get('type')=='text': txt+=p.get('text','')
            txt=txt.strip()
            if txt and not txt.startswith('<'):
                main_events.append((t,None,'user',txt[:120].replace('\n',' ')))
    main_events.sort(key=lambda x:x[0])

    # ---------- SUBAGENTS ----------
    subs=[]
    SUBDIRS = [os.path.join(r, 'subagents') for _, r in MAINS]
    for meta in sorted(f for d in SUBDIRS for f in glob.glob(os.path.join(d, '*.meta.json'))):
        m=json.load(open(meta, encoding='utf-8'))
        jf=meta.replace('.meta.json','.jsonl')
        first=last=None; tok=zero(); nmsg=0; model=None
        if os.path.exists(jf):
            for line in open(jf, encoding='utf-8', errors='replace'):
                try: d=json.loads(line)
                except: continue
                t=parse_ts(d.get('timestamp'))
                if t:
                    if not first or t<first: first=t
                    if not last or t>last: last=t
                if d.get('type')=='assistant':
                    mm=d.get('message',{})
                    u=usage_tokens(mm.get('usage'))
                    if u:
                        add(tok,u); nmsg+=1
                        if t: events.append((t,u))
                    if mm.get('model'): model=mm.get('model')
        subs.append({
            'id':os.path.basename(meta).replace('.meta.json','').replace('agent-',''),
            'type':m.get('agentType'),
            'desc':m.get('description',''),
            'depth':m.get('spawnDepth',1),
            'model':model,
            'first':first,'last':last,
            'dur':(last-first).total_seconds() if first and last else 0,
            'tok':tok,'nmsg':nmsg,
            'total':total(tok),
        })

    # ---------- GROUPING ----------
    # A session has no phases of its own — what it does have is a main thread and the
    # sub-agents it spawns. So every actor is a group: MAIN for the orchestrator, and one
    # group per agent type. Each group's sub-steps are the individual task descriptions.
    MAIN = 'MAIN'
    MAIN_NAME = 'Main thread (orchestrator)'

    def grp(s):
        ty = s['type'] or 'agent'
        return ty, (s['desc'] or '(no description)')

    group_names = {MAIN: MAIN_NAME}
    for s in subs:
        group_names[grp(s)[0]] = grp(s)[0]

    # ---------- AGGREGATE ----------
    phase_tok = {p: zero() for p in group_names}
    phase_main_tok = {p: zero() for p in group_names}
    phase_sub_tok = {p: zero() for p in group_names}
    substep_tok = collections.defaultdict(zero)   # (group, substep) -> tokens
    substep_count = collections.Counter()
    substep_dur = collections.defaultdict(float)
    phase_agent_busy = {p: 0.0 for p in group_names}

    # main-thread tokens are their own group: the orchestrator runs concurrently with every
    # sub-agent, so folding its spend into one of them would misattribute it.
    for t, tok, kind, _ in main_events:
        if kind == 'assistant' and tok:
            add(phase_tok[MAIN], tok); add(phase_main_tok[MAIN], tok)
            add(substep_tok[(MAIN, 'Assistant turns')], tok)
            substep_count[(MAIN, 'Assistant turns')] += 1

    for s in subs:
        p, ss = grp(s)
        add(phase_tok[p], s['tok']); add(phase_sub_tok[p], s['tok'])
        add(substep_tok[(p, ss)], s['tok'])
        substep_count[(p, ss)] += 1
        substep_dur[(p, ss)] += s['dur']
        phase_agent_busy[p] += s['dur']

    # ---------- TIMELINE (idle-gap segmentation) ----------
    # merge main-thread event times + subagent spans into one activity stream
    stream=[]  # (dt, label)
    for t,tok,kind,txt in main_events:
        if kind=='user': stream.append((t,'USER: '+txt))
        else: stream.append((t,None))
    for s in subs:
        if s['first']: stream.append((s['first'], 'SPAWN:'+s['type']+':'+(s['desc'] or '')))
    stream.sort(key=lambda x:x[0])

    GAP=20*60  # 20 min idle splits a block
    blocks=[]
    cur=None
    for t,label in stream:
        if cur is None or (t-cur['end']).total_seconds()>GAP:
            cur={'start':t,'end':t,'events':[], 'spawns':collections.Counter(),'users':[]}
            blocks.append(cur)
        cur['end']=t
        if label:
            if label.startswith('USER: '): cur['users'].append((t,label[6:]))
            elif label.startswith('SPAWN:'):
                _,ty,desc=label.split(':',2)
                cur['spawns'][ty]+=1
                cur['events'].append((t,ty,desc))

    # the main thread's "work-hours" = its active footprint: the summed duration of the activity
    # blocks it drives (the same active windows the report calls "active wall-clock"). It overlaps
    # sub-agent time (they run concurrently), so it stays its own group, never added onto another.
    phase_agent_busy[MAIN]=sum((b['end']-b['start']).total_seconds() for b in blocks
        if not ((b['end']-b['start']).total_seconds()<30 and not b['users'] and not b['spawns']))

    # ---------- OUTPUT ----------
    def tok_out(t): return {**t,'total':total(t)}
    out={
     'meta':{
       'session':SESSION_SHORT,
       'span_start': main_events[0][0].isoformat(),
       'span_end': main_events[-1][0].isoformat(),
       'n_subagents': len(subs),
       'n_main_assistant': sum(1 for e in main_events if e[2]=='assistant'),
     },
     'groups':[],
     'substeps':[],
     'timeline':[],
     'subagent_type_totals':[],
    }
    grand=zero()
    # agent-type groups first (biggest spend first), the main thread last: it is its own
    # category, never counted inside another. grand_total includes it — it is real spend.
    order=[p for p in sorted(group_names, key=lambda k:-total(phase_tok[k])) if p!=MAIN]+[MAIN]
    for p in order:
        if p==MAIN and total(phase_tok[p])==0: continue
        add(grand,phase_tok[p])
        out['groups'].append({
          'id':p,'name':group_names[p],
          'tokens':tok_out(phase_tok[p]),
          'main_tokens':tok_out(phase_main_tok[p]),
          'sub_tokens':tok_out(phase_sub_tok[p]),
          'agent_busy_sec':phase_agent_busy[p],
          'win_start':None,
        })
    out['meta']['grand_total']=tok_out(grand)

    for (p,ss),tok in sorted(substep_tok.items(), key=lambda kv:-total(kv[1])):
        out['substeps'].append({
          'group':p,'group_name':group_names[p],'substep':ss,
          'tokens':tok_out(tok),'count':substep_count[(p,ss)],
          'dur_sec':substep_dur[(p,ss)],
        })

    # subagent type totals
    by_ty=collections.defaultdict(lambda:{'tok':zero(),'n':0,'dur':0.0})
    for s in subs:
        by_ty[s['type']]['tok']=by_ty[s['type']]['tok']
        add(by_ty[s['type']]['tok'],s['tok'])
        by_ty[s['type']]['n']+=1
        by_ty[s['type']]['dur']+=s['dur']
    for ty,v in sorted(by_ty.items(), key=lambda kv:-total(kv[1]['tok'])):
        out['subagent_type_totals'].append({'type':ty,'tokens':tok_out(v['tok']),'count':v['n'],'dur_sec':v['dur']})

    # ---------- PER-TASK (every subagent, one row each) ----------
    _FAR=dt.datetime.max.replace(tzinfo=dt.timezone.utc)
    out['subagents']=[]
    for s in sorted(subs, key=lambda x:(x['first'] or _FAR)):
        p,ss=grp(s)
        out['subagents'].append({
            'id':s['id'],'type':s['type'],'desc':s['desc'],'model':s.get('model'),
            'group':p,'group_name':group_names[p],'substep':ss,
            'start':s['first'].isoformat() if s['first'] else None,
            'end':s['last'].isoformat() if s['last'] else None,
            'dur_sec':s['dur'],'nmsg':s['nmsg'],
            'tokens':tok_out(s['tok']),
        })

    # which agent types each activity block actually used (drives the block narrative)
    for s in subs:
        if not s['first']: continue
        _p,_=grp(s)
        for b in blocks:
            if b['start']<=s['first']<=b['end']:
                b.setdefault('groupc',collections.Counter())[_p]+=1
                break

    for b in blocks:
        dur=(b['end']-b['start']).total_seconds()
        if dur<30 and not b['users'] and not b['spawns']: continue
        out['timeline'].append({
          'start':b['start'].isoformat(),'end':b['end'].isoformat(),
          'dur_sec':dur,
          'spawns':dict(b['spawns']),
          'groups':dict(b.get('groupc',{})),
          'n_spawn':sum(b['spawns'].values()),
          'users':[{'t':t.isoformat(),'txt':x} for t,x in b['users']],
          'sample_events':[{'t':t.isoformat(),'ty':ty,'desc':desc} for t,ty,desc in b['events'][:6]],
        })

    # ---------- TOKEN TIME-SERIES (every assistant msg, compact [ms,in,cc,cr,out]) ----------
    events.sort(key=lambda x:x[0])
    out['events']=[[int(t.timestamp()*1000), tok['in'], tok['cc'], tok['cr'], tok['out']] for t,tok in events]
    out['events_key']=['ms','in','cc','cr','out']

    # ---------- GROUP SPANS (the timeline's group strip: real per-group [min,max], each on
    #            its own row so interleaved agent types are shown truthfully) ----------
    _span={}
    for s in subs:
        _p,_=grp(s)
        if not s['first']: continue
        _a=s['first']; _b=s['last'] or s['first']
        if _p not in _span: _span[_p]=[_a,_b]
        else:
            if _a<_span[_p][0]: _span[_p][0]=_a
            if _b>_span[_p][1]: _span[_p][1]=_b
    # the main thread is drawn first as a band spanning the WHOLE run — it drives continuously
    # from the first message to the last.
    _pw=[]
    if main_events:
        _pw.append({'id':MAIN,'name':MAIN_NAME,
                    'start':main_events[0][0].isoformat(),'end':main_events[-1][0].isoformat()})
    _pw+=[{'id':p,'name':group_names[p],
        'start':_span[p][0].isoformat(),'end':_span[p][1].isoformat()}
        for p in [g for g in order if g!=MAIN] if p in _span]
    out['group_windows']=_pw

    return out

HTML = '''<div id="app"></div>
<style>
:root{
  --bg:#eef1f6; --surface:#ffffff; --surface2:#f5f7fb; --ink:#141d2b; --ink2:#5a6a82;
  --line:#dde3ee; --line2:#ccd5e3; --accent:#3457d5; --accent-soft:#e4e9fb;
  --p1:#159aa8; --p2:#6b5cf0; --p3:#d68420; --p4:#e14a67; --p5:#2fa876;
  --in:#3457d5; --cc:#6b5cf0; --cr:#8b9bb4; --out:#e14a67;
  --good:#2fa876; --warn:#d68420; --crit:#e14a67;
  --shadow:0 1px 2px rgba(20,29,43,.06),0 8px 24px rgba(20,29,43,.06);
}
@media (prefers-color-scheme:dark){:root{
  --bg:#0c1117; --surface:#141b25; --surface2:#1a232f; --ink:#e9eef6; --ink2:#8f9fb5;
  --line:#232f3d; --line2:#2d3b4c; --accent:#6d8bff; --accent-soft:#1c2740;
  --p1:#2bc0d0; --p2:#8a7cff; --p3:#e6a24a; --p4:#f0687f; --p5:#43c48f;
  --in:#6d8bff; --cc:#8a7cff; --cr:#5f7191; --out:#f0687f;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px rgba(0,0,0,.35);
}}
:root[data-theme="light"]{
  --bg:#eef1f6; --surface:#ffffff; --surface2:#f5f7fb; --ink:#141d2b; --ink2:#5a6a82;
  --line:#dde3ee; --line2:#ccd5e3; --accent:#3457d5; --accent-soft:#e4e9fb;
  --p1:#159aa8; --p2:#6b5cf0; --p3:#d68420; --p4:#e14a67; --p5:#2fa876;
  --in:#3457d5; --cc:#6b5cf0; --cr:#8b9bb4; --out:#e14a67;
  --shadow:0 1px 2px rgba(20,29,43,.06),0 8px 24px rgba(20,29,43,.06);
}
:root[data-theme="dark"]{
  --bg:#0c1117; --surface:#141b25; --surface2:#1a232f; --ink:#e9eef6; --ink2:#8f9fb5;
  --line:#232f3d; --line2:#2d3b4c; --accent:#6d8bff; --accent-soft:#1c2740;
  --p1:#2bc0d0; --p2:#8a7cff; --p3:#e6a24a; --p4:#f0687f; --p5:#43c48f;
  --in:#6d8bff; --cc:#8a7cff; --cr:#5f7191; --out:#f0687f;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px rgba(0,0,0,.35);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);overflow-x:hidden;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  line-height:1.5;-webkit-font-smoothing:antialiased;}
.mono{font-family:ui-monospace,"SF Mono","JetBrains Mono",Menlo,monospace;font-variant-numeric:tabular-nums;}
.wrap{max-width:1120px;margin:0 auto;padding:0 20px 80px;}
.eyebrow{font-family:ui-monospace,Menlo,monospace;font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--ink2);}
h1{font-size:clamp(26px,4vw,40px);line-height:1.08;letter-spacing:-.02em;margin:.35em 0 .1em;text-wrap:balance;}
h2{font-size:20px;letter-spacing:-.01em;margin:0;}
.sub{color:var(--ink2);max-width:62ch;}
section{margin-top:54px;}
.sechead{display:flex;align-items:baseline;gap:12px;border-bottom:1px solid var(--line);padding-bottom:12px;margin-bottom:22px;flex-wrap:wrap;}
.sechead .num{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:var(--accent);letter-spacing:.1em;}
.sechead .note{margin-left:auto;color:var(--ink2);font-size:13px;}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);}
/* header */
header{padding-top:44px;}
.hgrid{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px;}
.chip{font-family:ui-monospace,Menlo,monospace;font-size:12px;padding:5px 10px;border:1px solid var(--line2);border-radius:999px;color:var(--ink2);background:var(--surface);}
.chip b{color:var(--ink);}
/* kpi */
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:22px;}
.kpi{padding:18px 18px 16px;border-radius:14px;background:var(--surface);border:1px solid var(--line);box-shadow:var(--shadow);position:relative;overflow:hidden;}
.kpi .k{font-family:ui-monospace,Menlo,monospace;font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink2);}
.kpi .v{font-family:ui-monospace,Menlo,monospace;font-size:27px;font-weight:600;letter-spacing:-.02em;margin-top:6px;}
.kpi .s{font-size:12px;color:var(--ink2);margin-top:3px;}
.kpi .rail{position:absolute;left:0;top:0;bottom:0;width:4px;}
/* layout helpers */
.row{display:grid;gap:18px;}
.row.c2{grid-template-columns:1.1fr .9fr;}
.row.c2b{grid-template-columns:.9fr 1.1fr;}
.pad{padding:20px 22px;}
.legend{display:flex;flex-wrap:wrap;gap:12px 18px;margin-top:6px;}
.lg{display:flex;align-items:center;gap:7px;font-size:12.5px;color:var(--ink2);}
.lg .sw{width:11px;height:11px;border-radius:3px;flex:none;}
.toggle{display:inline-flex;background:var(--surface2);border:1px solid var(--line);border-radius:10px;padding:3px;gap:2px;}
.toggle button{font:inherit;font-size:12.5px;border:0;background:transparent;color:var(--ink2);padding:6px 11px;border-radius:7px;cursor:pointer;font-family:ui-monospace,Menlo,monospace;letter-spacing:.02em;}
.toggle button.on{background:var(--accent);color:#fff;}
/* bars */
.bars{display:flex;flex-direction:column;gap:12px;margin-top:4px;}
.bar .top{display:flex;justify-content:space-between;font-size:13px;margin-bottom:5px;}
.bar .top .nm{font-weight:600;}
.bar .top .vl{font-family:ui-monospace,Menlo,monospace;color:var(--ink2);}
.track{height:12px;background:var(--surface2);border-radius:6px;overflow:hidden;}
.fill{height:100%;border-radius:6px;transition:width .5s cubic-bezier(.2,.7,.2,1);}
/* stacked master bar */
.stack{display:flex;height:44px;border-radius:10px;overflow:visible;border:1px solid var(--line);}
.stack .seg:first-child{border-radius:9px 0 0 9px;}
.stack .seg:last-child{border-radius:0 9px 9px 0;}
.stack .seg{position:relative;transition:flex-grow .5s,transform .2s cubic-bezier(.2,.7,.2,1),filter .2s;min-width:2px;cursor:pointer;transform-origin:center;}
.stackrows{margin-top:16px;display:grid;grid-template-columns:1fr 1fr;gap:8px 26px;}
.srow{display:flex;align-items:center;gap:9px;font-size:12.5px;padding:3px 0;border-bottom:1px dashed var(--line);}
.srow .sw{width:10px;height:10px;border-radius:3px;flex:none;}
.srow .nm{flex:1;color:var(--ink);}
.srow .vl{font-family:ui-monospace,Menlo,monospace;color:var(--ink2);}
/* time ladder */
.ladder{display:flex;align-items:stretch;gap:6px;flex-wrap:wrap;}
.lstep{flex:1;min-width:190px;background:var(--surface2);border:1px solid var(--line);border-radius:11px;padding:14px 16px;}
.lstep .lk{font-family:ui-monospace,Menlo,monospace;font-size:11px;letter-spacing:.05em;color:var(--ink2);}
.lstep .lv{font-size:26px;font-weight:600;letter-spacing:-.02em;margin:4px 0 4px;}
.lstep .ls{font-size:12px;color:var(--ink2);line-height:1.35;}
.larrow{display:flex;align-items:center;font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--ink2);white-space:nowrap;padding:0 2px;}
@media(max-width:820px){.larrow{width:100%;justify-content:center;padding:2px 0;}}
/* actor detail cards */
.pcards{display:grid;grid-template-columns:repeat(2,1fr);gap:18px;}
.pcard{padding:0;overflow:hidden;}
.pcard .head{padding:16px 20px;display:flex;align-items:center;gap:12px;border-bottom:1px solid var(--line);}
.pcard .dot{width:13px;height:13px;border-radius:4px;flex:none;}
.pcard .head .nm{font-weight:700;font-size:15px;}
.pcard .head .pct{margin-left:auto;font-family:ui-monospace,Menlo,monospace;font-size:13px;color:var(--ink2);}
.pcard .body{padding:16px 20px 20px;display:flex;gap:18px;align-items:center;}
.tbl{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:4px;}
.tbl th{text-align:left;font-family:ui-monospace,Menlo,monospace;font-weight:500;color:var(--ink2);font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;padding:6px 8px;border-bottom:1px solid var(--line);}
.tbl td{padding:7px 8px;border-bottom:1px solid var(--line);}
.tbl td.n{font-family:ui-monospace,Menlo,monospace;text-align:right;font-variant-numeric:tabular-nums;color:var(--ink);}
.tbl tr:last-child td{border-bottom:0;}
.tbl .ss{display:flex;align-items:center;gap:8px;}
.tbl .ss .sw{width:8px;height:8px;border-radius:2px;flex:none;}
/* timeline */
.tl{display:flex;flex-direction:column;gap:10px;}
details.blk{background:var(--surface);border:1px solid var(--line);border-radius:12px;overflow:hidden;box-shadow:var(--shadow);}
details.blk>summary{list-style:none;cursor:pointer;padding:15px 18px;display:grid;grid-template-columns:auto 1fr auto;gap:14px;align-items:center;}
details.blk>summary::-webkit-details-marker{display:none;}
.blk .time{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;color:var(--ink);white-space:nowrap;}
.blk .time .dur{color:var(--ink2);font-size:11px;display:block;}
.blk .ttl{font-weight:600;font-size:14px;}
.blk .ttl .d{font-weight:400;color:var(--ink2);font-size:12.5px;margin-top:2px;display:block;}
.blk .meta{display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end;}
.tag{font-family:ui-monospace,Menlo,monospace;font-size:11px;padding:3px 8px;border-radius:6px;background:var(--surface2);color:var(--ink2);border:1px solid var(--line);white-space:nowrap;}
.tag.big{background:var(--accent-soft);color:var(--accent);border-color:transparent;}
.blk .caret{width:16px;height:16px;color:var(--ink2);transition:transform .2s;}
details[open] .caret{transform:rotate(90deg);}
.blk .inner{padding:0 18px 18px;border-top:1px solid var(--line);}
.blk .inner h4{font-family:ui-monospace,Menlo,monospace;font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink2);margin:16px 0 8px;}
.spawnwrap{display:flex;flex-wrap:wrap;gap:7px;}
.uev{font-size:13px;padding:8px 11px;background:var(--surface2);border-radius:8px;border-left:3px solid var(--accent);margin-bottom:6px;}
.uev .ut{font-family:ui-monospace,Menlo,monospace;color:var(--accent);font-size:11px;margin-right:8px;}
.evline{font-size:12.5px;color:var(--ink2);padding:3px 0;display:flex;gap:10px;}
.evline .et{font-family:ui-monospace,Menlo,monospace;color:var(--ink);font-size:11px;}
.foot{margin-top:60px;padding-top:20px;border-top:1px solid var(--line);color:var(--ink2);font-size:12.5px;}
/* ---- hover pop ---- */
@media (prefers-reduced-motion:no-preference){
  .kpi,.pcard,.lstep,.chip,.lg,.bar,.srow,details.blk,.tag,.uev{
    transition:transform .22s cubic-bezier(.2,.7,.2,1),box-shadow .22s,border-color .22s,background .22s;
  }
}
.hoverpop{transform-origin:center;}
.kpi:hover,.pcard:hover,.lstep:hover,.chip:hover,.lg:hover,.bar:hover,.srow:hover,details.blk:not([open]):hover{
  transform:scale(1.15);position:relative;z-index:30;
  box-shadow:0 18px 46px rgba(20,29,43,.20);
}
:root[data-theme="dark"] .kpi:hover,:root[data-theme="dark"] .pcard:hover,
:root[data-theme="dark"] .lstep:hover,:root[data-theme="dark"] details.blk:hover{
  box-shadow:0 18px 46px rgba(0,0,0,.5);
}
.kpi:hover,.pcard:hover,.lstep:hover,details.blk:hover{border-color:var(--line2);}
.tag:hover{background:var(--accent-soft);color:var(--accent);border-color:transparent;}
.uev:hover{background:var(--surface);box-shadow:0 6px 18px rgba(20,29,43,.10);}
@media (prefers-reduced-motion:reduce){
  .kpi:hover,.pcard:hover,.lstep:hover,.chip:hover,.lg:hover,.bar:hover,.srow:hover,details.blk:hover{
    transform:none;box-shadow:0 8px 24px rgba(20,29,43,.14);
  }
}
/* ---- interactive charts - corrected slice scaling & explosion ---- */
.donut{overflow:visible;}
.donut .slice{
  cursor:pointer;
  transform-box:fill-box;
  transform-origin:center;
  transition:transform .24s cubic-bezier(.2,.7,.2,1),opacity .2s,stroke-width .2s,filter .2s;
}
.donut .dc-main,.donut .dc-sub{transition:fill .2s;pointer-events:none;}
.lg{cursor:pointer;border-radius:8px;padding:2px 5px;margin:-2px -1px;}
.lg.hot{background:var(--surface2);}
.lg.hot .sw{box-shadow:0 0 0 3px color-mix(in srgb,currentColor 30%,transparent);}
.stack .seg:hover{transform:scaleY(1.18);filter:brightness(1.1) saturate(1.08);z-index:6;box-shadow:0 6px 16px rgba(20,29,43,.28);}
@media (prefers-reduced-motion:reduce){.donut .slice{transition:opacity .2s,stroke-width .2s;}.stack .seg:hover{transform:none;}}
svg{display:block;max-width:100%;}
.donutwrap{display:flex;gap:22px;align-items:center;flex-wrap:wrap;}
@media(max-width:820px){
  .kpis{grid-template-columns:repeat(2,1fr);}
  .row.c2,.row.c2b{grid-template-columns:1fr;}
  .pcards{grid-template-columns:1fr;}
  .stackrows{grid-template-columns:1fr;}
  .blk summary{grid-template-columns:auto 1fr;}
  .blk .meta{grid-column:1/-1;justify-content:flex-start;}
  .kpi:hover,.pcard:hover,.lstep:hover,.bar:hover,.srow:hover,details.blk:not([open]):hover{transform:scale(1.06);}
  .chip:hover,.lg:hover{transform:scale(1.1);}
}
/* ---- floating language switcher (top-left) ---- */
#langui{position:fixed;top:16px;left:16px;z-index:9999;display:flex;gap:3px;
  background:var(--surface);border:1px solid var(--line2);border-radius:999px;
  box-shadow:var(--shadow);padding:4px;
  font-family:ui-monospace,Menlo,monospace;}
#langui .langbtn{border:0;background:transparent;color:var(--ink2);font:inherit;font-size:12px;
  font-weight:600;padding:6px 11px;border-radius:999px;cursor:pointer;line-height:1;
  transition:background .15s,color .15s,transform .15s cubic-bezier(.2,.7,.2,1);}
#langui .langbtn:hover{color:var(--ink);transform:scale(1.06);}
#langui .langbtn.on{background:var(--accent);color:#fff;}
/* ---- floating search ---- */
#searchui{position:fixed;top:16px;right:16px;z-index:9999;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;}
.sui-fab{width:46px;height:46px;border-radius:50%;border:1px solid var(--line2);
  background:var(--surface);color:var(--ink);box-shadow:var(--shadow);cursor:pointer;
  display:flex;align-items:center;justify-content:center;
  transition:transform .2s cubic-bezier(.2,.7,.2,1),background .2s,color .2s;}
.sui-fab:hover{transform:scale(1.1);background:var(--accent);color:#fff;border-color:transparent;}
.sui-fab svg{width:21px;height:21px;}
.sui-panel{position:absolute;top:0;right:0;display:flex;align-items:center;gap:6px;
  background:var(--surface);border:1px solid var(--line2);border-radius:26px;
  box-shadow:var(--shadow);padding:6px 7px 6px 16px;animation:suiIn .16s ease;}
.sui-panel[hidden]{display:none;}
@keyframes suiIn{from{opacity:0;transform:translateY(-4px) scale(.96);}to{opacity:1;transform:none;}}
.sui-mag{width:17px;height:17px;color:var(--ink2);flex:none;}
#searchInput{border:0;background:transparent;color:var(--ink);font:inherit;font-size:14px;
  outline:none;width:210px;padding:6px 2px;}
#searchInput::placeholder{color:var(--ink2);}
#searchInput.sui-err{color:var(--crit);}
.sui-toggle{height:26px;min-width:27px;padding:0 5px;border-radius:7px;border:1px solid transparent;
  background:transparent;color:var(--ink2);cursor:pointer;flex:none;
  font-family:ui-monospace,Menlo,monospace;font-size:12px;font-weight:600;line-height:1;
  display:flex;align-items:center;justify-content:center;
  transition:background .15s,color .15s,border-color .15s;}
.sui-toggle:hover{background:var(--surface2);color:var(--ink);}
.sui-toggle.on{background:var(--accent-soft);color:var(--accent);
  border-color:color-mix(in srgb,var(--accent) 42%,transparent);}
.sui-count{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:var(--ink2);
  min-width:48px;text-align:center;white-space:nowrap;}
.sui-round{width:33px;height:33px;border-radius:50%;border:1px solid var(--line);
  background:var(--surface2);color:var(--ink);cursor:pointer;font-size:19px;line-height:1;
  display:flex;align-items:center;justify-content:center;flex:none;
  transition:transform .15s cubic-bezier(.2,.7,.2,1),background .15s,color .15s;}
.sui-round:hover{background:var(--accent);color:#fff;border-color:transparent;transform:scale(1.12);}
.sui-round:disabled{opacity:.4;cursor:default;transform:none;background:var(--surface2);color:var(--ink2);}
mark.sui-hit{background:#ffd84d;color:#141d2b;border-radius:3px;padding:0 1px;
  box-shadow:0 0 0 1px rgba(0,0,0,.06);scroll-margin:120px;}
mark.sui-cur{background:#ff8a3d;color:#0c0f14;display:inline-block;
  transform:scale(1.2);transform-origin:center;position:relative;z-index:5;
  box-shadow:0 2px 12px rgba(255,138,61,.65);}
.agenttbl{min-width:620px;}
.agenttbl td:first-child{max-width:340px;}
.agenttbl th.sortable{cursor:pointer;user-select:none;white-space:nowrap;transition:color .15s;}
.agenttbl th.sortable:hover{color:var(--accent);}
.agenttbl th.sortable::after{content:"↕";opacity:.35;margin-left:5px;font-size:9px;}
.agenttbl th.sortable[aria-sort="ascending"]::after{content:"↑";opacity:1;color:var(--accent);}
.agenttbl th.sortable[aria-sort="descending"]::after{content:"↓";opacity:1;color:var(--accent);}
/* cache-read column toggle */
th.cr-col,td.cr-col{display:none;}
span.cr-col{display:none;}
body.crshow th.cr-col,body.crshow td.cr-col{display:table-cell;}
body.crshow span.cr-col{display:inline;}
td.cr-col{color:var(--cr);}
.crchk{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--ink2);
  cursor:pointer;font-family:ui-monospace,Menlo,monospace;user-select:none;
  padding:3px 9px;border:1px solid var(--line);border-radius:8px;background:var(--surface2);}
.crchk:hover{border-color:var(--line2);color:var(--ink);}
.crchk input{accent-color:var(--accent);cursor:pointer;margin:0;}
/* ---- token/time interactive charts ---- */
.tcwrap{padding:16px 18px 14px;position:relative;margin-top:26px;}
.tchead{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:10px;}
.tchead b{font-size:14px;}
.tcsub{color:var(--ink2);font-size:12px;margin-left:10px;}
.tcctrls{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.tcreset{font:inherit;font-size:12px;font-family:ui-monospace,Menlo,monospace;padding:6px 11px;
  border-radius:8px;border:1px solid var(--line);background:var(--surface2);color:var(--ink2);cursor:pointer;
  transition:border-color .15s,color .15s;}
.tcreset:hover:not(:disabled){border-color:var(--line2);color:var(--ink);}
.tcreset:disabled{opacity:.4;cursor:default;}
.tclegend{display:flex;gap:16px;margin:2px 0 8px;flex-wrap:wrap;}
.tcover,.tcdetail{border:1px solid var(--line);border-radius:9px;overflow:hidden;background:var(--surface2);}
.tcover{margin-bottom:9px;}
.tcsvg{display:block;width:100%;}
.tcbar{cursor:crosshair;}
.tcbar:hover rect{filter:brightness(1.18) saturate(1.05);}
.tctip{position:absolute;z-index:40;pointer-events:none;background:var(--surface);
  border:1px solid var(--line2);border-radius:8px;box-shadow:var(--shadow);padding:7px 10px;
  font-size:12px;color:var(--ink);font-family:ui-monospace,Menlo,monospace;white-space:nowrap;line-height:1.5;}
.tctip[hidden]{display:none;}
/* ---- interactive flow timeline (section 05) ---- */
.floww{padding:14px 16px 12px;}
.flowhead{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:10px;}
.flowhead b{font-size:14px;}
.flowsub{color:var(--ink2);font-size:12px;margin-left:10px;}
.flowbody{position:relative;user-select:none;cursor:crosshair;}
.flowtrack{position:relative;border-top:1px solid var(--line);}
.flowtrack:first-child{border-top:0;}
.flowtrack .tlabel{position:absolute;left:7px;top:4px;font-size:9px;color:var(--ink2);
  font-family:ui-monospace,Menlo,monospace;letter-spacing:.05em;text-transform:uppercase;z-index:3;
  pointer-events:none;background:color-mix(in srgb,var(--surface) 80%,transparent);padding:1px 6px 1px 2px;border-radius:4px;}
.flowsvg{display:block;width:100%;}
.flowcross{position:absolute;top:0;bottom:0;width:1px;background:var(--accent);pointer-events:none;z-index:6;opacity:.9;}
.flowcross[hidden]{display:none;}
.flowsel{position:absolute;top:0;bottom:0;z-index:2;pointer-events:none;
  background:color-mix(in srgb,var(--accent) 15%,transparent);
  border-left:1.5px solid var(--accent);border-right:1.5px solid var(--accent);}
.flowsel[hidden]{display:none;}
.flowtip{position:absolute;z-index:20;pointer-events:none;background:var(--surface);
  border:1px solid var(--line2);border-radius:9px;box-shadow:var(--shadow);padding:9px 12px;
  font-size:12px;color:var(--ink);min-width:190px;}
.flowtip[hidden]{display:none;}
.flowtip .th{font-family:ui-monospace,Menlo,monospace;font-weight:600;margin-bottom:6px;font-size:11.5px;color:var(--accent);}
.flowtip .tr{display:flex;justify-content:space-between;gap:18px;font-family:ui-monospace,Menlo,monospace;font-size:11.5px;padding:1.5px 0;}
.flowtip .tr span:last-child{color:var(--ink);}
.flowtip .tr span:first-child{color:var(--ink2);}
.flowtok{display:flex;gap:7px;flex-wrap:wrap;}
.flowtok button{font:inherit;font-size:11.5px;font-family:ui-monospace,Menlo,monospace;padding:4px 9px;
  border-radius:7px;border:1px solid var(--line);background:var(--surface2);color:var(--ink);cursor:pointer;
  display:flex;align-items:center;gap:6px;transition:opacity .15s;}
.flowtok button .sw{width:9px;height:9px;border-radius:2px;flex:none;}
.flowtok button.off{opacity:.38;}
.flowclear{font:inherit;font-size:12px;font-family:ui-monospace,Menlo,monospace;padding:5px 11px;
  border-radius:8px;border:1px solid var(--line);background:var(--surface2);color:var(--ink2);cursor:pointer;}
.flowclear[hidden]{display:none;}
.flowclear:hover{border-color:var(--line2);color:var(--ink);}
</style>
<script>const DATA=__DATA__;</script>
<script>__JS__</script>
<script>__SEARCH__</script>
'''

JS = r'''
// ===== i18n: default English, options Simplified Chinese (zh) + Turkish (tr) =====
let LANG=(function(){try{return localStorage.getItem('reportLang')||'en';}catch(e){return 'en';}})();
if(['en','tr','zh'].indexOf(LANG)<0)LANG='en';
const MONTHS={
  en:['','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],
  tr:['','Oca','Şub','Mar','Nis','May','Haz','Tem','Ağu','Eyl','Eki','Kas','Ara'],
  zh:['','1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月']
};
const I18N={
 en:{
  total:'total', m_total:'Processed (total)', m_out:'Generated (output)', m_newin:'New input', m_cr:'Cache-read',
  gran_day:'Day', gran_hour:'Hour', gran_15m:'15 min', gran_5m:'5 min',
  tc_allspan:'full span', tc_title:'Token spend · time axis', tc_sub:'drag the chart above → select a range · ',
  tc_exclcache:'exclude cache-read', tc_all:'all',
  tc_leg_in:'input (in+cache-create)', tc_leg_out:'output', tc_leg_cr:'cache-read',
  tip_in:'input', tip_out:'output', tip_cache:'cache', tip_total:'total',
  kpi_total_k:'Processed tokens', kpi_total_s:'input+cache+output sum',
  kpi_out_k:'Generated (output)', kpi_out_s:'tokens the model wrote',
  kpi_newin_k:'New input', kpi_newin_s:'fresh context (excl. cache)',
  kpi_cr_k:'Cache-read', kpi_cr_s:p=>pctf(p)+' — reused context',
  kpi_sub_k:'Sub-agents', kpi_sub_s:'specialist agents spawned',
  kpi_active_k:'Active wall-clock', kpi_active_s:w=>w+' of wall-clock minus idle',
  kpi_main_k:'Main-thread messages', kpi_main_s:'orchestrator assistant turns',
  kpi_types_k:'Agent types', kpi_types_s:'distinct sub-agent kinds used',
  eyebrow:'Claude Code · Session Telemetry',
  h1:n=>'Anatomy of a session:<br>tokens, time and '+n+' agents',
  hsub:'A full breakdown of one Claude Code session: what the main thread spent, what each sub-agent spent, when everything ran, and how the work clustered into blocks. Read straight from the transcript — no estimates.',
  chip_session:'session', chip_wall:'wall-clock', chip_active:'active',
  sec01_h:'Token consumption — by actor', sec01_note:'pick a metric ↓ pie + bar update together',
  sec01_foot:'Note: <b>processed total</b> is mostly <span class="mono">cache-read</span> (long context is re-read each turn; cheap and expected). For the real "production load", switch to the <b>Generated</b> and <b>New input</b> metrics — that is where the actual cost is.',
  sec02_h:'Time consumption — three different measures', sec02_note:'not mixing these three is critical ↓',
  ladder1_k:'① Wall-clock', ladder1_s:'wall time from first command to last message (incl. idle)',
  ladder_idle:x=>'−'+x+' idle →',
  ladder2_k:'② Active wall-clock', ladder2_s:'wall time actually worked; parallel agents count as <b>one</b>',
  ladder_par:'parallel expands →',
  ladder3_k:'③ Agent work-hours', ladder3_s:"subagent durations summed (incl. parallel) + main-thread orchestration span",
  sec02_why:(cnt,type,active,busy,top)=>'<b>Why is ③ > ②?</b> Agents run in parallel (e.g. '+cnt+' '+type+' together) while the main thread drives throughout. In active wall-clock all this overlapping work counts as <b>a single time slice</b> ('+active+'); in work-hours each sub-agent duration is summed <b>separately</b> and the main-thread active span is added on top, so the total rises to '+busy+'. The breakdown below is on the <b>③ agent work-hours</b> axis — so '+top+' is a slice of the '+busy+' work-hours, not of the '+active+' active wall-clock.',
  busy_per_phase:'Agent work-hours per actor (③)', total_lbl:x=>'total '+x,
  sec02_donut_note:(cnt,type)=>'The busiest actor sets the shape here — '+cnt+' '+type+' alone. The main thread (grey) is its active driving span: it runs concurrently with the sub-agents (overlapping time, not extra wall-clock), so that bar equals the ② active wall-clock above.',
  wallbreak:'Wall-clock breakdown (①→②)',
  active_pct:(x,p)=>'Active '+x+' ('+pctf(p)+')', idle_pct:(x,p)=>'Idle / user away '+x+' ('+pctf(p)+')',
  sec03_h:'The whole run, split into sub-steps', sec03_note:n=>n+' sub-steps · color = actor (main thread grey)',
  sec04_h:'Actor details — sub-step breakdown', sec04_note:'each card: mini donut + input/output/time table',
  cr_col_lbl:'cache-read column', cr_col_title:'Show/hide the cache-read column',
  th_substep:'Sub-step', th_n:'n', th_cache:'cache', th_out:'out', th_dur:'time',
  sec05_h:'Hourly flow — what happened when', sec05_note:'interactive timeline + expandable blocks',
  sec06_h:'Agent / task details — one by one',
  sec06_note:n=>n+" tasks · grouped by type · expand → each task's time, duration, tokens · search finds them all and opens the group",
  foot_method:(session,mainN,subN,pct)=>'<b>Methodology.</b> The numbers were extracted from the raw transcript of session <span class="mono">'+session+'</span> (main thread '+mainN+' assistant turns + '+subN+' sub-agents) by summing the <span class="mono">usage</span> field of every message. Grouping: one group per sub-agent type; the main thread is its own group, never folded into another. "Processed total" = input + cache-creation + cache-read + output. Time: agent work-hours = sum of sub-agent durations (incl. parallel) + the main-thread active span. Cache-read is '+pct+'% of all processed tokens and is low-cost.',
  sum_workhours:x=>'Σ '+x+' work-hours',
  ag_sub:(n,total,cr,out)=>n+' tasks · '+total+' tokens processed · <span class="cr-col">'+cr+' cache-read · </span>'+out+' generated',
  ag_agents:n=>n+' agents', ag_tok:x=>x+' tok',
  th_task:'Task / description', th_model:'model', th_range:'start–end · UTC', th_newin:'new-input', th_cr:'cache-read', th_outp:'output', th_turns:'turns',
  flow_title:'Timeline · parallel agent flow', flow_sub:'hover → aligned line · drag → select a range &amp; summary', flow_clear:'clear selection',
  flow_lanes:'agents · parallel lanes', flow_count:'concurrent agent count', flow_tok:'tokens / time', flow_phase:'actors',
  flow_ov:n=>'+'+n+" more agents ran at once (didn't fit in lanes)",
  flow_lane_lbl_ov:(cap,peak)=>cap+' lanes · peak '+peak+' concurrent', flow_lane_lbl:n=>n+' lanes', flow_peak:x=>'peak '+x,
  flow_dur:'duration', flow_active_now:'active then', flow_started:'started', flow_cr:'cache-read',
  blk_min:x=>x+' min', blk_imgs:n=>n+' images', blk_cmds:'Commands', blk_spawned:'Spawned agents', blk_firstev:'First events',
  srch_open:'Search (Ctrl/Cmd+F)', srch_open_aria:'Search', srch_ph:'Search…  (regex supported)',
  srch_case:'Case sensitive', srch_regex:'Use regex', srch_prev:'Previous (Shift+Enter)', srch_next:'Next (Enter)', srch_close:'Close (Esc)',
  grp_main:'Main thread (orchestrator)'
 },
 tr:{
  total:'toplam', m_total:'İşlenen (toplam)', m_out:'Üretilen (output)', m_newin:'Yeni girdi', m_cr:'Cache-read',
  gran_day:'Gün', gran_hour:'Saat', gran_15m:'15 dk', gran_5m:'5 dk',
  tc_allspan:'tüm süre', tc_title:'Token harcaması · zaman ekseni', tc_sub:'üstteki grafiği sürükle → aralık seç · ',
  tc_exclcache:'cache-read hariç', tc_all:'tümü',
  tc_leg_in:'girdi (in+cache-create)', tc_leg_out:'çıktı (output)', tc_leg_cr:'cache-read',
  tip_in:'girdi', tip_out:'çıktı', tip_cache:'cache', tip_total:'toplam',
  kpi_total_k:'İşlenen token', kpi_total_s:'girdi+cache+çıktı toplamı',
  kpi_out_k:'Üretilen (output)', kpi_out_s:'modelin yazdığı token',
  kpi_newin_k:'Yeni girdi', kpi_newin_s:'taze bağlam (cache hariç)',
  kpi_cr_k:'Cache-read', kpi_cr_s:p=>pctf(p)+' — tekrar kullanılan bağlam',
  kpi_sub_k:'Alt-agent', kpi_sub_s:'spawn edilen uzman agent',
  kpi_active_k:'Aktif takvim', kpi_active_s:w=>w+' takvimin idle hariç kısmı',
  kpi_main_k:'Ana thread mesajı', kpi_main_s:'orkestratör asistan turu',
  kpi_types_k:'Agent tipi', kpi_types_s:'kullanılan farklı alt-agent türü',
  eyebrow:'Claude Code · Oturum Telemetrisi',
  h1:n=>'Bir oturumun anatomisi:<br>token, zaman ve '+n+' agent',
  hsub:'Tek bir Claude Code oturumunun tam dökümü: ana thread ne harcadı, her alt-agent ne harcadı, hepsi ne zaman koştu ve iş hangi bloklarda toplandı. Doğrudan transcript\'ten okundu — tahmin yok.',
  chip_session:'oturum', chip_wall:'takvim', chip_active:'aktif',
  sec01_h:'Token tüketimi — aktöre göre', sec01_note:'metrik seç ↓ pie + bar birlikte güncellenir',
  sec01_foot:'Not: <b>işlenen toplam</b> ağırlıklı olarak <span class="mono">cache-read</span>\'ten oluşur (uzun bağlam her turda tekrar okunur; ucuz ve beklenen). Gerçek "üretim yükü" için <b>Üretilen</b> ve <b>Yeni girdi</b> metriklerine geçin — asıl maliyet oradadır.',
  sec02_h:'Zaman tüketimi — üç farklı ölçü', sec02_note:'bu üçünü karıştırmamak kritik ↓',
  ladder1_k:'① Takvim süresi', ladder1_s:'ilk komuttan son mesaja duvar saati (idle dahil)',
  ladder_idle:x=>'−'+x+' idle →',
  ladder2_k:'② Aktif takvim', ladder2_s:'gerçekten çalışılan duvar saati; paralel agent\'lar <b>tek</b> sayılır',
  ladder_par:'paralel açılır →',
  ladder3_k:'③ Agent iş-saati', ladder3_s:'alt-agent süreleri toplanır (paralel dahil) + ana thread orkestrasyon süresi',
  sec02_why:(cnt,type,active,busy,top)=>'<b>Neden ③ > ②?</b> Agent\'lar paralel koşar (ör. '+cnt+' '+type+' birlikte) ve ana thread baştan sona sürekli çalışır. Aktif takvimde bu örtüşen işlerin tümü <b>tek bir saat dilimi</b> sayılır ('+active+'); iş-saatinde ise her alt-agent süresi <b>ayrı ayrı</b> toplanır ve üstüne ana thread\'in aktif süresi eklenir, o yüzden toplam '+busy+'\'e çıkar. Aşağıdaki kırılım <b>③ agent iş-saati</b> eksenindedir — yani '+top+', '+busy+' iş-saatinin dilimidir, '+active+' aktif takvimin değil.',
  busy_per_phase:'Aktör başına agent iş-saati (③)', total_lbl:x=>'toplam '+x,
  sec02_donut_note:(cnt,type)=>'Şekli en yoğun aktör belirler — tek başına '+cnt+' '+type+'. Ana thread (gri) kendi aktif çalışma süresidir; alt-agent\'larla eşzamanlı koşar (örtüşen süre, ek duvar-saati değil), bu yüzden o bar yukarıdaki ② aktif takvime eşittir.',
  wallbreak:'Takvim süresi kırılımı (①→②)',
  active_pct:(x,p)=>'Aktif '+x+' ('+pctf(p)+')', idle_pct:(x,p)=>'Idle / kullanıcı uzakta '+x+' ('+pctf(p)+')',
  sec03_h:'Tüm süreç, alt-adımlara bölünmüş', sec03_note:n=>n+' alt-adım · renk = aktör (ana thread gri)',
  sec04_h:'Aktör detayları — alt-adım kırılımı', sec04_note:'her kart: mini donut + input/output/zaman tablosu',
  cr_col_lbl:'cache-read sütunu', cr_col_title:'Cache-read sütununu göster/gizle',
  th_substep:'Alt-adım', th_n:'n', th_cache:'cache', th_out:'out', th_dur:'süre',
  sec05_h:'Saatlik akış — ne, ne zaman oldu', sec05_note:'interaktif zaman tüneli + genişletilebilir bloklar',
  sec06_h:'Agent / task detayları — tek tek',
  sec06_note:n=>n+' task · tipe göre gruplandı · genişlet → her task\'ın saati, süresi, token\'ı · arama hepsini bulur ve grubu açar',
  foot_method:(session,mainN,subN,pct)=>'<b>Metodoloji.</b> Sayılar <span class="mono">'+session+'</span> oturumunun ham transcript\'inden (ana thread '+mainN+' asistan turu + '+subN+' alt-agent) her mesajın <span class="mono">usage</span> alanı toplanarak çıkarıldı. Gruplama: her alt-agent tipi bir grup; ana thread kendi grubudur, hiçbirine katılmaz. "İşlenen toplam" = input + cache-creation + cache-read + output. Zaman: agent iş-saati = alt-agent\'ların süreleri toplamı (paralel dahil) + ana thread\'in aktif süresi. Cache-read tüm işlenen token\'ın %'+pct+'\'idir ve düşük maliyetlidir.',
  sum_workhours:x=>'Σ '+x+' iş-saati',
  ag_sub:(n,total,cr,out)=>n+' task · '+total+' token işlendi · <span class="cr-col">'+cr+' cache-read · </span>'+out+' üretildi',
  ag_agents:n=>n+' agent', ag_tok:x=>x+' tok',
  th_task:'Task / açıklama', th_model:'model', th_range:'başlangıç–bitiş · UTC', th_newin:'yeni-girdi', th_cr:'cache-read', th_outp:'çıktı', th_turns:'tur',
  flow_title:'Zaman tüneli · paralel agent akışı', flow_sub:'imleci gezdir → hizalı çizgi · sürükle → aralık seç &amp; özet', flow_clear:'seçimi temizle',
  flow_lanes:"agent'lar · paralel şeritler", flow_count:'eşzamanlı agent sayısı', flow_tok:'token / zaman', flow_phase:'aktörler',
  flow_ov:n=>'+'+n+' agent daha aynı anda çalıştı (şeritlere sığmadı)',
  flow_lane_lbl_ov:(cap,peak)=>cap+' şerit · tepe '+peak+' eşzamanlı', flow_lane_lbl:n=>n+' şerit', flow_peak:x=>'tepe '+x,
  flow_dur:'süre', flow_active_now:'o an aktif', flow_started:'başlayan', flow_cr:'cache-read',
  blk_min:x=>x+' dk', blk_imgs:n=>n+' görsel', blk_cmds:'Komutlar', blk_spawned:"Spawn edilen agent'lar", blk_firstev:'İlk olaylar',
  srch_open:'Ara (Ctrl/Cmd+F)', srch_open_aria:'Ara', srch_ph:'Ara…  (regex destekli)',
  srch_case:'Büyük/küçük harf duyarlı', srch_regex:'Regex kullan', srch_prev:'Önceki (Shift+Enter)', srch_next:'Sonraki (Enter)', srch_close:'Kapat (Esc)',
  grp_main:'Ana thread (orkestratör)'
 },
 zh:{
  total:'总计', m_total:'已处理（总计）', m_out:'已生成（输出）', m_newin:'新输入', m_cr:'缓存读取',
  gran_day:'天', gran_hour:'小时', gran_15m:'15 分', gran_5m:'5 分',
  tc_allspan:'全部时段', tc_title:'Token 消耗 · 时间轴', tc_sub:'拖动上方图表 → 选择范围 · ',
  tc_exclcache:'排除缓存读取', tc_all:'全部',
  tc_leg_in:'输入（in+缓存创建）', tc_leg_out:'输出', tc_leg_cr:'缓存读取',
  tip_in:'输入', tip_out:'输出', tip_cache:'缓存', tip_total:'总计',
  kpi_total_k:'已处理 token', kpi_total_s:'输入+缓存+输出总和',
  kpi_out_k:'已生成（输出）', kpi_out_s:'模型写出的 token',
  kpi_newin_k:'新输入', kpi_newin_s:'新鲜上下文（不含缓存）',
  kpi_cr_k:'缓存读取', kpi_cr_s:p=>pctf(p)+' — 复用的上下文',
  kpi_sub_k:'子 agent', kpi_sub_s:'派生的专家 agent',
  kpi_active_k:'活跃墙钟', kpi_active_s:w=>w+' 中除去空闲的部分',
  kpi_main_k:'主线程消息', kpi_main_s:'编排器助手轮次',
  kpi_types_k:'Agent 类型', kpi_types_s:'使用到的不同子 agent 种类',
  kpi_scr_k:'已生成屏幕', kpi_scr_s:(v,sh,df)=>v+' 个已在设备上验证 · '+sh+' 个 S2 空壳（不算迁移）'+(df?' · '+df+' 个按计划推迟':''),
  eyebrow:'Claude Code · 会话遥测',
  h1:n=>'一次迁移的解剖：<br>token、时间与 '+n+' 个 agent',
  hsub:'一次 Claude Code 会话的完整分解：主线程花了多少、每个子 agent 花了多少、各自何时运行，以及工作如何聚成区块。直接读自 transcript —— 没有估算。',
  chip_session:'会话', chip_wall:'墙钟', chip_active:'活跃',
  sec01_h:'Token 消耗 — 按参与者', sec01_note:'选择指标 ↓ 饼图 + 条形图一起更新',
  sec01_foot:'注意：<b>已处理总计</b>主要由 <span class="mono">cache-read</span> 构成（长上下文每轮都会重新读取；便宜且符合预期）。要看真正的"生产负载"，请切换到<b>已生成</b>和<b>新输入</b>指标 — 真正的成本在那里。',
  sec02_h:'时间消耗 — 三种不同度量', sec02_note:'不要混淆这三者很关键 ↓',
  ladder1_k:'① 墙钟时间', ladder1_s:'从首个命令到最后一条消息的墙钟时间（含空闲）',
  ladder_idle:x=>'−'+x+' 空闲 →',
  ladder2_k:'② 活跃墙钟', ladder2_s:'实际工作的墙钟时间；并行 agent 计为<b>一个</b>',
  ladder_par:'并行展开 →',
  ladder3_k:'③ Agent 工时', ladder3_s:'子 agent 时长之和（含并行）+ 主线程编排时段',
  sec02_why:(cnt,type,active,busy,top)=>'<b>为什么 ③ > ②？</b> agent 会并行运行（例如 '+cnt+' 个 '+type+' 一起），同时主线程全程驱动。在活跃墙钟中，这些重叠的工作算作<b>单个时间片</b>（'+active+'）；而在工时中，每个子 agent 的时长<b>分别</b>相加，再加上主线程的活跃时段，因此总数上升到 '+busy+'。下面的分解基于<b>③ agent 工时</b>轴 —— 因此 '+top+' 是 '+busy+' 工时的一部分，而不是 '+active+' 活跃墙钟的一部分。',
  busy_per_phase:'每个参与者的 agent 工时（③）', total_lbl:x=>'总计 '+x,
  sec02_donut_note:(cnt,type)=>'形状由最繁忙的参与者决定 —— 仅 '+cnt+' 个 '+type+'。主线程（灰色）是它自己的活跃驱动时段：与子 agent 并发运行（时间重叠，并非额外的墙钟时间），因此该条带等于上方的 ② 活跃墙钟。',
  wallbreak:'墙钟时间分解（①→②）',
  active_pct:(x,p)=>'活跃 '+x+'（'+pctf(p)+'）', idle_pct:(x,p)=>'空闲 / 用户离开 '+x+'（'+pctf(p)+'）',
  sec03_h:'整个运行，按子步骤拆分', sec03_note:n=>n+' 个子步骤 · 颜色 = 参与者（主线程为灰色）',
  sec04_h:'参与者详情 — 子步骤分解', sec04_note:'每张卡片：迷你环形图 + 输入/输出/时间表',
  cr_col_lbl:'缓存读取列', cr_col_title:'显示/隐藏缓存读取列',
  th_substep:'子步骤', th_n:'n', th_cache:'缓存', th_out:'输出', th_dur:'时长',
  sec05_h:'每小时流程 — 何时发生了什么', sec05_note:'交互式时间线 + 可展开区块',
  sec06_h:'Agent / 任务详情 — 逐个',
  sec06_note:n=>n+' 个任务 · 按类型分组 · 展开 → 每个任务的时间、时长、token · 搜索会找到全部并展开分组',
  foot_method:(session,mainN,subN,pct)=>'<b>方法论。</b> 这些数字提取自会话 <span class="mono">'+session+'</span> 的原始 transcript（主线程 '+mainN+' 个助手轮次 + '+subN+' 个子 agent），对每条消息的 <span class="mono">usage</span> 字段求和得出。分组：每种子 agent 类型一组；主线程自成一组，从不并入其它组。"已处理总计" = 输入 + 缓存创建 + 缓存读取 + 输出。时间：agent 工时 = 子 agent 时长之和（含并行）+ 主线程的活跃时段。缓存读取占所有已处理 token 的 '+pct+'%，成本很低。',
  sum_workhours:x=>'Σ '+x+' 工时',
  ag_sub:(n,total,cr,out)=>n+' 个任务 · 处理 '+total+' token · <span class="cr-col">'+cr+' 缓存读取 · </span>生成 '+out,
  ag_agents:n=>n+' 个 agent', ag_tok:x=>x+' tok',
  th_task:'任务 / 描述', th_model:'模型', th_range:'开始–结束 · UTC', th_newin:'新输入', th_cr:'缓存读取', th_outp:'输出', th_turns:'轮次',
  flow_title:'时间线 · 并行 agent 流', flow_sub:'悬停 → 对齐线 · 拖动 → 选择范围和摘要', flow_clear:'清除选择',
  flow_lanes:'agent · 并行泳道', flow_count:'并发 agent 数', flow_tok:'token / 时间', flow_phase:'参与者',
  flow_ov:n=>'另有 +'+n+' 个 agent 同时运行（泳道容纳不下）',
  flow_lane_lbl_ov:(cap,peak)=>cap+' 条泳道 · 峰值 '+peak+' 并发', flow_lane_lbl:n=>n+' 条泳道', flow_peak:x=>'峰值 '+x,
  flow_dur:'时长', flow_active_now:'当时活跃', flow_started:'开始', flow_cr:'缓存读取',
  blk_min:x=>x+' 分', blk_imgs:n=>n+' 张图片', blk_cmds:'命令', blk_spawned:'派生的 agent', blk_firstev:'首批事件',
  srch_open:'搜索 (Ctrl/Cmd+F)', srch_open_aria:'搜索', srch_ph:'搜索…（支持正则）',
  srch_case:'区分大小写', srch_regex:'使用正则', srch_prev:'上一个 (Shift+Enter)', srch_next:'下一个 (Enter)', srch_close:'关闭 (Esc)',
  grp_main:'主线程（编排器）'
 }
};
function T(k){const d=I18N[LANG]||I18N.en;return (k in d)?d[k]:((k in I18N.en)?I18N.en[k]:k);}
function t(k){const v=T(k);if(typeof v==='function'){return v.apply(null,Array.prototype.slice.call(arguments,1));}return v;}
function pctf(n){return LANG==='tr'?('%'+n):(n+'%');}
const fmt=n=>n.toLocaleString('en-US');
const kfmt=n=>{n=Math.round(n);if(n>=1e9)return (n/1e9).toFixed(2)+'B';if(n>=1e6)return (n/1e6).toFixed(1)+'M';if(n>=1e3)return (n/1e3).toFixed(1)+'K';return ''+n;};
const hrs=s=>s>=3600?(s/3600).toFixed(1)+'h':(s/60).toFixed(0)+'m';
const dfmt=s=>{s=Math.round(s);if(s<60)return s+'s';if(s<3600)return (s/60).toFixed(1)+'m';return (s/3600).toFixed(2)+'h';};
const cvar=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
// A session's actors are whatever agent types it happened to spawn, so both the colour and
// the label come from the data: a fixed palette walked by group order, main thread always grey.
const PALETTE=['--p2','--p4','--p3','--p1','--p5','--in','--out','--cc'];
function gcol(id){
  if(id==='MAIN') return cvar('--cr');
  const i=(DATA.groups||[]).filter(g=>g.id!=='MAIN').findIndex(g=>g.id===id);
  return cvar(PALETTE[(i<0?0:i)%PALETTE.length]);
}
function PN(id){
  if(id==='MAIN') return t('grp_main');
  const g=(DATA.groups||[]).find(x=>x.id===id);
  return g?g.name:id;
}
const esc=s=>(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function dtp(s){return new Date(s);}
function hm(s){const d=dtp(s);return String(d.getUTCHours()).padStart(2,'0')+':'+String(d.getUTCMinutes()).padStart(2,'0');}
function dayLabel(d){const mo=d.getUTCMonth()+1,day=d.getUTCDate();return LANG==='zh'?(mo+'月'+day+'日'):((MONTHS[LANG]||MONTHS.en)[mo]+' '+day);}
function md(s){return dayLabel(dtp(s));}

// -------- donut --------
const aesc=s=>(s||'').replace(/["&<>]/g,c=>({'"':'&quot;','&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function donut(items,{size=180,thick=26,center=''}={}){
  const tot=items.reduce((a,b)=>a+b.v,0)||1;const r=(size-thick)/2,cx=size/2,cy=size/2;let a=-Math.PI/2;
  let segs='';
  const off=size*0.04;
  items.forEach((it,idx)=>{const frac=it.v/tot;if(frac<=0){return;}const a2=a+frac*2*Math.PI;
    const large=frac>0.5?1:0;const pct=(frac*100);const mid=(a+a2)/2;
    const dx=(Math.cos(mid)*off).toFixed(2),dy=(Math.sin(mid)*off).toFixed(2);
    // a single 100% slice is a full circle — an SVG arc whose start==end is degenerate and
    // renders nothing, so draw a <circle> ring instead (fixes single-substep donuts).
    if(frac>=0.9999){
      segs+=`<circle class="slice" data-idx="${idx}" data-n="${aesc(it.n)}" data-v="${it.v}" data-c="${it.c}" data-pct="${pct.toFixed(1)}" data-dx="0" data-dy="0" style="--sw:${thick}" cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${it.c}" stroke-width="${thick}"><title>${esc(it.n)}: ${fmt(it.v)} (%${pct.toFixed(1)})</title></circle>`;
      a=a2;return;
    }
    const x1=cx+r*Math.cos(a),y1=cy+r*Math.sin(a),x2=cx+r*Math.cos(a2),y2=cy+r*Math.sin(a2);
    segs+=`<path class="slice" data-idx="${idx}" data-n="${aesc(it.n)}" data-v="${it.v}" data-c="${it.c}" data-pct="${pct.toFixed(1)}" data-dx="${dx}" data-dy="${dy}" style="--sw:${thick}" d="M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}" fill="none" stroke="${it.c}" stroke-width="${thick}" stroke-linecap="butt"><title>${esc(it.n)}: ${fmt(it.v)} (%${pct.toFixed(1)})</title></path>`;
    a=a2;});
  return `<svg class="donut" data-cmain="${aesc(center)}" data-csub="${center?aesc(t('total')):''}" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" role="img">${segs}
    <text class="dc-main" x="${cx}" y="${cy-4}" text-anchor="middle" font-family="ui-monospace,Menlo,monospace" font-size="21" font-weight="600" fill="${cvar('--ink')}">${center}</text>
    <text class="dc-sub" x="${cx}" y="${cy+15}" text-anchor="middle" font-family="ui-monospace,Menlo,monospace" font-size="10" fill="${cvar('--ink2')}">${center?esc(t('total')):''}</text></svg>`;
}
function bars(items,unit){
  const mx=Math.max(...items.map(i=>i.v))||1;
  return `<div class="bars">`+items.map(i=>`<div class="bar"><div class="top"><span class="nm">${esc(i.n)}</span><span class="vl">${i.t||fmt(i.v)}</span></div><div class="track"><div class="fill" style="width:${(i.v/mx*100).toFixed(1)}%;background:${i.c}"></div></div></div>`).join('')+`</div>`;
}
function legend(items){return `<div class="legend">`+items.map((i,idx)=>`<span class="lg" data-idx="${idx}" style="color:${i.c}"><span class="sw" style="background:${i.c}"></span><span style="color:var(--ink2)">${esc(i.n)}</span></span>`).join('')+`</div>`;}

// ===== token/time interactive chart (header) =====
let tcGran='hour', tcRange=null, tcExcl=false;
const TCGRAN=[['day','Gün',86400000],['hour','Saat',3600000],['15m','15 dk',900000],['5m','5 dk',300000]];
const EV=(DATA.events||[]);
const TSPAN=EV.length?[EV[0][0],EV[EV.length-1][0]]:[0,1];
function tcNiceStep(ms){const S=[60000,300000,900000,1800000,3600000,7200000,21600000,43200000,86400000];for(const s of S)if(ms<=s)return s;return 86400000;}
function tcBucket(gms,t0,t1){
  const map=new Map();
  for(let i=0;i<EV.length;i++){const e=EV[i],ms=e[0];if(ms<t0||ms>t1)continue;
    const b=Math.floor(ms/gms)*gms;let o=map.get(b);if(!o){o={t:b,gin:0,out:0,cr:0};map.set(b,o);}
    o.gin+=e[1]+e[2];o.cr+=e[3];o.out+=e[4];}
  return [...map.values()].sort((a,b)=>a.t-b.t);
}
function tcTotal(o){return o.gin+o.out+(tcExcl?0:o.cr);}
function tcTick(ms){const d=new Date(ms);
  const day=dayLabel(d);
  const hm=String(d.getUTCHours()).padStart(2,'0')+':'+String(d.getUTCMinutes()).padStart(2,'0');
  return tcGran==='day'?day:day+' '+hm;}
function renderTimeCharts(){
  const root=document.getElementById('tcRoot');if(!root)return;
  if(!EV.length){root.style.display='none';return;}
  root.style.display='';
  const gms=(TCGRAN.find(g=>g[0]===tcGran)||TCGRAN[1])[2];
  const r0=tcRange?tcRange[0]:TSPAN[0], r1=tcRange?tcRange[1]:TSPAN[1];
  const green=cvar('--p5'),red=cvar('--out'),grey=cvar('--cr'),accent=cvar('--accent'),ink2=cvar('--ink2');
  const granBtns=TCGRAN.map(([k])=>`<button data-g="${k}" class="${k===tcGran?'on':''}">${t('gran_'+k)}</button>`).join('');
  const rangeLbl=tcRange?`${tcTick(r0)} – ${tcTick(r1)}`:t('tc_allspan');
  root.innerHTML=`
    <div class="tchead">
      <div><b>${t('tc_title')}</b><span class="tcsub">${t('tc_sub')}<span class="mono">${rangeLbl}</span></span></div>
      <div class="tcctrls">
        <div class="toggle tcgran">${granBtns}</div>
        <label class="crchk"><input type="checkbox" class="tcexcl" ${tcExcl?'checked':''}> ${t('tc_exclcache')}</label>
        <button class="tcreset" ${tcRange?'':'disabled'}>${t('tc_all')}</button>
      </div>
    </div>
    <div class="tclegend">
      <span class="lg"><span class="sw" style="background:${green}"></span>${t('tc_leg_in')}</span>
      <span class="lg"><span class="sw" style="background:${red}"></span>${t('tc_leg_out')}</span>
      ${tcExcl?'':`<span class="lg"><span class="sw" style="background:${grey}"></span>${t('tc_leg_cr')}</span>`}
    </div>
    <div class="tcover" id="tcOver"></div>
    <div class="tcdetail" id="tcDet"></div>
    <div class="tctip" hidden></div>`;
  const overEl=root.querySelector('#tcOver'), detEl=root.querySelector('#tcDet');
  // ---- overview line + brush (full span) ----
  const OW=Math.max(320,overEl.clientWidth||1000),OH=88;
  const ogms=tcNiceStep(((TSPAN[1]-TSPAN[0])/180)||gms)||gms;
  const ob=tcBucket(ogms,TSPAN[0],TSPAN[1]);
  const oMax=Math.max(1,...ob.map(tcTotal));
  const ox=t=>(t-TSPAN[0])/((TSPAN[1]-TSPAN[0])||1)*OW;
  const oy=v=>OH-4-(v/oMax)*(OH-10);
  const pts=ob.map(o=>`${ox(o.t).toFixed(1)},${oy(tcTotal(o)).toFixed(1)}`).join(' ');
  overEl.innerHTML=`<svg class="tcsvg" viewBox="0 0 ${OW} ${OH}" height="${OH}">
     <rect class="tcsel" x="${ox(r0).toFixed(1)}" y="0" width="${Math.max(0,ox(r1)-ox(r0)).toFixed(1)}" height="${OH}" fill="${accent}" opacity="0.12"/>
     <polyline points="${pts}" fill="none" stroke="${accent}" stroke-width="1.5"/>
     <rect class="tcbrush" x="0" y="0" width="${OW}" height="${OH}" fill="transparent" style="cursor:crosshair"/></svg>`;
  // ---- detail stacked bars ----
  const DW=Math.max(320,detEl.clientWidth||1000),DH=232,padB=22;
  const db=tcBucket(gms,r0,r1);
  const dMax=Math.max(1,...db.map(tcTotal));
  const dx=t=>(t-r0)/((r1-r0)||1)*DW;
  const bw=Math.max(1,Math.min(30,(DW/Math.max(1,db.length))-1));
  let bars='';
  db.forEach((o,i)=>{const x=dx(o.t);let yb=DH-padB;
    const order=tcExcl?[[o.gin,green],[o.out,red]]:[[o.cr,grey],[o.gin,green],[o.out,red]];
    let seg='';order.forEach(([v,c])=>{if(v<=0)return;const h=(v/dMax)*(DH-padB-6);yb-=h;
      seg+=`<rect x="${x.toFixed(1)}" y="${yb.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" fill="${c}"/>`;});
    bars+=`<g class="tcbar" data-i="${i}">${seg}<rect x="${x.toFixed(1)}" y="0" width="${bw.toFixed(1)}" height="${DH-padB}" fill="transparent"/></g>`;});
  let ticks='';const nt=Math.min(6,db.length||1);
  for(let k=0;k<nt;k++){const tt=r0+(r1-r0)*(nt<2?0:k/(nt-1));const xx=dx(tt);
    ticks+=`<text x="${xx.toFixed(1)}" y="${DH-6}" fill="${ink2}" font-size="10" text-anchor="${k===0?'start':k===nt-1?'end':'middle'}" font-family="ui-monospace,Menlo,monospace">${tcTick(tt)}</text>`;}
  detEl.innerHTML=`<svg class="tcsvg" viewBox="0 0 ${DW} ${DH}" height="${DH}">${bars}${ticks}</svg>`;
  // ---- wire controls ----
  root.querySelectorAll('.tcgran button').forEach(b=>b.onclick=()=>{tcGran=b.dataset.g;renderTimeCharts();});
  root.querySelector('.tcexcl').onchange=e=>{tcExcl=e.target.checked;renderTimeCharts();};
  root.querySelector('.tcreset').onclick=()=>{tcRange=null;renderTimeCharts();};
  // ---- brush ----
  const brush=overEl.querySelector('.tcbrush'), sel=overEl.querySelector('.tcsel'), svg=overEl.querySelector('svg');
  if(brush){
    const toT=cx=>{const r=svg.getBoundingClientRect();let f=(cx-r.left)/r.width;f=Math.max(0,Math.min(1,f));return TSPAN[0]+f*(TSPAN[1]-TSPAN[0]);};
    let drag=false,a0=0;
    brush.addEventListener('pointerdown',e=>{drag=true;a0=toT(e.clientX);brush.setPointerCapture(e.pointerId);});
    brush.addEventListener('pointermove',e=>{if(!drag)return;const a1=toT(e.clientX);const lo=Math.min(a0,a1),hi=Math.max(a0,a1);
      sel.setAttribute('x',ox(lo).toFixed(1));sel.setAttribute('width',Math.max(0,ox(hi)-ox(lo)).toFixed(1));});
    brush.addEventListener('pointerup',e=>{if(!drag)return;drag=false;const a1=toT(e.clientX);let lo=Math.min(a0,a1),hi=Math.max(a0,a1);
      tcRange=(hi-lo<(TSPAN[1]-TSPAN[0])*0.004)?null:[lo,hi];renderTimeCharts();});
  }
  // ---- tooltips ----
  const tip=root.querySelector('.tctip');
  root.querySelectorAll('.tcbar').forEach(g=>{const o=db[+g.dataset.i];
    g.addEventListener('mouseenter',()=>{tip.hidden=false;
      tip.innerHTML=`<b>${tcTick(o.t)}</b><br><span style="color:${green}">${t('tip_in')} ${kfmt(o.gin)}</span> · <span style="color:${red}">${t('tip_out')} ${kfmt(o.out)}</span>${tcExcl?'':` · <span style="color:${grey}">${t('tip_cache')} ${kfmt(o.cr)}</span>`}<br>${t('tip_total')} ${kfmt(tcTotal(o))}`;});
    g.addEventListener('mousemove',e=>{const rr=root.getBoundingClientRect();
      let x=e.clientX-rr.left+14,y=e.clientY-rr.top+14;x=Math.min(x,rr.width-190);tip.style.left=x+'px';tip.style.top=y+'px';});
    g.addEventListener('mouseleave',()=>{tip.hidden=true;});});
}

// metric selection for token charts
const METRICS=[['total','İşlenen (toplam)'],['out','Üretilen (output)'],['newin','Yeni girdi'],['cr','Cache-read']];
function mval(tok,m){if(m==='newin')return tok.in+tok.cc;return tok[m===''?'total':m]??tok.total;}

let curMetric='total';
let showCR=false;
function crToggleHTML(){return `<label class="crchk" title="${t('cr_col_title')}"><input type="checkbox" class="crtoggle"${showCR?' checked':''}> ${t('cr_col_lbl')}</label>`;}
function render(){
  const m=curMetric;
  const groups=DATA.groups;
  const app=document.getElementById('app');
  const meta=DATA.meta;const g=meta.grand_total;

  // KPI
  const kpi=[
    {k:t('kpi_total_k'),v:kfmt(g.total),s:t('kpi_total_s'),c:cvar('--accent')},
    {k:t('kpi_out_k'),v:kfmt(g.out),s:t('kpi_out_s'),c:cvar('--out')},
    {k:t('kpi_newin_k'),v:kfmt(g.in+g.cc),s:t('kpi_newin_s'),c:cvar('--cc')},
    {k:t('kpi_cr_k'),v:kfmt(g.cr),s:t('kpi_cr_s',(g.cr/g.total*100).toFixed(0)),c:cvar('--cr')},
    {k:t('kpi_sub_k'),v:meta.n_subagents,s:t('kpi_sub_s'),c:cvar('--p2')},
    {k:t('kpi_active_k'),v:hrs(meta.active_sec),s:t('kpi_active_s',hrs(meta.wall_sec)),c:cvar('--p1')},
    {k:t('kpi_main_k'),v:fmt(meta.n_main_assistant),s:t('kpi_main_s'),c:cvar('--p3')},
    {k:t('kpi_types_k'),v:(DATA.subagent_type_totals||[]).length,s:t('kpi_types_s'),c:cvar('--p5')},
  ];

  // tokens per actor
  const tokItems=groups.map(pp=>({n:PN(pp.id),c:gcol(pp.id),id:pp.id,v:mval(pp.tokens,m),tok:pp.tokens}));
  const totSel=tokItems.reduce((a,b)=>a+b.v,0);

  // time per actor (agent busy) — includes the main thread's active driving span
  const timeItems=groups.filter(pp=>pp.agent_busy_sec>0).map(pp=>({n:PN(pp.id),c:gcol(pp.id),v:pp.agent_busy_sec,t:hrs(pp.agent_busy_sec)}));
  const totBusy=timeItems.reduce((a,b)=>a+b.v,0);

  // substeps master stack (by total)
  const ss=DATA.substeps.slice().sort((a,b)=>b.tokens.total-a.tokens.total);
  const ssTot=ss.reduce((a,b)=>a+b.tokens.total,0);
  // most-spawned agent type (dynamic — used in the narrative below)
  const topAgent=(DATA.subagent_type_totals||[]).slice().sort((a,b)=>b.count-a.count)[0]||{type:'agent',count:0};

  app.innerHTML=`
  <div class="wrap">
    <header>
      <div class="eyebrow">${t('eyebrow')}</div>
      <h1>${t('h1',meta.n_subagents)}</h1>
      <p class="sub">${t('hsub')}</p>
      <div class="hgrid">
        <span class="chip">${t('chip_session')} <b class="mono">${meta.session}</b></span>
        <span class="chip">${md(meta.span_start)} ${hm(meta.span_start)} → ${md(meta.span_end)} ${hm(meta.span_end)} <b>UTC</b></span>
        <span class="chip">${t('chip_wall')} <b>${hrs(meta.wall_sec)}</b></span>
        <span class="chip">${t('chip_active')} <b>${hrs(meta.active_sec)}</b></span>
      </div>
      <div class="kpis">${kpi.map(x=>`<div class="kpi"><div class="rail" style="background:${x.c}"></div><div class="k">${x.k}</div><div class="v">${x.v}</div><div class="s">${x.s}</div></div>`).join('')}</div>
      <div class="card tcwrap" id="tcRoot"></div>
    </header>

    <section>
      <div class="sechead"><span class="num">01</span><h2>${t('sec01_h')}</h2>
        <span class="note">${t('sec01_note')}</span></div>
      <div style="margin-bottom:16px">${toggleHTML()}</div>
      <div class="row c2">
        <div class="card pad">
          <div class="donutwrap chartbox">
            ${donut(tokItems,{center:kfmt(totSel)})}
            <div style="flex:1;min-width:180px">${legend(tokItems.map(i=>({n:i.n+' · '+kfmt(i.v),c:i.c})))}</div>
          </div>
        </div>
        <div class="card pad">${bars(tokItems.map(i=>({n:i.n,c:i.c,v:i.v,t:fmt(i.v)})))}</div>
      </div>
      <p class="sub" style="margin-top:14px;font-size:13px">${t('sec01_foot')}</p>
    </section>

    <section>
      <div class="sechead"><span class="num">02</span><h2>${t('sec02_h')}</h2>
        <span class="note">${t('sec02_note')}</span></div>
      <div class="card pad" style="margin-bottom:18px">
        <div class="ladder">
          <div class="lstep"><div class="lk">${t('ladder1_k')}</div><div class="lv mono" style="color:var(--cr)">${hrs(meta.wall_sec)}</div><div class="ls">${t('ladder1_s')}</div></div>
          <div class="larrow">${t('ladder_idle',hrs(meta.idle_sec))}</div>
          <div class="lstep"><div class="lk">${t('ladder2_k')}</div><div class="lv mono" style="color:var(--p1)">${hrs(meta.active_sec)}</div><div class="ls">${t('ladder2_s')}</div></div>
          <div class="larrow">${t('ladder_par')}</div>
          <div class="lstep"><div class="lk">${t('ladder3_k')}</div><div class="lv mono" style="color:var(--p4)">${hrs(meta.busy_sec)}</div><div class="ls">${t('ladder3_s')}</div></div>
        </div>
        <p class="sub" style="font-size:12.5px;margin-top:16px;max-width:none">${t('sec02_why',topAgent.count,esc(topAgent.type),hrs(meta.active_sec),hrs(meta.busy_sec),(function(){const g=(DATA.groups||[]).filter(x=>x.id!=='MAIN').sort((a,b)=>b.agent_busy_sec-a.agent_busy_sec)[0];return g?esc(g.name)+' '+hrs(g.agent_busy_sec):'—';})())}</p>
      </div>
      <div class="row c2b">
        <div class="card pad">
          <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px"><b style="font-size:13px">${t('busy_per_phase')}</b><span class="mono" style="font-size:12px;color:var(--ink2)">${t('total_lbl',hrs(totBusy))}</span></div>
          ${bars(timeItems)}
        </div>
        <div class="card pad">
          <div class="donutwrap chartbox">
            ${donut(timeItems,{center:hrs(totBusy)})}
            <div style="flex:1;min-width:170px">${legend(timeItems.map(i=>({n:i.n+' · '+i.t,c:i.c})))}
            <p class="sub" style="font-size:12px;margin-top:12px">${t('sec02_donut_note',topAgent.count,esc(topAgent.type))}</p></div>
          </div>
        </div>
      </div>
      <div class="card pad" style="margin-top:18px">
        <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px"><b style="font-size:13px">${t('wallbreak')}</b><span class="mono" style="font-size:12px;color:var(--ink2)">${hrs(meta.wall_sec)}</span></div>
        <div class="stack" style="height:26px">
          <div class="seg" style="flex-grow:${meta.active_sec};background:var(--good)"></div>
          <div class="seg" style="flex-grow:${meta.idle_sec};background:var(--cr)"></div>
        </div>
        <div class="legend" style="margin-top:12px">
          <span class="lg"><span class="sw" style="background:var(--good)"></span>${t('active_pct',hrs(meta.active_sec),(meta.active_sec/meta.wall_sec*100).toFixed(0))}</span>
          <span class="lg"><span class="sw" style="background:var(--cr)"></span>${t('idle_pct',hrs(meta.idle_sec),(meta.idle_sec/meta.wall_sec*100).toFixed(0))}</span>
        </div>
      </div>
    </section>

    <section>
      <div class="sechead"><span class="num">03</span><h2>${t('sec03_h')}</h2>
        <span class="note">${t('sec03_note',ss.length)}</span></div>
      <div class="card pad">
        <div class="stack">${ss.map(s=>`<div class="seg" style="flex-grow:${s.tokens.total};background:${gcol(s.group)}" title="${esc(s.substep)} — ${fmt(s.tokens.total)}"></div>`).join('')}</div>
        <div class="stackrows">${ss.map(s=>`<div class="srow"><span class="sw" style="background:${gcol(s.group)}"></span><span class="nm">${esc(s.substep)}</span><span class="vl">${kfmt(s.tokens.total)} · %${(s.tokens.total/ssTot*100).toFixed(1)}</span></div>`).join('')}</div>
      </div>
    </section>

    <section>
      <div class="sechead"><span class="num">04</span><h2>${t('sec04_h')}</h2>
        <span class="note">${t('sec04_note')}</span>${crToggleHTML()}</div>
      <div class="pcards">${groups.map(pp=>groupCard(pp)).join('')}</div>
    </section>

    <section>
      <div class="sechead"><span class="num">05</span><h2>${t('sec05_h')}</h2>
        <span class="note">${t('sec05_note')}</span></div>
      <div class="card floww" id="flowRoot" style="margin-bottom:16px"></div>
      <div class="tl" id="flowBlocks">${DATA.timeline.filter(b=>b.n_spawn>0||b.users.length>0).map(blk).join('')}</div>
    </section>

    <section>
      <div class="sechead"><span class="num">06</span><h2>${t('sec06_h')}</h2>
        <span class="note">${t('sec06_note',DATA.subagents.length)}</span>${crToggleHTML()}</div>
      <div class="tl">${DATA.subagent_type_totals.map(t=>agentGroup(t.type)).join('')}</div>
    </section>

    <div class="foot">
      ${t('foot_method',meta.session,fmt(meta.n_main_assistant),meta.n_subagents,(g.cr/g.total*100).toFixed(0))}
    </div>
  </div>`;
  // wire toggle
  document.querySelectorAll('.toggle button').forEach(b=>b.onclick=()=>{curMetric=b.dataset.m;render();});
  wireCharts();
  wireSortTables();
  renderTimeCharts();
  renderFlow();
  // cache-read column checkboxes (kept in sync, driven by a body class)
  document.body.classList.toggle('crshow',showCR);
  document.querySelectorAll('.crtoggle').forEach(cb=>{cb.checked=showCR;cb.onchange=()=>{
    showCR=cb.checked;document.body.classList.toggle('crshow',showCR);
    document.querySelectorAll('.crtoggle').forEach(o=>{o.checked=showCR;});
  };});
}
// click a column header to sort that group's tasks; toggles desc <-> asc
function wireSortTables(){
  document.querySelectorAll('table.agenttbl').forEach(tb=>{
    if(!tb.tHead||!tb.tBodies.length) return;
    const ths=[...tb.tHead.rows[0].cells];
    ths.forEach((th,ci)=>{
      if(!th.classList.contains('sortable')) return;
      th.onclick=()=>{
        const dir=th.getAttribute('aria-sort')==='descending'?'ascending':'descending';
        ths.forEach(h=>h.removeAttribute('aria-sort'));
        th.setAttribute('aria-sort',dir);
        const body=tb.tBodies[0], rows=[...body.rows];
        const val=r=>r.cells[ci]?r.cells[ci].getAttribute('data-v')||'':'';
        const numeric=rows.length>0 && rows.every(r=>{const v=val(r);return v!==''&&!isNaN(Number(v));});
        rows.sort((a,b)=>{
          let x=val(a),y=val(b);
          if(numeric) return dir==='descending'?Number(y)-Number(x):Number(x)-Number(y);
          return dir==='descending'?String(y).localeCompare(String(x),'tr'):String(x).localeCompare(String(y),'tr');
        });
        rows.forEach(r=>body.appendChild(r));
      };
    });
  });
}
function wireCharts(){
  document.querySelectorAll('.chartbox').forEach(box=>{
    const svg=box.querySelector('svg.donut'); if(!svg)return;
    const main=svg.querySelector('.dc-main'), sub=svg.querySelector('.dc-sub');
    const dmain=svg.dataset.cmain||'', dsub=svg.dataset.csub||'';
    const slices=[...box.querySelectorAll('.slice')];
    const legs=[...box.querySelectorAll('.lg')];
    const hi=idx=>{
      slices.forEach(s=>{const on=+s.dataset.idx===idx;
        s.style.opacity=on?'1':'.28';
        s.style.transform=on?`translate(${s.dataset.dx}px, ${s.dataset.dy}px) scale(1.04)`:'';
        s.style.filter=on?'drop-shadow(0 4px 10px rgba(20,29,43,.45))':'';
      });
      legs.forEach(l=>l.classList.toggle('hot',+l.dataset.idx===idx));
      const s=slices.find(x=>+x.dataset.idx===idx);
      if(s){if(main){main.textContent=kfmt(+s.dataset.v);main.style.fill=s.dataset.c;}if(sub){sub.textContent='%'+s.dataset.pct;}}
    };
    const reset=()=>{
      slices.forEach(s=>{s.style.opacity='';s.style.transform='';s.style.filter='';});
      legs.forEach(l=>l.classList.remove('hot'));
      if(main){main.textContent=dmain;main.style.fill='';}
      if(sub){sub.textContent=dsub;}
    };
    slices.forEach(s=>{s.addEventListener('mouseenter',()=>hi(+s.dataset.idx));s.addEventListener('mouseleave',reset);});
    legs.forEach(l=>{l.addEventListener('mouseenter',()=>hi(+l.dataset.idx));l.addEventListener('mouseleave',reset);});
  });
}
function toggleHTML(){return `<div class="toggle">`+METRICS.map(([k])=>`<button data-m="${k}" class="${k===curMetric?'on':''}">${t('m_'+k)}</button>`).join('')+`</div>`;}

function groupCard(pp){
  const c=gcol(pp.id);
  const mine=DATA.substeps.filter(s=>s.group===pp.id).sort((a,b)=>b.tokens.total-a.tokens.total);
  const tot=pp.tokens.total;
  const grand=DATA.meta.grand_total.total;
  const di=mine.map((s,i)=>({n:s.substep,v:s.tokens.total,c:shade(c,i,mine.length)}));
  const rows=mine.map((s,i)=>`<tr><td><div class="ss"><span class="sw" style="background:${shade(c,i,mine.length)}"></span>${esc(s.substep)}</div></td>
     <td class="n">${s.count}</td><td class="n cr-col">${kfmt(s.tokens.cr)}</td><td class="n">${kfmt(s.tokens.out)}</td><td class="n">${kfmt(s.tokens.total)}</td><td class="n">${s.dur_sec?hrs(s.dur_sec):'—'}</td></tr>`).join('');
  return `<div class="card pcard">
    <div class="head"><span class="dot" style="background:${c}"></span><span class="nm">${esc(pp.name)}</span>
      <span class="pct">%${(tot/grand*100).toFixed(1)} · ${hrs(pp.agent_busy_sec)}</span></div>
    <div class="body">
      <div class="chartbox" style="flex:none">${donut(di,{size:132,thick:20,center:kfmt(tot)})}</div>
      <div style="flex:1;overflow-x:auto"><table class="tbl">
        <thead><tr><th>${t('th_substep')}</th><th style="text-align:right">${t('th_n')}</th><th class="cr-col" style="text-align:right">${t('th_cache')}</th><th style="text-align:right">${t('th_out')}</th><th style="text-align:right">${t('total')}</th><th style="text-align:right">${t('th_dur')}</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
    </div></div>`;
}
// shade a base color by index (mix toward light/dark)
function shade(hex,i,n){
  const c=cvar(hex.startsWith('--')?hex:('--'+hex))||hex;
  // c is like #rrggbb
  let m=c.match(/#?([0-9a-f]{6})/i);if(!m)return c;
  let r=parseInt(m[1].slice(0,2),16),g=parseInt(m[1].slice(2,4),16),b=parseInt(m[1].slice(4,6),16);
  const t=(i/Math.max(1,n))*0.55; // toward lighter
  const dark=matchMedia('(prefers-color-scheme:dark)').matches||document.documentElement.dataset.theme==='dark';
  const tgt=dark?255:0; // dark theme lightens, light theme darkens for separation
  const mix=(x)=>Math.round(x+(tgt-x)*t*(dark?0.5:0.35));
  return `rgb(${mix(r)},${mix(g)},${mix(b)})`;
}

// -------- per-task (agent) detail --------
function agentRow(s){
  const t=s.tokens, newin=t.in+t.cc;
  const c=gcol(s.group);
  const when=s.start?`${md(s.start)} ${hm(s.start)}${s.end?'–'+hm(s.end):''}`:'—';
  const model=(s.model||'').replace('claude-','').replace(/\[1m\]/,'');
  const ep=s.start?(Date.parse(s.start)||0):0;
  return `<tr>
    <td data-v="${aesc((s.desc||s.type||'').toLowerCase())}"><div class="ss"><span class="sw" style="background:${c}"></span>${esc(s.desc||s.type)}</div></td>
    <td data-v="${aesc(model)}" class="mono" style="font-size:11px;color:var(--ink2)">${esc(model)}</td>
    <td data-v="${ep}" class="mono" style="white-space:nowrap;font-size:11.5px">${when}</td>
    <td data-v="${s.dur_sec||0}" class="n">${s.dur_sec?dfmt(s.dur_sec):'—'}</td>
    <td data-v="${newin}" class="n">${kfmt(newin)}</td>
    <td data-v="${t.cr}" class="n cr-col">${kfmt(t.cr)}</td>
    <td data-v="${t.out}" class="n" style="color:var(--out)">${kfmt(t.out)}</td>
    <td data-v="${t.total}" class="n">${kfmt(t.total)}</td>
    <td data-v="${s.nmsg}" class="n">${s.nmsg}</td>
  </tr>`;
}
function agentGroup(ty){
  const list=DATA.subagents.filter(s=>s.type===ty).slice().sort((a,b)=>(a.start||'').localeCompare(b.start||''));
  if(!list.length) return '';
  const agg=list.reduce((o,s)=>{o.total+=s.tokens.total;o.out+=s.tokens.out;o.newin+=s.tokens.in+s.tokens.cc;o.cr+=s.tokens.cr;o.dur+=s.dur_sec;return o;},{total:0,out:0,newin:0,cr:0,dur:0});
  const starts=list.map(s=>s.start).filter(Boolean).sort();
  const ends=list.map(s=>s.end).filter(Boolean).sort();
  const range=starts.length?`${md(starts[0])} ${hm(starts[0])}–${ends.length?hm(ends[ends.length-1]):''}`:'—';
  const rows=list.map(agentRow).join('');
  return `<details class="blk">
    <summary>
      <div class="time">${range}<span class="dur">${t('sum_workhours',dfmt(agg.dur))}</span></div>
      <div><div class="ttl">${esc(ty)}<span class="d">${t('ag_sub',list.length,kfmt(agg.total),kfmt(agg.cr),kfmt(agg.out))}</span></div></div>
      <div class="meta"><span class="tag big">${t('ag_agents',list.length)}</span><span class="tag">${t('ag_tok',kfmt(agg.total))}</span>
        <svg class="caret" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 4l4 4-4 4"/></svg></div>
    </summary>
    <div class="inner">
      <div style="overflow-x:auto"><table class="tbl agenttbl">
        <thead><tr><th class="sortable">${t('th_task')}</th><th class="sortable">${t('th_model')}</th><th class="sortable">${t('th_range')}</th><th class="sortable" style="text-align:right">${t('th_dur')}</th><th class="sortable" style="text-align:right">${t('th_newin')}</th><th class="sortable cr-col" style="text-align:right">${t('th_cr')}</th><th class="sortable" style="text-align:right">${t('th_outp')}</th><th class="sortable" style="text-align:right">${t('total')}</th><th class="sortable" style="text-align:right">${t('th_turns')}</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
    </div>
  </details>`;
}

// ===== interactive flow timeline (section 05) =====
let flowSel=null;                       // committed [aMs,bMs] or null
const flowTok={gin:true,out:true,cr:true};
function tcAbs(ms){const d=new Date(ms);return dayLabel(d)+' '+String(d.getUTCHours()).padStart(2,'0')+':'+String(d.getUTCMinutes()).padStart(2,'0');}
function flowAgg(a,b,AG){
  let i=0,cc=0,cr=0,o=0;for(const e of EV){if(e[0]>=a&&e[0]<=b){i+=e[1];cc+=e[2];cr+=e[3];o+=e[4];}}
  return {gin:i+cc,out:o,cr:cr,total:i+cc+cr+o,
          overlap:AG.filter(x=>x.s<b&&x.e>a).length, started:AG.filter(x=>x.s>=a&&x.s<=b).length, dur:(b-a)/1000};
}
function renderFlow(){
  const root=document.getElementById('flowRoot'); if(!root) return;
  const AG=(DATA.subagents||[]).filter(s=>s.start&&s.end)
    .map(s=>({t:s.type,ph:s.group||s.type,desc:s.desc,tok:s.tokens,s:Date.parse(s.start),e:Date.parse(s.end)}))
    .filter(a=>isFinite(a.s)&&isFinite(a.e)&&a.e>=a.s).sort((a,b)=>a.s-b.s);
  if(!AG.length && !EV.length){root.style.display='none';return;}
  root.style.display='';
  const laneEnd=[];AG.forEach(a=>{let ln=-1;for(let i=0;i<laneEnd.length;i++){if(a.s>=laneEnd[i]){ln=i;break;}}if(ln<0){ln=laneEnd.length;laneEnd.push(0);}laneEnd[ln]=a.e;a.lane=ln;});
  const nLanes=Math.max(1,laneEnd.length);
  let FT0=Math.min(...(EV.length?[EV[0][0]]:[]),...(AG.length?AG.map(a=>a.s):[]));
  let FT1=Math.max(...(EV.length?[EV[EV.length-1][0]]:[]),...(AG.length?AG.map(a=>a.e):[]));
  if(!isFinite(FT0)||!isFinite(FT1)||FT1<=FT0){FT0=TSPAN[0];FT1=TSPAN[1];}
  const PW=(DATA.group_windows||[]).map(w=>({id:w.id,s:Date.parse(w.start),e:Date.parse(w.end)}));

  root.innerHTML=`
    <div class="flowhead">
      <div><b>${t('flow_title')}</b><span class="flowsub">${t('flow_sub')}</span></div>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><div class="flowtok" id="flowTok"></div><button class="flowclear" id="flowClear" hidden>${t('flow_clear')}</button></div>
    </div>
    <div class="flowbody" id="flowBody">
      <div class="flowtrack" id="trLanes"><span class="tlabel">${t('flow_lanes')}</span></div>
      <div class="flowtrack" id="trCount"><span class="tlabel">${t('flow_count')}</span></div>
      <div class="flowtrack" id="trTok"><span class="tlabel">${t('flow_tok')}</span></div>
      <div class="flowtrack" id="trPhase"><span class="tlabel">${t('flow_phase')}</span></div>
      <div class="flowtrack" id="trAxis"></div>
      <div class="flowsel" id="flowSelEl" hidden></div>
      <div class="flowcross" id="flowCrossEl" hidden></div>
      <div class="flowtip" id="flowTipEl" hidden></div>
    </div>`;
  const body=root.querySelector('#flowBody');
  const W=Math.max(360, body.clientWidth||1000);
  const fx=t=>(t-FT0)/((FT1-FT0)||1)*W;
  const ft=px=>FT0+Math.max(0,Math.min(1,px/W))*(FT1-FT0);
  const pcol=id=>gcol(id);
  const green=cvar('--p5'),red=cvar('--out'),grey=cvar('--cr'),ink2=cvar('--ink2'),acc=cvar('--accent');

  // lanes — cap visible lanes at the robust "typical" concurrency; a rare over-concurrency
  // burst (e.g. a 77-way verify wave) is collapsed into a "+N" overflow row instead of 77 rows.
  const rowH=13;
  const lce=[];AG.forEach(a=>{lce.push([a.s,1]);lce.push([a.e,-1]);});lce.sort((x,y)=>x[0]-y[0]||y[1]-x[1]);
  let ll=0,lpt=null;const lseg=[];lce.forEach(([t,d])=>{if(lpt!==null&&t>lpt)lseg.push([lpt,t,ll]);ll+=d;lpt=t;});
  const lwl=lseg.filter(s=>s[1]>s[0]).map(s=>({v:s[2],w:s[1]-s[0]})).sort((a,b)=>a.v-b.v);
  const lTot=lwl.reduce((s,o)=>s+o.w,0)||1; let lAcc=0,lp99=nLanes;
  for(const o of lwl){lAcc+=o.w;if(lAcc/lTot>=0.99){lp99=o.v;break;}}
  let capLanes=Math.min(nLanes,Math.max(6,lp99+2));
  if(nLanes<=capLanes*1.3)capLanes=nLanes;            // no real outlier -> show every lane
  const hasOv=capLanes<nLanes, lanesH=capLanes*rowH+(hasOv?rowH+4:0)+6;
  let lb='';
  AG.forEach(a=>{if(a.lane>=capLanes)return;const x=fx(a.s),w=Math.max(1.3,fx(a.e)-fx(a.s)),y=a.lane*rowH+3;
    lb+=`<rect x="${x.toFixed(1)}" y="${y}" width="${w.toFixed(1)}" height="${rowH-2}" rx="2" fill="${pcol(a.ph)}" opacity="0.85"><title>${aesc(a.t)} — ${aesc(a.desc||'')}\n${tcAbs(a.s)}–${tcAbs(a.e).slice(-5)} · ${kfmt(a.tok?a.tok.total:0)} tok</title></rect>`;});
  if(hasOv){
    const ov=AG.filter(a=>a.lane>=capLanes);
    const oe=[];ov.forEach(a=>{oe.push([a.s,1]);oe.push([a.e,-1]);});oe.sort((x,y)=>x[0]-y[0]||y[1]-x[1]);
    let ol=0,opt=null;const oseg=[];oe.forEach(([t,d])=>{if(opt!==null&&t>opt)oseg.push([opt,t,ol]);ol+=d;opt=t;});
    let oruns=[],ocur=null;
    oseg.forEach(([a,b,v])=>{if(v>0){if(!ocur)ocur={s:a,e:b,peak:v};else{ocur.e=b;if(v>ocur.peak)ocur.peak=v;}}else if(ocur){oruns.push(ocur);ocur=null;}});
    if(ocur)oruns.push(ocur);
    const merged=[];oruns.forEach(r=>{const L=merged[merged.length-1];
      if(L&&fx(r.s)-fx(L.e)<10){L.e=r.e;L.peak=Math.max(L.peak,r.peak);}else merged.push({s:r.s,e:r.e,peak:r.peak});});
    const oyy=capLanes*rowH+3;
    merged.forEach(r=>{const x=fx(r.s),w=Math.max(3,fx(r.e)-fx(r.s)),xm=Math.min(W-22,Math.max(22,x+w/2));
      lb+=`<rect x="${x.toFixed(1)}" y="${oyy}" width="${w.toFixed(1)}" height="${rowH-2}" rx="2" fill="${red}" opacity="0.3"><title>${t('flow_ov',r.peak)}</title></rect>`
        +`<text x="${xm.toFixed(1)}" y="${oyy+rowH-4}" text-anchor="middle" fill="${red}" font-size="9" font-weight="700" font-family="ui-monospace,Menlo,monospace">+${r.peak}</text>`;});
  }
  const laneLbl=hasOv?t('flow_lane_lbl_ov',capLanes,nLanes):t('flow_lane_lbl',nLanes);
  const trL=root.querySelector('#trLanes'), tl=trL.querySelector('.tlabel');
  if(tl)tl.textContent=t('flow_lanes')+' · '+laneLbl;
  trL.insertAdjacentHTML('beforeend',`<svg class="flowsvg" viewBox="0 0 ${W} ${lanesH}" height="${lanesH}">${lb}</svg>`);

  // concurrent-agent count
  const cH=58; const ce=[];AG.forEach(a=>{ce.push([a.s,1]);ce.push([a.e,-1]);});ce.sort((x,y)=>x[0]-y[0]||y[1]-x[1]);
  let cc=0;const cp=[];ce.forEach(([t,d])=>{cp.push([t,cc]);cc+=d;cp.push([t,cc]);});
  const cMax=Math.max(1,...cp.map(p=>p[1])); const cy=v=>cH-3-(v/cMax)*(cH-14);
  const cpoly=cp.length?cp.map(p=>`${fx(p[0]).toFixed(1)},${cy(p[1]).toFixed(1)}`).join(' '):'';
  root.querySelector('#trCount').insertAdjacentHTML('beforeend',`<svg class="flowsvg" viewBox="0 0 ${W} ${cH}" height="${cH}"><polyline points="${cpoly}" fill="none" stroke="${acc}" stroke-width="1.3"/><text x="${W-4}" y="12" text-anchor="end" fill="${ink2}" font-size="10" font-family="ui-monospace,Menlo,monospace">${t('flow_peak',cMax)}</text></svg>`);

  // token lines (toggle in/out/cache)
  const kH=84, kgms=tcNiceStep(((FT1-FT0)/Math.max(20,W/4))||3600000);
  const kmap=new Map();for(const e of EV){const b=Math.floor(e[0]/kgms)*kgms;let o=kmap.get(b);if(!o){o={t:b,gin:0,out:0,cr:0};kmap.set(b,o);}o.gin+=e[1]+e[2];o.out+=e[4];o.cr+=e[3];}
  const kb=[...kmap.values()].sort((a,b)=>a.t-b.t);
  const kseries=[['gin',green],['out',red],['cr',grey]].filter(([k])=>flowTok[k]);
  let kMax=1;kb.forEach(o=>kseries.forEach(([k])=>{if(o[k]>kMax)kMax=o[k];}));
  const ky=v=>kH-4-(v/kMax)*(kH-10); let kpoly='';
  kseries.forEach(([k,col])=>{kpoly+=`<polyline points="${kb.map(o=>`${fx(o.t).toFixed(1)},${ky(o[k]).toFixed(1)}`).join(' ')}" fill="none" stroke="${col}" stroke-width="1.3" opacity="0.95"/>`;});
  root.querySelector('#trTok').insertAdjacentHTML('beforeend',`<svg class="flowsvg" viewBox="0 0 ${W} ${kH}" height="${kH}">${kpoly}</svg>`);

  // actor strip (one row per group)
  const pRowH=15, phaseH=Math.max(pRowH,PW.length*pRowH+4); let pb='';
  PW.forEach((w,i)=>{const x=fx(w.s),wd=Math.max(1,fx(w.e)-fx(w.s)),y=i*pRowH+2;
    pb+=`<rect x="${x.toFixed(1)}" y="${y}" width="${wd.toFixed(1)}" height="${pRowH-3}" rx="2" fill="${pcol(w.id)}" opacity="0.8"/><text x="${(x+4).toFixed(1)}" y="${y+pRowH-6}" fill="#fff" font-size="9" font-family="ui-monospace,Menlo,monospace">${esc(w.id==='P0'?'ORCH':w.id)}</text>`;});
  root.querySelector('#trPhase').insertAdjacentHTML('beforeend',`<svg class="flowsvg" viewBox="0 0 ${W} ${phaseH}" height="${phaseH}">${pb}</svg>`);

  // axis
  const axH=18; let ax=''; const NT=Math.min(7,Math.max(2,Math.round(W/150)));
  for(let k=0;k<NT;k++){const tt=FT0+(FT1-FT0)*k/(NT-1);const xx=fx(tt);
    ax+=`<text x="${xx.toFixed(1)}" y="13" fill="${ink2}" font-size="10" text-anchor="${k===0?'start':k===NT-1?'end':'middle'}" font-family="ui-monospace,Menlo,monospace">${tcAbs(tt)}</text>`;}
  root.querySelector('#trAxis').insertAdjacentHTML('beforeend',`<svg class="flowsvg" viewBox="0 0 ${W} ${axH}" height="${axH}">${ax}</svg>`);

  // token toggle buttons
  const tokEl=root.querySelector('#flowTok');
  tokEl.innerHTML=[['gin','tip_in',green],['out','tip_out',red],['cr','tip_cache',grey]].map(([k,l,col])=>`<button data-k="${k}" class="${flowTok[k]?'':'off'}"><span class="sw" style="background:${col}"></span>${t(l)}</button>`).join('');
  tokEl.querySelectorAll('button').forEach(b=>b.onclick=()=>{flowTok[b.dataset.k]=!flowTok[b.dataset.k];renderFlow();});

  // interaction: crosshair + range select + summary box
  const cross=root.querySelector('#flowCrossEl'), selEl=root.querySelector('#flowSelEl'), tip=root.querySelector('#flowTipEl'), clr=root.querySelector('#flowClear');
  const applySel=()=>{if(flowSel){const x0=fx(flowSel[0]),x1=fx(flowSel[1]);selEl.style.left=x0+'px';selEl.style.width=Math.max(1,x1-x0)+'px';selEl.hidden=false;clr.hidden=false;cross.hidden=true;}else{selEl.hidden=true;clr.hidden=true;}applyBlockFilter();};
  applySel();
  const showTip=(cx,cyPix,a,b)=>{const g=flowAgg(a,b,AG),rr=body.getBoundingClientRect();
    tip.innerHTML=`<div class="th">${tcAbs(a)} – ${tcAbs(b).slice(-5)}</div>
      <div class="tr"><span>${t('flow_dur')}</span><span>${dfmt(g.dur)}</span></div>
      <div class="tr"><span>${t('flow_active_now')}</span><span>${t('ag_agents',g.overlap)}</span></div>
      <div class="tr"><span>${t('flow_started')}</span><span>${t('ag_agents',g.started)}</span></div>
      <div class="tr"><span style="color:${green}">${t('tip_in')}</span><span>${kfmt(g.gin)}</span></div>
      <div class="tr"><span style="color:${red}">${t('tip_out')}</span><span>${kfmt(g.out)}</span></div>
      <div class="tr"><span style="color:${grey}">${t('flow_cr')}</span><span>${kfmt(g.cr)}</span></div>
      <div class="tr"><span>${t('tip_total')}</span><span>${t('ag_tok',kfmt(g.total))}</span></div>`;
    let lx=cx-rr.left+14,ly=cyPix-rr.top+12;lx=Math.min(lx,rr.width-215);ly=Math.min(ly,rr.height-160);
    tip.style.left=Math.max(0,lx)+'px';tip.style.top=Math.max(0,ly)+'px';tip.hidden=false;};
  let drag=false,a0=0;
  body.addEventListener('pointerdown',e=>{drag=true;a0=ft(e.clientX-body.getBoundingClientRect().left);cross.hidden=true;try{body.setPointerCapture(e.pointerId);}catch(_){}});
  body.addEventListener('pointermove',e=>{const rr=body.getBoundingClientRect(),px=e.clientX-rr.left;
    if(drag){const a1=ft(px),lo=Math.min(a0,a1),hi=Math.max(a0,a1);selEl.style.left=fx(lo)+'px';selEl.style.width=Math.max(1,fx(hi)-fx(lo))+'px';selEl.hidden=false;cross.hidden=true;showTip(e.clientX,e.clientY,lo,hi);}
    else if(flowSel){cross.hidden=true;showTip(e.clientX,e.clientY,flowSel[0],flowSel[1]);}
    else{cross.style.left=px+'px';cross.hidden=false;tip.hidden=true;}});
  body.addEventListener('pointerup',e=>{if(!drag)return;drag=false;const a1=ft(e.clientX-body.getBoundingClientRect().left);let lo=Math.min(a0,a1),hi=Math.max(a0,a1);
    if(hi-lo<(FT1-FT0)*0.004){flowSel=null;applySel();tip.hidden=true;}else{flowSel=[lo,hi];applySel();showTip(e.clientX,e.clientY,lo,hi);}});
  body.addEventListener('pointerleave',()=>{if(!drag){cross.hidden=true;tip.hidden=true;}});
  clr.onclick=()=>{flowSel=null;applySel();tip.hidden=true;};
}

const tl=o=>(o&&typeof o==='object')?(o[LANG]||o.en||''):(o||'');
// section-05 blocks are shown/hidden by the flow-timeline range selection:
// no selection -> all visible; otherwise only blocks whose [start,end] overlaps the selection.
function applyBlockFilter(){
  const wrap=document.getElementById('flowBlocks'); if(!wrap)return;
  const sel=(typeof flowSel!=='undefined')?flowSel:null;
  wrap.querySelectorAll('details.flowblk').forEach(el=>{
    if(!sel){el.style.display='';return;}
    const a=+el.dataset.a, b=+el.dataset.b;
    el.style.display=(a<=sel[1] && b>=sel[0])?'':'none';   // inclusive interval overlap
  });
}
function blk(b){
  const c=cvar('--accent');
  const spawnTags=Object.entries(b.spawns).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`<span class="tag">${esc(k)} ×${v}</span>`).join('');
  const big=b.n_spawn>=40||b.dur_sec>=7200;
  const users=b.users.filter(u=>!u.txt.startsWith('[Image')&&!u.txt.startsWith('#')).map(u=>`<div class="uev"><span class="ut">${hm(u.t)}</span>${esc(u.txt)}</div>`).join('');
  const cmds=b.users.filter(u=>u.txt.startsWith('#')).map(u=>u.txt.split('—')[0].trim()).filter((v,i,a)=>a.indexOf(v)===i);
  const imgs=b.users.filter(u=>u.txt.startsWith('[Image')).length;
  const evs=b.sample_events.map(e=>`<div class="evline"><span class="et">${hm(e.t)}</span><span>${esc(e.ty)}${e.desc?' — '+esc(e.desc):''}</span></div>`).join('');
  const a=Date.parse(b.start)||0, bb=Date.parse(b.end)||a;
  return `<details class="blk flowblk" data-a="${a}" data-b="${bb}">
    <summary>
      <div class="time">${md(b.start)} ${hm(b.start)}–${hm(b.end)}<span class="dur">${t('blk_min',(b.dur_sec/60).toFixed(0))}</span></div>
      <div><div class="ttl">${esc(tl(b.title))}<span class="d">${esc(tl(b.desc))}</span></div></div>
      <div class="meta">${b.n_spawn?`<span class="tag ${big?'big':''}">${t('ag_agents',b.n_spawn)}</span>`:''}${imgs?`<span class="tag">${t('blk_imgs',imgs)}</span>`:''}
        <svg class="caret" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 4l4 4-4 4"/></svg></div>
    </summary>
    <div class="inner">
      ${cmds.length?`<h4>${t('blk_cmds')}</h4><div class="spawnwrap">${cmds.map(x=>`<span class="tag big">${esc(x)}</span>`).join('')}</div>`:''}
      ${spawnTags?`<h4>${t('blk_spawned')}</h4><div class="spawnwrap">${spawnTags}</div>`:''}
      ${evs?`<h4>${t('blk_firstev')}</h4>${evs}`:''}
    </div>
  </details>`;
}
// ---- language switcher (top-left) ----
function mountLangSwitcher(){
  if(document.getElementById('langui'))return;
  const el=document.createElement('div');el.id='langui';
  el.innerHTML=[['en','EN'],['zh','中文'],['tr','TR']].map(([l,lbl])=>`<button data-l="${l}" class="langbtn ${l===LANG?'on':''}">${lbl}</button>`).join('');
  document.body.appendChild(el);
  el.querySelectorAll('.langbtn').forEach(b=>b.onclick=()=>setLang(b.dataset.l));
}
function setLang(l){
  if(['en','tr','zh'].indexOf(l)<0||l===LANG)return;
  LANG=l;try{localStorage.setItem('reportLang',l);}catch(e){}
  document.documentElement.lang=l;
  document.querySelectorAll('#langui .langbtn').forEach(b=>b.classList.toggle('on',b.dataset.l===l));
  render();
  if(window.__searchRelang)window.__searchRelang();
}
document.documentElement.lang=LANG;
render();
mountLangSwitcher();
new MutationObserver(()=>render()).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
let _tcRz;addEventListener('resize',()=>{clearTimeout(_tcRz);_tcRz=setTimeout(()=>{renderTimeCharts();if(typeof renderFlow==='function')renderFlow();},160);});
'''

SEARCH = r'''
(function(){
  const appEl=()=>document.getElementById('app');
  let hits=[], cur=-1, panelOpen=false, reapplying=false, moT=0;
  let caseSensitive=true, useRegex=true;   // defaults

  // ---- floating UI (lives outside #app so it survives re-render) ----
  const MAG='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>';
  const root=document.createElement('div');
  root.id='searchui';
  root.innerHTML=
    '<button id="searchToggle" class="sui-fab" title="'+t('srch_open')+'" aria-label="'+t('srch_open_aria')+'">'+MAG+'</button>'+
    '<div id="searchPanel" class="sui-panel" hidden>'+
      '<span class="sui-mag">'+MAG+'</span>'+
      '<input id="searchInput" type="text" spellcheck="false" autocomplete="off" placeholder="'+t('srch_ph')+'"/>'+
      '<button id="searchCase" class="sui-toggle" title="'+t('srch_case')+'" aria-label="'+t('srch_case')+'">Aa</button>'+
      '<button id="searchRegex" class="sui-toggle" title="'+t('srch_regex')+'" aria-label="'+t('srch_regex')+'">.*</button>'+
      '<span id="searchCount" class="sui-count">0/0</span>'+
      '<button id="searchPrev" class="sui-round" title="'+t('srch_prev')+'" aria-label="'+t('srch_prev')+'">‹</button>'+
      '<button id="searchNext" class="sui-round" title="'+t('srch_next')+'" aria-label="'+t('srch_next')+'">›</button>'+
      '<button id="searchClose" class="sui-round" title="'+t('srch_close')+'" aria-label="'+t('srch_close')+'">×</button>'+
    '</div>';
  document.body.appendChild(root);

  const fab=root.querySelector('#searchToggle');
  const panel=root.querySelector('#searchPanel');
  const input=root.querySelector('#searchInput');
  const countEl=root.querySelector('#searchCount');
  const btnCase=root.querySelector('#searchCase');
  const btnRegex=root.querySelector('#searchRegex');
  const btnPrev=root.querySelector('#searchPrev');
  const btnNext=root.querySelector('#searchNext');
  const btnClose=root.querySelector('#searchClose');

  // re-label the (statically built) search UI when the report language changes
  function relangSearch(){
    fab.title=t('srch_open'); fab.setAttribute('aria-label',t('srch_open_aria'));
    input.placeholder=t('srch_ph');
    btnCase.title=t('srch_case'); btnCase.setAttribute('aria-label',t('srch_case'));
    btnRegex.title=t('srch_regex'); btnRegex.setAttribute('aria-label',t('srch_regex'));
    btnPrev.title=t('srch_prev'); btnPrev.setAttribute('aria-label',t('srch_prev'));
    btnNext.title=t('srch_next'); btnNext.setAttribute('aria-label',t('srch_next'));
    btnClose.title=t('srch_close'); btnClose.setAttribute('aria-label',t('srch_close'));
  }
  window.__searchRelang=relangSearch;

  function buildRegex(q){
    if(!q) return null;
    const flags='g'+(caseSensitive?'':'i');
    const src=useRegex ? q : q.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    try{ return new RegExp(src,flags); }
    catch(e){ return null; }   // invalid regex -> error state (only possible when regex is on)
  }

  function clearHighlights(){
    const a=appEl(); if(!a) return;
    a.querySelectorAll('mark.sui-hit').forEach(m=>{
      const p=m.parentNode;
      while(m.firstChild) p.insertBefore(m.firstChild,m);
      p.removeChild(m);
      p.normalize();
    });
    hits=[];
  }

  function highlight(re){
    const a=appEl(); if(!a||!re) return;
    const walker=document.createTreeWalker(a,NodeFilter.SHOW_TEXT,{
      acceptNode(node){
        if(!node.nodeValue||!node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        const p=node.parentNode; if(!p||!p.closest) return NodeFilter.FILTER_REJECT;
        if(p.nodeName==='SCRIPT'||p.nodeName==='STYLE') return NodeFilter.FILTER_REJECT;
        if(p.closest('svg')) return NodeFilter.FILTER_REJECT;          // SVG text can't host <mark>
        if(p.closest('#searchui')) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    const nodes=[]; let n; while(n=walker.nextNode()) nodes.push(n);
    nodes.forEach(node=>{
      const s=node.nodeValue; re.lastIndex=0;
      let m, ranges=[];
      while(m=re.exec(s)){
        if(m[0]===''){ re.lastIndex++; continue; }
        ranges.push([m.index,m.index+m[0].length]);
      }
      if(!ranges.length) return;
      const frag=document.createDocumentFragment(); let last=0;
      ranges.forEach(([st,en])=>{
        if(st>last) frag.appendChild(document.createTextNode(s.slice(last,st)));
        const mk=document.createElement('mark'); mk.className='sui-hit';
        mk.textContent=s.slice(st,en); frag.appendChild(mk); last=en;
      });
      if(last<s.length) frag.appendChild(document.createTextNode(s.slice(last)));
      node.parentNode.replaceChild(frag,node);
    });
    hits=[...a.querySelectorAll('mark.sui-hit')];
  }

  function updateCount(){
    countEl.textContent=hits.length?((cur+1)+'/'+hits.length):(input.value?'0/0':'0');
    const none=!hits.length;
    btnPrev.disabled=none; btnNext.disabled=none;
  }

  function revealAncestors(el){
    let p=el.parentElement;
    while(p && p!==appEl()){
      if(p.tagName==='DETAILS' && !p.open) p.open=true;
      p=p.parentElement;
    }
  }

  function setCurrent(i,scroll){
    if(!hits.length){ cur=-1; updateCount(); return; }
    hits.forEach(h=>h.classList.remove('sui-cur'));
    cur=((i%hits.length)+hits.length)%hits.length;
    const h=hits[cur];
    h.classList.add('sui-cur');
    revealAncestors(h);                               // open collapsed parents so it's visible
    if(scroll!==false) h.scrollIntoView({behavior:'smooth',block:'center'});
    updateCount();
  }

  function performSearch(keepPos){
    const prev=cur;
    reapplying=true;
    clearHighlights();
    const q=input.value;
    const re=buildRegex(q);
    input.classList.toggle('sui-err', !!q && !re);
    if(q && re) highlight(re);
    reapplying=false;
    if(hits.length){
      let idx=keepPos && prev>=0 ? Math.min(prev,hits.length-1) : 0;
      setCurrent(idx, !keepPos);
    } else { cur=-1; updateCount(); }
  }

  // ---- open / close ----
  function openPanel(){
    panelOpen=true; panel.hidden=false; fab.style.display='none';
    input.focus(); input.select();
    if(input.value) performSearch(true);
  }
  function closePanel(){
    panelOpen=false; panel.hidden=true; fab.style.display='';
    clearHighlights(); cur=-1;
  }

  function syncToggles(){ btnCase.classList.toggle('on',caseSensitive); btnRegex.classList.toggle('on',useRegex); }
  syncToggles();

  fab.onclick=openPanel;
  btnClose.onclick=closePanel;
  btnNext.onclick=()=>setCurrent(cur+1);
  btnPrev.onclick=()=>setCurrent(cur-1);
  btnCase.onclick=()=>{ caseSensitive=!caseSensitive; syncToggles(); input.focus(); performSearch(true); };
  btnRegex.onclick=()=>{ useRegex=!useRegex; syncToggles(); input.focus(); performSearch(true); };

  let debT=0;
  input.addEventListener('input',()=>{ clearTimeout(debT); debT=setTimeout(()=>performSearch(false),140); });
  input.addEventListener('keydown',e=>{
    if(e.key==='Enter'){ e.preventDefault(); if(hits.length) setCurrent(cur+(e.shiftKey?-1:1)); else performSearch(false); }
    else if(e.key==='Escape'){ e.preventDefault(); closePanel(); }
  });
  document.addEventListener('keydown',e=>{
    if((e.metaKey||e.ctrlKey) && (e.key==='f'||e.key==='F')){ e.preventDefault(); panelOpen?input.focus():openPanel(); }
  });

  // re-apply highlights after the app re-renders (theme toggle rebuilds #app)
  const target=appEl();
  if(target){
    new MutationObserver(()=>{
      if(reapplying||!panelOpen||!input.value) return;
      clearTimeout(moT); moT=setTimeout(()=>performSearch(true),60);
    }).observe(target,{childList:true,subtree:true});
  }
})();
'''


def build_report(d, here):
    """Write viewdata.json and report.html into *here* (the script's directory)."""

    def p(s): return dt.datetime.fromisoformat(s)
    span=(p(d['meta']['span_end'])-p(d['meta']['span_start'])).total_seconds()
    active=sum(b['dur_sec'] for b in d['timeline'])
    d['meta']['wall_sec']=span
    d['meta']['active_sec']=active
    d['meta']['idle_sec']=span-active
    d['meta']['busy_sec']=sum(pp['agent_busy_sec'] for pp in d['groups'])

    # ---- trilingual narrative per timeline block (en default, tr, zh) ----
    def _L(en, tr, zh): return {'en': en, 'tr': tr, 'zh': zh}
    def narr(b):
        """One honest sentence per activity block, built only from what the block contains:
        the slash commands the user typed, the agent types spawned, and how many."""
        sp = b['spawns']
        users = [u['txt'] for u in b['users']]
        cmds = []
        for u in users:
            for w in u.split():
                if w.startswith('/') and len(w) > 1 and w[1].isalpha() and w not in cmds:
                    cmds.append(w)
        nag = sum(sp.values())
        top = sorted(sp.items(), key=lambda kv: (-kv[1], kv[0]))
        agents = ', '.join('%d %s' % (v, k) for k, v in top)
        first = users[0][:90] if users else ''

        if cmds:
            c = ' '.join(cmds[:3])
            ttl = _L('Command: ' + c, 'Komut: ' + c, '命令：' + c)
        elif nag:
            ttl = _L('%d agents · mostly %s' % (nag, top[0][0]),
                     '%d agent · ağırlıklı %s' % (nag, top[0][0]),
                     '%d 个 agent · 主要是 %s' % (nag, top[0][0]))
        elif users:
            ttl = _L('Main-thread work', 'Ana thread çalışması', '主线程工作')
        else:
            ttl = _L('Idle-adjacent block', 'Boşluk komşusu blok', '空闲相邻区块')

        parts_en, parts_tr, parts_zh = [], [], []
        if nag:
            parts_en.append('Spawned %d sub-agent(s): %s.' % (nag, agents))
            parts_tr.append('%d alt-agent spawn edildi: %s.' % (nag, agents))
            parts_zh.append('派生了 %d 个子 agent：%s。' % (nag, agents))
        else:
            parts_en.append('No sub-agent ran here — main-thread work only.')
            parts_tr.append('Burada alt-agent koşmadı — sadece ana thread çalıştı.')
            parts_zh.append('这里没有子 agent 运行 —— 只有主线程工作。')
        if users:
            parts_en.append('%d user message(s), starting with “%s”.' % (len(users), first))
            parts_tr.append('%d kullanıcı mesajı, ilki: “%s”.' % (len(users), first))
            parts_zh.append('%d 条用户消息，第一条：“%s”。' % (len(users), first))
        return ttl, _L(' '.join(parts_en), ' '.join(parts_tr), ' '.join(parts_zh))

    for b in d['timeline']:
        ti,desc=narr(b)
        b['title']=ti; b['desc']=desc

    json.dump(d, open(os.path.join(here, 'viewdata.json'), 'w', encoding='utf-8'))
    print('enriched. wall %.1fh active %.1fh idle %.1fh'%(span/3600,active/3600,(span-active)/3600))

    # ================= HTML =================
    DATA_JSON=json.dumps(d, ensure_ascii=False)

    body=HTML.replace('__DATA__',DATA_JSON).replace('__JS__',JS).replace('__SEARCH__',SEARCH)
    # a real document with an explicit charset — without it a browser sniffs the encoding and
    # mangles the Turkish/Chinese text (and Windows would write the file in cp1252 too).
    out=('<!doctype html><html lang="en"><head><meta charset="utf-8">'
         '<meta name="viewport" content="width=device-width,initial-scale=1">'
         '<title>Claude Code session report — %s</title></head><body>%s</body></html>'
         % (d['meta']['session'], body))
    open(os.path.join(here, 'report.html'), 'w', encoding='utf-8').write(out)
    print('wrote report.html', len(out),'bytes')


def main():
    argv = sys.argv[1:]
    outdir = None
    rest = []
    i = 0
    while i < len(argv):                       # --out DIR (also --out=DIR)
        a = argv[i]
        if a == '--out' and i + 1 < len(argv):
            outdir = os.path.expanduser(argv[i + 1]); i += 2; continue
        if a.startswith('--out='):
            outdir = os.path.expanduser(a.split('=', 1)[1]); i += 1; continue
        rest.append(a); i += 1
    out = run_analysis(rest)
    if out is None:            # --list (or nothing to do) already handled by run_analysis
        return
    outdir = outdir or HERE
    os.makedirs(outdir, exist_ok=True)
    json.dump(out, open(os.path.join(outdir, 'report-data.json'), 'w', encoding='utf-8'), indent=1)
    build_report(out, outdir)
    print('\n\u2713 report ready:', os.path.join(outdir, 'report.html'))

if __name__ == '__main__':
    main()
