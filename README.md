<h1 align="center">session-analyzer</h1>

<p align="center">
  <b>See where a Claude Code session actually went.</b><br>
  Tokens per actor, three honest time measures, every sub-agent on a timeline — from the
  transcript the CLI already wrote on your machine.
</p>

<p align="center">
  <a href="https://agent-session-report.vercel.app"><img alt="Live on the web"
     src="https://img.shields.io/badge/live%20on%20the%20web-agent--session--report.vercel.app-2fa876?style=flat-square&logo=vercel&logoColor=white"></a>
  <a href="https://agent-session-report.vercel.app/docs"><img alt="Docs"
     src="https://img.shields.io/badge/docs-read-3457d5?style=flat-square&logo=readthedocs&logoColor=white"></a>
  <a href="https://agent-session-report.vercel.app/demo-report"><img alt="Sample report"
     src="https://img.shields.io/badge/sample-report-6b5cf0?style=flat-square"></a>
</p>

<p align="center">
  <img alt="Python 3.8+" src="https://img.shields.io/badge/python-3.8%2B-3776ab?style=flat-square&logo=python&logoColor=white">
  <img alt="Dependencies: none" src="https://img.shields.io/badge/dependencies-none-2fa876?style=flat-square">
  <img alt="Network: never" src="https://img.shields.io/badge/network-never-141d2b?style=flat-square">
  <img alt="Languages: EN, 中文, TR" src="https://img.shields.io/badge/i18n-EN%20%C2%B7%20%E4%B8%AD%E6%96%87%20%C2%B7%20TR-d68420?style=flat-square">
  <a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-blue?style=flat-square"></a>
</p>

<p align="center">
  <img alt="Stars" src="https://img.shields.io/github/stars/Ege-BULUT/session-analyzer?style=flat-square">
  <img alt="Last commit" src="https://img.shields.io/github/last-commit/Ege-BULUT/session-analyzer?style=flat-square">
  <img alt="Code size" src="https://img.shields.io/github/languages/code-size/Ege-BULUT/session-analyzer?style=flat-square">
  <img alt="Top language" src="https://img.shields.io/github/languages/top/Ege-BULUT/session-analyzer?style=flat-square">
  <img alt="Visitors" src="https://visitor-badge.laobi.icu/badge?page_id=Ege-BULUT.session-analyzer&style=flat-square">
</p>

<p align="center">
  <img src="web/img/landing.png" alt="Drop a session log and get the whole picture" width="860">
</p>

---

## Try it

**[agent-session-report.vercel.app](https://agent-session-report.vercel.app)** — drop a session log,
read the report. The analysis runs **in your browser**: the file is never uploaded, and the site has
no backend to upload it to.

Prefer to keep it local? The same analyzer is a single Python file with no dependencies, and it
comes with a session picker:

```bash
python3 serve_report.py                  # session picker at http://127.0.0.1:8799
python3 analyze_and_report.py            # or straight to a report, no UI
python3 analyze_and_report.py --list     # what is available here
```

<p align="center">
  <img src="web/img/launcher.png" alt="The local launcher: pick sessions, merge them, run the analysis" width="860">
</p>

In the launcher: tick a session (read from `~/.claude/projects`) or drag a copied log folder in;
ticking several **merges them into one report**. Sort by the `agents` / `size` / `last edited`
headers, shift-click for a range, or drag a selection box over the rows —
<kbd>ctrl</kbd>/<kbd>cmd</kbd> while dragging deselects. The **Project folder** box finds sessions
in both directions: it fills in from the session you pick, and its button goes the other way.

Curious first? Open the **[sample report](https://agent-session-report.vercel.app/demo-report)** —
built from a synthetic session, so it shows everything without publishing anyone's transcript.
Every screenshot in this README comes from it.

## What you get

<p align="center">
  <img src="web/img/report-overview.png" alt="KPI tiles and the token/time chart" width="860">
</p>

- **Tokens, split by actor** — the main thread and each sub-agent type, switchable between processed
  total, generated output, new input and cache-read.
- **Three time measures, kept apart** — wall-clock, active wall-clock (idle removed), and agent
  work-hours (parallel agents summed separately). Conflating these is how session numbers get misread.
- **Token spend over time** — drag the chart to zoom into any window and read its totals.
- **The parallel-agent flow** — concurrency lanes, agent count, tokens on the same axis, one row per actor.

<p align="center">
  <img src="web/img/report-timeline.png" alt="Flow timeline with parallel lanes and concurrency" width="860">
</p>

- **Every task as a row** — description, type, model, start–end, duration, turns, tokens; sortable and
  searchable with regex.
- **Activity blocks** — the run split on 20-minute idle gaps, each listing what was spawned and which
  commands were typed.

<p align="center">
  <img src="web/img/report-tasks.png" alt="Per-task table grouped by agent type" width="860">
</p>

## Languages

**EN · 中文 · TR** — the interface and every generated report ship in all three, switchable in the
top-left corner, and the choice is remembered.

<sub><i>Other languages are welcome — a translation is one entry in the `T` table of
`analyze_and_report.py`; pull requests are open.</i></sub>

## How the numbers are produced

| Number | Definition |
|---|---|
| processed tokens | `input + cache-creation + cache-read + output`, from the `usage` field of every assistant message |
| generated | output tokens only — what the model actually wrote |
| new input | `input + cache-creation` — context paid for at full price |
| sub-agents | one per `*.meta.json` under `<session>/subagents/`, with tokens from its own transcript |
| grouping | one group per agent type; the main thread is its own group, never folded into another |
| ① wall-clock | first timestamp to last |
| ② active wall-clock | the same span minus idle gaps longer than 20 minutes |
| ③ agent work-hours | every sub-agent's duration summed (parallel included) + the main thread's active span |

③ being larger than ② is not a bug: ten agents working an hour in parallel are one hour of calendar
time and ten hours of agent work. Full detail in the [docs](https://agent-session-report.vercel.app/docs).

## Privacy

Nothing is uploaded, anywhere, in either version — the CLI is local by definition, and the web build
is a static page that reads your file with the File API and analyses it in the tab.

> A generated report **contains your session**: task descriptions, typed commands, the first line of
> your prompts. Treat `report.html` like the transcript it came from before sharing it.

## Repository layout

Single branch, two entry points: the Python tool at the root, and the static site under `web/`
(that folder is the deploy root, so a push to `main` publishes it).

| Path | Role |
|---|---|
| `analyze_and_report.py` | the analyzer and the report renderer — standalone, stdlib only |
| `serve_report.py` | local launcher: session list, drag-and-drop, calls the analyzer |
| `web/analyze.js` | the browser port of the aggregation |
| `web/report-assets.js` | **generated** — the renderer, compiled out of the Python file |
| `build_web.py` | regenerates `web/report-assets.js` |
| `tests/conformance.mjs` | runs both analyzers over one transcript and fails on any difference |
| `tools/demo_session.py` | builds the synthetic session behind the sample report |
| `tools/screenshots.py`, `tools/launcher_shot.py` | regenerate the images used here |
| `web/vercel.json` | static-site config: clean URLs and a strict Content-Security-Policy |

## Development

```bash
python3 build_web.py                        # renderer -> web/report-assets.js
python3 -m http.server -d web 8815          # try the site locally
node tests/conformance.mjs <session.jsonl>  # JS and Python must agree, field for field
python3 tools/demo_session.py               # rebuild the sample report
```

The renderer has exactly one source (`analyze_and_report.py`); the aggregation has two (Python and
JS), and the conformance test is what keeps them honest. Run it after touching either side.

## License

MIT — see [LICENSE](LICENSE).
