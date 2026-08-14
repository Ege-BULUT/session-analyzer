#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Take the screenshots used by the README and the docs page.

Serves web/ locally, drives headless Chrome, and writes PNGs into web/img/. Sections deep inside
the sample report are captured by loading it in a wrapper page scrolled to that section, because
Chrome only ever photographs the viewport.

    python3 tools/demo_session.py && python3 tools/screenshots.py
"""
import http.server, os, socketserver, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB = os.path.join(ROOT, 'web')
IMG = os.path.join(WEB, 'img')
PORT = 8823

CHROME_CANDIDATES = [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    'google-chrome', 'chromium', 'chromium-browser',
]

WRAPPER = """<!doctype html><meta charset="utf-8">
<style>html,body{margin:0;background:%(bg)s}iframe{border:0;width:100%%;height:%(h)dpx;display:block}</style>
<iframe id="f" src="/demo-report.html"></iframe>
<script>
  const f = document.getElementById('f');
  f.onload = () => {
    const d = f.contentDocument;
    const go = () => {
      const el = d.querySelector(%(sel)r);
      if (el) el.scrollIntoView({block: 'start'});
      d.documentElement.scrollTop -= %(off)d;
    };
    setTimeout(go, 400); setTimeout(go, 1200);   // the report renders its charts asynchronously
  };
</script>"""


def chrome():
    for c in CHROME_CANDIDATES:
        if os.path.exists(c):
            return c
        from shutil import which
        if which(c):
            return which(c)
    sys.exit('no Chrome/Chromium found — install one or set CHROME=/path')


def shot(browser, url, out, w, h, wait=4000):
    # a fresh profile per shot: Chrome serialises (and sometimes hangs) on a shared user-data-dir
    import tempfile as _tf
    prof = _tf.mkdtemp(prefix='shot-')
    try:
        subprocess.run([browser, '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
                        '--no-first-run', '--no-default-browser-check', '--disable-extensions',
                        '--force-device-scale-factor=1', '--window-size=%d,%d' % (w, h),
                        '--virtual-time-budget=%d' % wait, '--user-data-dir=' + prof,
                        '--screenshot=' + out, url],
                       check=False, timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        pass
    finally:
        __import__('shutil').rmtree(prof, ignore_errors=True)
    ok = os.path.isfile(out) and os.path.getsize(out) > 5000
    print(('  ok  ' if ok else '  FAIL') + ' %-22s %s' % (os.path.basename(out),
          ('%.0f KB' % (os.path.getsize(out) / 1024)) if ok else ''))
    return ok


def main():
    if not os.path.isfile(os.path.join(WEB, 'demo-report.html')):
        sys.exit('run tools/demo_session.py first')
    os.makedirs(IMG, exist_ok=True)
    browser = os.environ.get('CHROME') or chrome()

    os.chdir(WEB)
    class Q(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a): pass
    handler = lambda *a, **k: Q(*a, directory=WEB, **k)
    srv = socketserver.TCPServer(('127.0.0.1', PORT), handler)
    srv.allow_reuse_address = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.5)
    base = 'http://127.0.0.1:%d' % PORT

    # section shots: (file, css selector inside the report, extra scroll-up, height)
    sections = [
        ('report-overview.png', 'header .kpis', 40, 1000),
        ('report-tokens.png', 'section:nth-of-type(1)', 90, 900),
        ('report-timeline.png', '#flowRoot', 90, 900),
        ('report-tasks.png', 'section:nth-of-type(6)', 90, 1000),
    ]
    print('screenshots ->', IMG)
    shot(browser, base + '/', os.path.join(IMG, 'landing.png'), 1440, 900)
    shot(browser, base + '/docs.html', os.path.join(IMG, 'docs.png'), 1440, 1000)
    for name, sel, off, h in sections:
        wrap = os.path.join(WEB, '__wrap.html')
        open(wrap, 'w', encoding='utf-8').write(WRAPPER % {'sel': sel, 'off': off, 'h': h, 'bg': '#eef1f6'})
        shot(browser, base + '/__wrap.html', os.path.join(IMG, name), 1440, h, wait=6000)
        os.remove(wrap)
    srv.shutdown()


if __name__ == '__main__':
    main()
