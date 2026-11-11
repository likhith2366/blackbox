"""Human review worksheets (Milestone 2 s5 "Review and interpretation").

One Markdown worksheet and one claims CSV per run, plus batch-level CSVs. Reviewer
columns are always written BLANK: this code never fills a human verdict.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Optional

from blackbox.schemas import AnswerKey, SystemOutput

from eval.matching import accepted_components, accepted_mechanisms
from eval.metrics import classify_claims, evidence_text_index, fmt, supplied_ids

REVIEW_COLS = ["reviewer_A_verdict", "reviewer_A_notes", "reviewer_B_verdict",
               "reviewer_B_notes", "resolved_verdict"]
CLAIM_COLS = ["run_id", "incident_id", "system", "ablation", "repeat", "claim_id", "kind",
              "claim_text", "cited_ids", "cited_present", "cited_absent",
              "auto_verdict_provisional", "cited_evidence_text"] + REVIEW_COLS
DIAG_COLS = ["run_id", "incident_id", "scenario_number", "system", "ablation", "repeat",
             "run_status", "expected_status", "expected_component", "expected_mechanism",
             "report_status", "rank", "reported_component", "reported_mechanism",
             "auto_match_provisional"] + REVIEW_COLS

EVIDENCE_CHARS = 500


def _clip(s: str, n: int = EVIDENCE_CHARS) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n] + " [...]"


def claim_rows(run_id: str, info: dict, out: Optional[SystemOutput]) -> list[dict[str, Any]]:
    if out is None:
        return []
    texts = evidence_text_index(out)
    rows = []
    for v in classify_claims(out):
        ev = []
        for i in v.claim.evidence_ids:
            ev.append(f"[{i}] " + (_clip(texts[i]) if i in texts and i in v.cited_present
                                   else "(not in supplied evidence)"))
        rows.append({
            "run_id": run_id, "incident_id": info["incident_id"], "system": info["system"],
            "ablation": info["ablation"], "repeat": info["repeat"],
            "claim_id": v.claim.claim_id, "kind": v.claim.kind, "claim_text": v.claim.text,
            "cited_ids": " ".join(v.claim.evidence_ids),
            "cited_present": " ".join(v.cited_present), "cited_absent": " ".join(v.cited_absent),
            "auto_verdict_provisional": v.verdict, "cited_evidence_text": "\n".join(ev),
            **{c: "" for c in REVIEW_COLS},
        })
    return rows


def diagnosis_rows(run_id: str, info: dict, out: Optional[SystemOutput],
                   key: Optional[AnswerKey], row: dict) -> list[dict[str, Any]]:
    base = {
        "run_id": run_id, "incident_id": info["incident_id"],
        "scenario_number": info.get("scenario_number"), "system": info["system"],
        "ablation": info["ablation"], "repeat": info["repeat"], "run_status": row["run_status"],
        "expected_status": key.expected_status if key else "",
        "expected_component": " | ".join(accepted_components(key)) if key else "",
        "expected_mechanism": " | ".join(accepted_mechanisms(key)) if key else "",
        "report_status": row.get("report_status") or "",
        **{c: "" for c in REVIEW_COLS},
    }
    report = out.report if out is not None else None
    causes = sorted(report.ranked_causes, key=lambda c: c.rank)[:3] if report else []
    if not causes:
        return [{**base, "rank": "", "reported_component": "", "reported_mechanism": "",
                 "auto_match_provisional": fmt(row.get("outcome_correct_auto"))}]
    out_rows = []
    for i, c in enumerate(causes, start=1):
        m = row.get("match_rank_auto")
        auto = ("match" if m == i else "no match") if row.get("expected_status") == "diagnosis" \
            else f"n/a (expected {row.get('expected_status')})"
        out_rows.append({**base, "rank": c.rank, "reported_component": c.component,
                         "reported_mechanism": c.mechanism, "auto_match_provisional": auto})
    return out_rows


def write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def run_markdown(run_id: str, info: dict, out: Optional[SystemOutput],
                 key: Optional[AnswerKey], row: dict, claims: list[dict], stub: bool) -> str:
    L: list[str] = []
    if stub:
        L += ["> **STUB RUN - development only. Not a result. Do not review or report.**", ""]
    L += [f"# Review worksheet: {run_id}", "",
          f"- Incident: `{info['incident_id']}` (scenario {info.get('scenario_number')}, "
          f"{info.get('scenario_type')})",
          f"- System: `{info['system']}`  ablation: `{info['ablation']}`  repeat: {info['repeat']}",
          f"- Harness status: **{row['run_status']}**"
          + (f"  - exception: `{info.get('exception')}`" if info.get("exception") else ""),
          "", "All automated verdicts below are PROVISIONAL. Reviewer fields are intentionally "
          "blank; two reviewers fill them independently, then resolve disagreements.", "",
          "## Expected answer (evaluator-private)", ""]
    if key:
        L += [f"- Expected status: `{key.expected_status}`",
              f"- Component (accepted): {', '.join(accepted_components(key)) or '-'}",
              f"- Mechanism (accepted): {', '.join(accepted_mechanisms(key)) or '-'}",
              f"- Decisive evidence: {', '.join(key.decisive_evidence_ids) or '-'}",
              f"- Distractors: {', '.join(key.distractor_evidence_ids) or '-'}"]
        if key.remaining_evidence_sufficient is not None:
            L.append(f"- Remaining evidence sufficient (scenario 8): {key.remaining_evidence_sufficient}")
    else:
        L.append("- (no answer key found)")
    L += ["", "## System report", ""]
    report = out.report if out is not None else None
    if report is None:
        L.append(f"_No report._ {('Output error: ' + (out.error or '')) if out is not None else ''}")
    else:
        L += [f"- Status: `{report.status}`", f"- Headline: {report.headline}"]
        for c in sorted(report.ranked_causes, key=lambda c: c.rank):
            L.append(f"- Cause #{c.rank}: **{c.component}** / {c.mechanism} "
                     f"(confidence {c.confidence}; cites {', '.join(c.evidence_ids) or 'nothing'})")
        if report.unresolved_questions:
            L.append("- Unresolved: " + "; ".join(report.unresolved_questions))
        if report.next_check:
            L.append(f"- Next check: {report.next_check}")
        if report.suggested_fix:
            L.append(f"- Suggested fix (untested): {report.suggested_fix}")
        if report.invalid_citations:
            L.append(f"- System-reported invalid citations: {', '.join(report.invalid_citations)}")
    L += ["", "## Diagnosis verdict", "",
          f"- Automated (provisional): top-1 {fmt(row.get('top1_auto'))}, top-3 "
          f"{fmt(row.get('top3_auto'))}, outcome correct {fmt(row.get('outcome_correct_auto'))}",
          "- Reviewer A: ______  Reviewer B: ______  Resolved: ______", "",
          "## Claims", ""]
    if out is not None:
        src = supplied_ids(out)[1]
        L.append(f"Supplied evidence for support checks: `{src}` "
                 f"({row.get('supplied_count')} ids). Split compound claims before grading.")
        L.append("")
    if not claims:
        L.append("_No claims._")
    for c in claims:
        L += [f"### {c['claim_id']} ({c['kind']})", "", c["claim_text"], "",
              f"- Automated (provisional): {c['auto_verdict_provisional']}"]
        if c["cited_absent"]:
            L.append(f"- Cited but not supplied: {c['cited_absent']}")
        if c["cited_evidence_text"]:
            L.append("- Cited evidence:")
            for line in c["cited_evidence_text"].split("\n"):
                L.append(f"    - {line}")
        L += ["- Reviewer A (supported / partly / unsupported): ______ notes: ______",
              "- Reviewer B (supported / partly / unsupported): ______ notes: ______",
              "- Resolved: ______", ""]
    if out is not None and out.report is not None and out.report.citation_audit:
        L += ["## Report citation audit (objective: how each written citation resolved)", "",
              "| where | as written | resolved | method |", "|---|---|---|---|"]
        for a in out.report.citation_audit:
            L.append(f"| {a.where} | `{_clip(a.raw, 80)}` | {a.resolved or '-'} | {a.method} |")
        L.append("")
    L += ["## Trajectory (objective)", "",
          f"- Tool calls requested: {fmt(row.get('tool_calls_requested'))}; accepted (budget "
          f"consumed): {fmt(row.get('tool_calls_accepted'))} = backend executions "
          f"{fmt(row.get('backend_executions'))} + cache hits {fmt(row.get('cache_hits'))}; "
          f"refused: {fmt(row.get('tool_calls_refused'))}",
          f"- Redundant: {fmt(row.get('redundant_calls'))} (cache hits included); failed: "
          f"{fmt(row.get('failed_calls'))}",
          f"- Citations, ledger: {fmt(row.get('ledger_cit_n'))} (exact {fmt(row.get('ledger_cit_exact'))}, "
          f"recovered {fmt((row.get('ledger_cit_embedded') or 0) + (row.get('ledger_cit_suffix') or 0))}, "
          f"ambiguous {fmt(row.get('ledger_cit_ambiguous'))}, invalid {fmt(row.get('ledger_cit_invalid'))}); "
          f"report: {fmt(row.get('report_cit_n'))} (exact {fmt(row.get('report_cit_exact'))}, "
          f"recovered {fmt((row.get('report_cit_embedded') or 0) + (row.get('report_cit_suffix') or 0))}, "
          f"ambiguous {fmt(row.get('report_cit_ambiguous'))}, invalid {fmt(row.get('report_cit_invalid'))})",
          f"- Context: max prompt tokens {fmt(row.get('max_prompt_tokens_actual'))}; truncated by "
          f"server (reported) {fmt(row.get('prompt_truncated_reported'))}; truncation (evaluator "
          f"estimate) {fmt(row.get('prompt_truncation_est'))}; notebook records omitted (max) "
          f"{fmt(row.get('notebook_omitted_max'))}; baseline omitted ids {fmt(row.get('omitted_ids'))}",
          f"- Orchestration interventions (coordinator, not model): {fmt(row.get('orchestration_events'))}"
          f" (first-tool {fmt(row.get('orchestration_first_tool'))}, diagnosis gate "
          f"{fmt(row.get('orchestration_diagnosis_gate'))})",
          f"- Decisive coverage: {fmt(row.get('decisive_coverage'))} "
          f"({fmt(row.get('decisive_retrieved'))}/{fmt(row.get('decisive_total'))}); "
          f"calls to first decisive: {fmt(row.get('calls_to_first_decisive'))}",
          f"- Budget violations: {fmt(row.get('budget_violations'))}; unauthorized attempted/"
          f"executed: {fmt(row.get('unauthorized_attempted'))}/{fmt(row.get('unauthorized_executed'))}",
          f"- Tokens: {fmt(row.get('total_tokens'))}; wall s: {fmt(row.get('harness_wall_seconds'))}",
          ""]
    return "\n".join(L)
