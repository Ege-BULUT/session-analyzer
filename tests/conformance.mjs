// Proves the browser analyzer agrees with the Python one, field for field.
//
//   node tests/conformance.mjs <session.jsonl> [more.jsonl ...]
//
// Runs analyze_and_report.py on the same input, then diffs the two enriched view models.
// Timestamps are compared as instants (Python prints microseconds, JS keeps the raw string),
// and floats within 1e-6 — everything else must match exactly.

import {execFileSync} from 'node:child_process';
import {mkdtempSync, readFileSync, readdirSync, statSync, existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, dirname, basename} from 'node:path';
import {fileURLToPath} from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..');
const args = process.argv.slice(2);
if (!args.length) {
  console.error('usage: node tests/conformance.mjs <session.jsonl> [more.jsonl ...]');
  process.exit(2);
}

// ---- the browser code wants File-ish objects; give it the same shape over node fs ----
class NodeFile {
  constructor(path) { this.path = path; this.name = basename(path); this.lastModified = statSync(path).mtimeMs; }
  async text() { return readFileSync(this.path, 'utf-8'); }
  stream() {
    const text = readFileSync(this.path, 'utf-8');
    return new ReadableStream({start(c) { c.enqueue(new TextEncoder().encode(text)); c.close(); }});
  }
}

const {analyze, groupFiles} = await import(join(ROOT, 'web', 'analyze.js'));

const files = [];
for (const a of args) {
  files.push({path: a, file: new NodeFile(a)});
  const subdir = join(a.slice(0, -6), 'subagents');
  if (existsSync(subdir)) {
    for (const f of readdirSync(subdir)) {
      files.push({path: a.slice(0, -6) + '/subagents/' + f, file: new NodeFile(join(subdir, f))});
    }
  }
}
const sessions = groupFiles(files);
const js = await analyze(sessions);

const out = mkdtempSync(join(tmpdir(), 'conformance-'));
execFileSync('python3', [join(ROOT, 'analyze_and_report.py'), ...args, '--out', out], {stdio: 'ignore'});
// viewdata.json is report-data.json *after* the enrichment step, which is what analyze()
// returns — comparing against the pre-enrichment file would miss the narratives.
const py = JSON.parse(readFileSync(join(out, 'viewdata.json'), 'utf-8'));

// ---- compare ----
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/;
const norm = v => (typeof v === 'string' && ISO.test(v) ? 'T' + Date.parse(v) : v);
const diffs = [];

function walk(a, b, path) {
  a = norm(a); b = norm(b);
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b)) return diffs.push(`${path}: array vs non-array`);
    if (a.length !== b.length) return diffs.push(`${path}: length ${a.length} (js) vs ${b.length} (py)`);
    a.forEach((x, i) => walk(x, b[i], `${path}[${i}]`));
  } else if (a && b && typeof a === 'object' && typeof b === 'object') {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const k of keys) {
      if (!(k in a)) { diffs.push(`${path}.${k}: missing in js`); continue; }
      if (!(k in b)) { diffs.push(`${path}.${k}: missing in py`); continue; }
      walk(a[k], b[k], `${path}.${k}`);
    }
  } else if (typeof a === 'number' && typeof b === 'number') {
    if (Math.abs(a - b) > 1e-6) diffs.push(`${path}: ${a} (js) vs ${b} (py)`);
  } else if (a !== b) {
    diffs.push(`${path}: ${JSON.stringify(a)} (js) vs ${JSON.stringify(b)} (py)`);
  }
}

walk(js, py, '');

const shown = diffs.slice(0, 25);
if (diffs.length) {
  console.error(`FAIL — ${diffs.length} difference(s):`);
  for (const d of shown) console.error('  ' + d);
  if (diffs.length > shown.length) console.error(`  … and ${diffs.length - shown.length} more`);
  process.exit(1);
}
console.log(`OK — js and python agree (${js.meta.n_subagents} sub-agents, ` +
            `${js.meta.n_main_assistant} main turns, ${js.meta.grand_total.total.toLocaleString()} tokens)`);
