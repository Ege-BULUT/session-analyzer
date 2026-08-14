#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a synthetic session and render it, for the sample report and the screenshots.

Everything here is invented: no real transcript, no real project, nothing anybody said. That is
the point — the documentation can show a full report without publishing somebody's session.

    python3 tools/demo_session.py            # -> web/demo-report.html
"""
import json, os, random, subprocess, sys, tempfile, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "docs")
T0 = dt.datetime(2026, 3, 12, 9, 0, tzinfo=dt.timezone.utc)      # fixed: same demo every run
RNG = random.Random(7)

AGENTS = [                       # (type, how many, what they were asked to do)
    ('explorer', 6, ['Map the routing layer', 'Find every call site of `render`', 'Survey the test suite',
                     'Trace the config loader', 'List the public exports', 'Locate the cache layer']),
    ('implementer', 5, ['Add pagination to the list view', 'Extract the retry helper',
                        'Wire the settings screen', 'Port the date utils', 'Split the god object']),
    ('reviewer', 4, ['Review the pagination diff', 'Review the retry helper', 'Review settings wiring',
                     'Second pass on the god-object split']),
    ('test-writer', 3, ['Cover the retry helper', 'Cover pagination edges', 'Regression test for the cache']),
    ('doc-writer', 1, ['Update the module README']),
]
USER_TURNS = [
    (0, '/analyze the repo and plan the refactor'),
    (52, 'go ahead with the plan, run the agents in parallel'),
    (188, 'the retry helper still drops the last attempt — fix it'),
    (263, '/status'),
]


def msg(kind, when, usage=None, text=None, model='claude-opus-5'):
    m = {'type': kind, 'timestamp': when.isoformat().replace('+00:00', 'Z')}
    if kind == 'assistant':
        m['message'] = {'model': model, 'usage': usage}
    else:
        m['message'] = {'content': text}
    return json.dumps(m)


def usage(scale):
    return {'input_tokens': RNG.randint(2, 40),
            'cache_creation_input_tokens': RNG.randint(4000, 30000) * scale,
            'cache_read_input_tokens': RNG.randint(60000, 400000) * scale,
            'output_tokens': RNG.randint(400, 4200) * scale}


def build(tmp):
    sid = 'demo0001-2b3c-4d5e-6f70-8192a3b4c5d6'
    main_path = os.path.join(tmp, sid + '.jsonl')
    subdir = os.path.join(tmp, sid, 'subagents')
    os.makedirs(subdir, exist_ok=True)

    lines, minute = [], 0
    for at, text in USER_TURNS:
        lines.append((at, msg('user', T0 + dt.timedelta(minutes=at), text=text)))
    # the orchestrator talks throughout the run, with a long idle stretch in the middle
    for minute in list(range(0, 60)) + list(range(150, 200)) + list(range(255, 290)):
        lines.append((minute, msg('assistant', T0 + dt.timedelta(minutes=minute, seconds=RNG.randint(0, 50)),
                                  usage=usage(1))))
    lines.sort(key=lambda x: x[0])
    open(main_path, 'w', encoding='utf-8').write('\n'.join(l for _, l in lines) + '\n')

    i = 0
    for ty, n, descs in AGENTS:
        for k in range(n):
            i += 1
            start = {'explorer': 6, 'implementer': 20, 'reviewer': 42,
                     'test-writer': 58, 'doc-writer': 74}[ty] + k * RNG.randint(2, 5)
            span = RNG.randint(4, 18)
            name = 'agent-demo%03d' % i
            json.dump({'agentType': ty, 'description': descs[k % len(descs)],
                       'toolUseId': 'toolu_demo%03d' % i, 'spawnDepth': 1},
                      open(os.path.join(subdir, name + '.meta.json'), 'w', encoding='utf-8'))
            turns = []
            for t in range(RNG.randint(3, 9)):
                when = T0 + dt.timedelta(minutes=start + t * span / 8.0, seconds=RNG.randint(0, 40))
                turns.append(msg('assistant', when, usage=usage(1),
                                 model='claude-sonnet-5' if ty in ('explorer', 'test-writer') else 'claude-opus-5'))
            open(os.path.join(subdir, name + '.jsonl'), 'w', encoding='utf-8').write('\n'.join(turns) + '\n')
    return main_path


def main():
    tmp = tempfile.mkdtemp(prefix='demo-session-')
    main_path = build(tmp)
    os.makedirs(OUT, exist_ok=True)
    subprocess.run([sys.executable, os.path.join(ROOT, 'analyze_and_report.py'), main_path, '--out', tmp],
                   check=True, stdout=subprocess.DEVNULL)
    src = open(os.path.join(tmp, 'report.html'), encoding='utf-8').read()
    dest = os.path.join(OUT, 'demo-report.html')
    open(dest, 'w', encoding='utf-8').write(src)
    print('wrote', dest, '(%.0f KB)' % (len(src) / 1024))


if __name__ == '__main__':
    main()
