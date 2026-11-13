"""Static HTML viewer for saved runs. Read-only; never calls a model or a backend.

    python -m viewer.build --runs runs/pilot_v1 --out viewer/out/pilot_v1
    python -m viewer.build --runs runs/_dev_stub/dry1 --out viewer/out/dry1      # pages say STUB

Input: every directory under --runs that contains output.json and/or meta.json (harness run
directories, or a single `blackbox.cli investigate --out` directory). Directories starting
with "_" (e.g. _interrupted, _results) are skipped. Output: index.html plus one page per run.

Shown per run: harness and output status, final report (status, headline, causes, claims,
next check, suggested fix), raw vs recovered citations (citation_audit), each investigation
step (decision, stop reason, orchestration rules, hypothesis changes and ledger, tool requests
with status / records / cache hits / errors, invalid citations, context numbers, raw model
output), evidence records with content (anchored, with "cited by" back-references), B0/B1
evidence budget, unauthorized attempts, usage, timing, config and harness metadata.
Runs under runs/_dev_stub or carrying "stub": true are marked STUB on every page.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from blackbox.schemas import SystemOutput

CSS = """
:root{--bg:#f7f7f5;--panel:#fff;--ink:#1d1d1b;--muted:#6b6b66;--line:#e2e1dc;--accent:#2f5d8a;
--ok:#2e7d4f;--warn:#a86a00;--bad:#b3261e;--chip:#eef2f7;--code:#f1f0ec;--stub:#b3261e}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#151514;--panel:#1e1e1c;--ink:#ecebe6;
--muted:#a3a29b;--line:#34332f;--accent:#8fb6dd;--ok:#6cc08f;--warn:#e0a84a;--bad:#ef7a72;--chip:#26303a;
--code:#262624;--stub:#ef7a72}}
:root[data-theme="dark"]{--bg:#151514;--panel:#1e1e1c;--ink:#ecebe6;--muted:#a3a29b;--line:#34332f;--accent:#8fb6dd;
--ok:#6cc08f;--warn:#e0a84a;--bad:#ef7a72;--chip:#26303a;--code:#262624;--stub:#ef7a72}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}a{color:var(--accent)}
h1{font-size:20px;margin:8px 0}h2{font-size:16px;margin:24px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
h3{font-size:14px;margin:12px 0 6px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:10px 0}
.muted{color:var(--muted)}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.stub{background:var(--stub);color:#fff;font-weight:700;padding:6px 10px;border-radius:6px;margin:8px 0}
.kv{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:4px 16px}
.kv div span{color:var(--muted)}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid var(--line);
padding:4px 6px;text-align:left;vertical-align:top}th{color:var(--muted);font-weight:600}
.scroll{overflow-x:auto}
code,pre{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}
pre{background:var(--code);padding:8px;border-radius:6px;white-space:pre-wrap;word-break:break-word;margin:4px 0}
.chip{display:inline-block;background:var(--chip);border-radius:4px;padding:0 5px;margin:1px 2px;font-family:ui-monospace,Consolas,monospace;font-size:12px}
.chip.missing{outline:1px dashed var(--bad)}
.tag{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:0 7px;font-size:12px;margin-right:4px}
details>summary{cursor:pointer;color:var(--accent)}
.ev{border-top:1px solid var(--line);padding:6px 0}.ev:target{background:var(--chip)}
"""


def esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def anchor(eid: str) -> str:
    return "ev-" + re.sub(r"[^A-Za-z0-9_-]", "_", eid)


def read_json(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def status_class(s: Optional[str]) -> str:
    if s in ("completed", "ok", "diagnosis", "stop_diagnosis"):
        return "ok"
    if s in ("failed", "timeout", "exception", "error", "denied", "stop_error", "stop_timeout", "invalid"):
        return "bad"
    return "warn"


def find_runs(root: Path) -> list[Path]:
    dirs = []
    if (root / "output.json").exists() or (root / "meta.json").exists():
        return [root]
    for p in sorted(root.rglob("*")):
        if not p.is_dir() or any(part.startswith("_") for part in p.relative_to(root).parts):
            continue
        if (p / "output.json").exists() or (p / "meta.json").exists():
            dirs.append(p)
    return dirs


class Run:
    def __init__(self, d: Path):
        self.dir = d
        self.meta = read_json(d / "meta.json") or {}
        self.input = read_json(d / "input.json") or {}
        self.raw = read_json(d / "output.json")
        self.out: Optional[SystemOutput] = None
        self.error = None
        if (d / "output.json").exists():
            try:
                self.out = SystemOutput.model_validate_json((d / "output.json").read_text(encoding="utf-8"))
            except Exception as exc:
                self.error = f"output.json does not validate as SystemOutput: {exc}"
        else:
            self.error = "no output.json"
        self.id = self.meta.get("run_id") or self.input.get("run_id") or d.name
        self.stub = ("_dev_stub" in d.resolve().parts or bool(self.meta.get("stub")) or bool(self.input.get("stub"))
                     or bool((self.raw or {}).get("stub")))
        self.page = re.sub(r"[^A-Za-z0-9_.-]", "_", self.id) + ".html"


def chips(ids: list[str], known: set[str]) -> str:
    missing = ' missing" title="not in supplied evidence'
    return " ".join(f'<a class="chip{"" if i in known else missing}" href="#{anchor(i)}">{esc(i)}</a>'
                    for i in ids) or '<span class="muted">none</span>'


def page(title: str, body: str, stub: bool) -> str:
    banner = ('<div class="stub">STUB: development output from eval/stub.py. Not a model output, '
              'not a result.</div>' if stub else "")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>'
            f'<style>{CSS}</style></head><body><main>{banner}{body}{banner}</main></body></html>')


def kv(pairs: list[tuple[str, Any]]) -> str:
    return '<div class="kv">' + "".join(f"<div><span>{esc(k)}</span> {v}</div>" for k, v in pairs) + "</div>"


def audit_table(audit) -> str:
    if not audit:
        return '<p class="muted">no citations</p>'
    rows = "".join(
        f'<tr><td>{esc(c.where)}</td><td><code>{esc(c.raw)}</code></td>'
        f'<td>{("<code>" + esc(c.resolved) + "</code>") if c.resolved else "-"}</td>'
        f'<td class="{"ok" if c.method == "exact" else ("warn" if c.method in ("embedded", "suffix") else "bad")}">'
        f'{esc(c.method)}{" (recovered)" if c.method in ("embedded", "suffix") else ""}</td></tr>' for c in audit)
    return f'<div class="scroll"><table><tr><th>where</th><th>raw (as written)</th><th>resolved</th><th>method</th></tr>{rows}</table></div>'


def render_run(r: Run, batch: str) -> str:
    o, m = r.out, r.meta
    parts = [f'<p><a href="index.html">&larr; {esc(batch)}</a></p><h1>{esc(r.id)}</h1>']
    if r.error:
        parts.append(f'<div class="panel bad">{esc(r.error)}</div>')
    harness = m.get("status")
    if o is None:
        parts.append(kv([("harness status", f'<b class="{status_class(harness)}">{esc(harness)}</b>'),
                         ("exception", esc(m.get("exception")))]))
        parts.append(f"<details><summary>meta.json</summary><pre>{esc(json.dumps(m, indent=1))}</pre></details>")
        return page(r.id, "".join(parts), r.stub)

    known = {e.evidence_id for e in o.evidence_seen}
    if o.evidence_budget:
        known |= set(o.evidence_budget.included_ids)
    rep = o.report
    last = o.steps[-1] if o.steps else None
    parts.append('<div class="panel">' + kv([
        ("system", esc(o.system)), ("incident", esc(o.incident_id)), ("ablation", esc(o.ablation)),
        ("harness status", f'<b class="{status_class(harness)}">{esc(harness or "-")}</b>'),
        ("output status", f'<b class="{status_class(o.status)}">{esc(o.status)}</b>'),
        ("report status", f'<b class="{status_class(rep.status if rep else None)}">{esc(rep.status if rep else "none")}</b>'),
        ("stopping", esc(f"{last.stop_decision}: {last.stop_reason}" if last else "single call")),
        ("rounds / tool calls", esc(f"{o.rounds_used} / {o.tool_calls_used}")),
        ("budget violations", esc(o.budget_violations)),
        ("unauthorized attempted / executed", esc(f"{len(o.unauthorized_attempts)} / {o.unauthorized_executed}")),
        ("model calls", esc(o.usage.model_calls)),
        ("tokens prompt+completion", esc(f"{o.usage.prompt_tokens}+{o.usage.completion_tokens}")),
        ("model seconds", esc(f"{o.usage.model_seconds:.1f}")), ("system wall s", esc(o.wall_seconds)),
        ("harness wall s", esc(m.get("wall_seconds", "-"))),
        ("start / end", esc(f"{m.get('start_time', '-')} / {m.get('end_time', '-')}")),
        ("error", f'<span class="bad">{esc(o.error)}</span>' if o.error else "-"),
    ]) + "</div>")

    # ---- report
    parts.append("<h2>Final report</h2>")
    if rep is None:
        parts.append('<p class="bad">no report</p>')
    else:
        rows = "".join(f"<tr><td>{c.rank}</td><td>{esc(c.component)}</td><td>{esc(c.mechanism)}</td>"
                       f"<td>{esc(c.confidence)}</td><td>{chips(c.evidence_ids, known)}</td></tr>" for c in rep.ranked_causes)
        claims = "".join(f"<tr><td>{esc(c.claim_id)}</td><td>{esc(c.kind)}</td><td>{esc(c.text)}</td>"
                         f"<td>{chips(c.evidence_ids, known)}</td></tr>" for c in rep.claims)
        parts.append(f'<div class="panel"><p><b>{esc(rep.headline)}</b></p>'
                     + (f'<h3>Ranked causes</h3><div class="scroll"><table><tr><th>#</th><th>component</th><th>mechanism</th>'
                        f'<th>confidence</th><th>evidence</th></tr>{rows}</table></div>' if rows else "")
                     + (f'<h3>Claims</h3><div class="scroll"><table><tr><th>id</th><th>kind</th><th>text</th><th>evidence</th>'
                        f'</tr>{claims}</table></div>' if claims else "")
                     + (f"<h3>Unresolved questions</h3><ul>{''.join(f'<li>{esc(q)}</li>' for q in rep.unresolved_questions)}</ul>"
                        if rep.unresolved_questions else "")
                     + (f"<p><span class='muted'>Next check:</span> {esc(rep.next_check)}</p>" if rep.next_check else "")
                     + (f"<p><span class='muted'>Suggested fix (untested):</span> {esc(rep.suggested_fix)}</p>"
                        if rep.suggested_fix else "")
                     + (f"<p><span class='muted'>Invalid citations removed:</span> {chips(rep.invalid_citations, known)}</p>"
                        if rep.invalid_citations else "")
                     + "<h3>Citation audit (raw vs recovered)</h3>" + audit_table(rep.citation_audit) + "</div>")

    # ---- B0/B1 evidence budget
    eb = o.evidence_budget
    if eb:
        parts.append("<h2>Evidence budget (single call)</h2><div class='panel'>" + kv([
            ("context tokens", esc(eb.context_tokens)), ("reserved", esc(eb.reserved_tokens)),
            ("evidence budget", esc(eb.evidence_token_budget)), ("evidence tokens total", esc(eb.evidence_tokens_total)),
            ("included", esc(len(eb.included_ids))), ("omitted", esc(len(eb.omitted_ids))),
            ("omitted tokens", esc(eb.omitted_tokens)),
            ("B1 matched", f'<b class="{"ok" if eb.matched else ("bad" if eb.matched is False else "muted")}">{esc(eb.matched)}</b>'),
        ]) + (f"<details><summary>omitted ids ({len(eb.omitted_ids)})</summary><p>"
              + " ".join(f'<span class="chip">{esc(i)}</span>' for i in eb.omitted_ids) + "</p></details>"
              if eb.omitted_ids else "") + "</div>")
    if o.context:
        parts.append(f"<details><summary>context accounting (run level)</summary><pre>{esc(json.dumps(o.context, indent=1))}</pre></details>")

    # ---- steps
    if o.steps:
        parts.append("<h2>Investigation steps</h2>")
    cited_by: dict[str, list[str]] = defaultdict(list)
    for s in o.steps:
        for h in s.hypotheses_after:
            for i in h.supporting + h.contradicting:
                cited_by[i].append(f"r{s.round} {h.hypothesis_id}")
        dup = {d.split("==")[0]: d.split("==")[1] for d in s.duplicate_calls}
        reqs = {q.call_id: q for q in s.tool_requests}
        trows = []
        for res in s.tool_results:
            q = reqs.get(res.call_id)
            note = f"cache hit (same as {dup[res.call_id]})" if res.call_id in dup else ""
            trows.append(f'<tr><td>{esc(res.call_id)}</td><td>{esc(res.tool)}</td>'
                         f'<td><code>{esc(json.dumps(q.args if q else {}, ensure_ascii=False))}</code></td>'
                         f'<td class="{status_class(res.status)}">{esc(res.status)}</td>'
                         f'<td>{len(res.records)}{" of " + esc(res.total_matches) if res.truncated else ""}</td>'
                         f'<td>{res.elapsed_ms:.0f}</td><td>{esc(note)} {esc(res.error or "")}</td></tr>'
                         + (f'<tr><td></td><td colspan="6">{chips([x.evidence_id for x in res.records], known)}</td></tr>'
                            if res.records else ""))
        hyp = "".join(f"<tr><td>{esc(h.hypothesis_id)}</td><td>{esc(h.status)}</td><td>{esc(h.confidence)}</td>"
                      f"<td>{esc(h.component)}: {esc(h.mechanism)}</td><td>{chips(h.supporting, known)}</td>"
                      f"<td>{chips(h.contradicting, known)}</td></tr>" for h in s.hypotheses_after)
        ctx = s.context or {}
        ctx_line = ", ".join(f"{k}={ctx[k]}" for k in ("prompt_tokens_est", "prompt_tokens_actual", "num_ctx", "num_predict",
                                                       "fixed_tokens", "notebook_tokens", "records_full",
                                                       "records_one_line", "records_omitted", "attempts",
                                                       "prompt_truncated_by_server") if k in ctx)
        parts.append(
            f'<div class="panel"><h3>Round {s.round}: model asked <code>{esc(s.model_decision or "?")}</code> &rarr; '
            f'<span class="{status_class(s.stop_decision)}">{esc(s.stop_decision)}</span></h3>'
            + (f'<p class="muted">{esc(s.stop_reason)}</p>' if s.stop_reason else "")
            + (f'<p class="bad">parse failure: {esc(s.parse_error)}</p>' if not s.parse_ok else "")
            + "".join(f'<p><span class="tag">orchestration</span>{esc(x)}</p>' for x in s.orchestration_rules)
            + kv([("model s", esc(f"{s.model_seconds:.1f}")),
                  ("tokens", esc(f"{s.prompt_tokens}+{s.completion_tokens}")), ("context", esc(ctx_line or "-"))])
            + (f"<h3>Hypothesis changes</h3><ul>{''.join(f'<li><code>{esc(c)}</code></li>' for c in s.hypothesis_changes)}</ul>"
               if s.hypothesis_changes else "")
            + (f'<h3>Ledger after round</h3><div class="scroll"><table><tr><th>id</th><th>status</th><th>conf.</th>'
               f'<th>hypothesis</th><th>supporting</th><th>contradicting</th></tr>{hyp}</table></div>' if hyp else "")
            + (f'<h3>Tool calls</h3><div class="scroll"><table><tr><th>call</th><th>tool</th><th>args</th><th>status</th>'
               f'<th>records</th><th>ms</th><th>notes</th></tr>{"".join(trows)}</table></div>' if trows else "")
            + (f"<p><span class='muted'>Invalid citations removed:</span> "
               f"{' '.join(f'<span class=chip>{esc(i)}</span>' for i in s.invalid_citations)}</p>" if s.invalid_citations else "")
            + (f"<details><summary>citation audit ({len(s.citation_audit)})</summary>{audit_table(s.citation_audit)}</details>"
               if s.citation_audit else "")
            + f"<details><summary>raw model output</summary><pre>{esc(s.raw_output)}</pre></details></div>")
    if rep:
        for c in rep.ranked_causes:
            for i in c.evidence_ids:
                cited_by[i].append(f"report cause{c.rank}")
        for c in rep.claims:
            for i in c.evidence_ids:
                cited_by[i].append(f"report {c.claim_id}")

    if o.unauthorized_attempts:
        parts.append("<h2>Unauthorized attempts (refused, never executed)</h2><div class='panel'><ul>"
                     + "".join(f"<li><code>{esc(a.call_id)} {esc(a.tool)} {esc(json.dumps(a.args))}</code></li>"
                               for a in o.unauthorized_attempts) + "</ul></div>")

    # ---- evidence
    parts.append(f"<h2>Evidence seen ({len(o.evidence_seen)})</h2><div class='panel'>")
    for e in o.evidence_seen:
        flags = "".join(f'<span class="tag">{t}</span>' for t, on in
                        (("fixture", e.fixture), ("truncated", e.truncated), ("live", e.attributes.get("live")))
                        if on)
        parts.append(f'<div class="ev" id="{anchor(e.evidence_id)}"><b><code>{esc(e.evidence_id)}</code></b> '
                     f'<span class="muted">{esc(e.source)} {esc(e.service or "")} event={esc(e.event_time)} '
                     f'available={esc(e.available_time)}</span> {flags}<br>{esc(e.title)}'
                     + (f"<pre>{esc(e.content)}</pre>" if e.content and e.content.strip() != e.title.strip() else "")
                     + (f'<p class="muted">cited by: {esc(", ".join(dict.fromkeys(cited_by[e.evidence_id])))}</p>'
                        if cited_by.get(e.evidence_id) else "")
                     + "</div>")
    missing = [i for i in cited_by if i not in known]
    if missing:
        parts.append(f'<p class="bad">Cited but not in supplied evidence: {" ".join(esc(i) for i in missing)}</p>')
    parts.append("</div>")

    parts.append("<h2>Configuration and metadata</h2><div class='panel'>"
                 + kv([("prompt versions", esc(json.dumps(o.prompt_versions))), ("model", esc(o.config.model)),
                       ("seed", esc(o.config.seed)), ("num_ctx / num_predict", esc(f"{o.config.num_ctx} / {o.config.num_predict}")),
                       ("code revision", esc(o.config.code_revision)), ("schema", esc(o.schema_version)),
                       ("model digest", esc(m.get("model_digest", "-")))])
                 + f"<details><summary>RunConfig</summary><pre>{esc(o.config.model_dump_json(indent=1))}</pre></details>"
                 + (f"<details><summary>meta.json</summary><pre>{esc(json.dumps(m, indent=1, ensure_ascii=False))}</pre></details>" if m else "")
                 + (f"<details><summary>input.json</summary><pre>{esc(json.dumps(r.input, indent=1, ensure_ascii=False))}</pre></details>" if r.input else "")
                 + "</div>")
    return page(r.id, "".join(parts), r.stub)


def render_index(runs: list[Run], batch: str, src: Path) -> str:
    rows = []
    for r in runs:
        o = r.out
        rep = o.report if o else None
        rows.append(f'<tr><td><a href="{esc(r.page)}">{esc(r.id)}</a>{" <b class=bad>STUB</b>" if r.stub else ""}</td>'
                    f'<td>{esc(o.system if o else r.input.get("system", "-"))}</td>'
                    f'<td>{esc(o.ablation if o else r.input.get("ablation", "-"))}</td>'
                    f'<td class="{status_class(r.meta.get("status"))}">{esc(r.meta.get("status", "-"))}</td>'
                    f'<td class="{status_class(o.status if o else None)}">{esc(o.status if o else "-")}</td>'
                    f'<td class="{status_class(rep.status if rep else None)}">{esc(rep.status if rep else "-")}</td>'
                    f'<td>{esc(rep.headline if rep else (r.error or ""))}</td>'
                    f'<td>{esc(o.rounds_used if o else "-")}/{esc(o.tool_calls_used if o else "-")}</td>'
                    f'<td>{esc(o.wall_seconds if o else "-")}</td></tr>')
    any_stub = any(r.stub for r in runs)
    body = (f"<h1>Saved runs: {esc(batch)}</h1><p class='muted'>Source: {esc(src)} &middot; {len(runs)} runs &middot; "
            f"read-only view; nothing here was re-run.</p>"
            f'<div class="panel scroll"><table><tr><th>run</th><th>system</th><th>ablation</th><th>harness</th>'
            f'<th>output</th><th>report</th><th>headline</th><th>rounds/calls</th><th>wall s</th></tr>'
            f'{"".join(rows)}</table></div>')
    return page(f"Runs {batch}", body, any_stub)


def build(runs_dir: Path, out_dir: Path) -> list[Run]:
    runs = [Run(d) for d in find_runs(runs_dir)]
    out_dir.mkdir(parents=True, exist_ok=True)
    batch = runs_dir.name
    for r in runs:
        (out_dir / r.page).write_text(render_run(r, batch), encoding="utf-8")
    (out_dir / "index.html").write_text(render_index(runs, batch, runs_dir), encoding="utf-8")
    return runs


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m viewer.build")
    ap.add_argument("--runs", required=True, help="a batch directory (runs/<batch>) or one run directory")
    ap.add_argument("--out", required=True, help="output directory, e.g. viewer/out/<batch>")
    a = ap.parse_args(argv)
    src = Path(a.runs)
    if not src.is_dir():
        print(f"not a directory: {src}", file=sys.stderr)
        return 2
    runs = build(src, Path(a.out))
    if not runs:
        print(f"no run directories (output.json/meta.json) under {src}", file=sys.stderr)
        return 1
    print(f"wrote {len(runs)} run pages + index.html to {a.out}"
          + (" (STUB runs marked)" if any(r.stub for r in runs) else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
