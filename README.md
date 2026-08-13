# session-analyzer

Turn a Claude Code session into one self-contained HTML report: where the tokens went, how long
the work really took, what every sub-agent did, and an hour-by-hour timeline of the run.

Deterministic and offline — it reads the JSONL transcript the CLI already wrote, sums the
`usage` fields, and renders a single file. No model calls, no telemetry, no network.
Standard library only, Python 3.8+.

## What the report shows

- **Tokens** — processed total, generated output, new input, cache-read, split per actor
  (the main thread and each sub-agent type) with an interactive pie/bar you can switch metrics on.
- **Time, three ways** — wall-clock, active wall-clock (idle removed), and agent work-hours
  (parallel agents summed separately). Mixing these three up is the usual way session numbers
  get misread, so the report keeps them apart and explains the gap.
- **Token spend over time** — a zoomable time-series; drag to select a range.
- **Parallel-agent flow timeline** — lanes showing what ran concurrently, concurrent-agent count,
  tokens over time, and one row per actor.
- **Every task, one row** — description, type, model, start–end, duration, turns and tokens,
  searchable (regex supported) and sortable.
- **Activity blocks** — the run split on 20-minute idle gaps, each with what was spawned and
  the commands typed.

Interface and report are trilingual: **English · Türkçe · 简体中文** (switch top-left, the
report remembers your choice). Light and dark themes follow the browser.

## Use it

```bash
python3 serve_report.py
```

Your browser opens on `http://127.0.0.1:8799`:

1. Tick a session in the list (read from `~/.claude/projects`), or drag a copied log folder /
   `.jsonl` onto the drop zone. Ticking several **merges them into one report** — useful when a
   piece of work spanned `/resume`s or several days.
2. Press **Run analysis**, then **Open the report ↗**.

The **Project folder** box is only a shortcut for finding sessions: it fills in from the session
you pick (read from the `cwd` the transcript recorded), and the button on the left goes the other
way — type a folder, get its sessions ticked.

List tips: click the `agents` / `size` / `last edited` headers to sort (click again to flip),
click a row to toggle, shift-click for a range, or drag a selection box over the rows — hold
<kbd>ctrl</kbd>/<kbd>cmd</kbd> while dragging to deselect instead.

## Or from the command line

```bash
python3 analyze_and_report.py                     # busiest session of the current folder's project
python3 analyze_and_report.py <session-uuid>      # one specific session
python3 analyze_and_report.py a.jsonl b.jsonl     # several sessions, merged into one report
python3 analyze_and_report.py --list              # what is available for this folder
python3 analyze_and_report.py --out ./out         # where to write (default: next to the script)
```

Three files come out: `report.html` (open this), `report-data.json` (raw aggregates) and
`viewdata.json` (the enriched view model). The JSON files are there so you can chart the numbers
somewhere else.

## How the numbers are produced

| Number | Source |
|---|---|
| tokens | the `usage` field of every assistant message — main thread and every sub-agent transcript |
| sub-agents | one `*.meta.json` per spawned agent under `<session-uuid>/subagents/` |
| grouping | one group per agent type; the main thread is its own group, never folded into another |
| wall-clock | first to last timestamp |
| active wall-clock | the same span minus idle gaps longer than 20 minutes |
| agent work-hours | every sub-agent's own duration summed (parallel included) + the main thread's active span |

Cache-read usually dominates the processed total — long context is re-read every turn. That is
expected and cheap; switch the metric to **Generated** or **New input** for the real load.

## Files

| File | Role |
|---|---|
| `analyze_and_report.py` | the analyzer and report renderer — runs standalone |
| `serve_report.py` | local launcher: serves the page, calls the analyzer |
| `index.html` | the launcher page |

## Privacy

Everything runs on your machine. The launcher binds to `127.0.0.1`, the report is a single local
HTML file, and nothing is uploaded anywhere. Note that a report does contain the content of your
session — task descriptions, typed commands, the first line of your prompts — so treat a
generated `report.html` like the transcript it came from before sharing it.

## Troubleshooting

- **The list is empty** — no logs under `~/.claude/projects` on this machine. Drag a copied log
  folder in instead.
- **The project folder is not detected** — the session ran on another machine, so the path in the
  transcript does not exist here. Type it in, or ignore it.
- **Port already in use** — the launcher walks up from 8799 until a port is free and prints the
  address it picked. `PORT=9000 python3 serve_report.py` overrides it.

## License

MIT — see [LICENSE](LICENSE).
