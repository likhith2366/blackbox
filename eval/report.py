"""Reports for a scored batch (re-scores first so tables never go stale).

    python -m eval.report --batch <name>           # -> results/<name>/
    python -m eval.report --batch <name> --stub    # dev only -> runs/_dev_stub/<name>/_results/

Outputs: report.md (summary), per_case.{md,csv}, repeats.md, ablations.md,
b1_same_evidence.md, failure_catalogue.{md,csv}, hypothesis_traces/<run_id>.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from eval.common import load_batch_runs, read_json, resolve_batch_dir, results_dir_for
from eval.matching import tokens
from eval.metrics import fmt, tool_calls
from eval.score import score_batch
from eval.worksheets import write_csv

STUB_BANNER = ("> **STUB / DEVELOPMENT OUTPUT. Produced by the labelled stub system, not by "
               "BlackBox, B0 or B1. Not a result; never cite it.**\n")
PROV = "(provisional, automated)"

CASE_COLS = ["run_id", "scenario_number", "scenario_type", "incident_id", "system", "ablation",
             "repeat", "run_status", "expected_status", "report_status", "top1_auto", "top3_auto",
             "outcome_correct_auto", "claims_asserted", "claims_unsupported_auto",
             "unsupported_rate_auto", "claims_tentative", "decisive_coverage",
             "tool_calls_requested", "tool_calls_accepted", "backend_executions", "cache_hits",
             "tool_calls_refused", "redundant_calls", "failed_calls",
             "calls_to_first_decisive", "rounds_used", "total_tokens", "harness_wall_seconds",
             "budget_violations", "unauthorized_attempted", "unauthorized_executed",
             "invalid_citations_recomputed",
             "ledger_cit_n", "ledger_cit_exact", "ledger_cit_embedded", "ledger_cit_suffix",
             "ledger_cit_ambiguous", "ledger_cit_invalid",
             "report_cit_n", "report_cit_exact", "report_cit_embedded", "report_cit_suffix",
             "report_cit_ambiguous", "report_cit_invalid",
             "max_prompt_tokens_actual", "prompt_truncated_reported", "prompt_truncation_est",
             "notebook_omitted_max", "omitted_ids", "orchestration_events", "b1_matched"]

# compact columns for markdown tables (the CSV carries everything)
CASE_MD_COLS = ["run_id", "scenario_number", "system", "ablation", "repeat", "run_status",
                "expected_status", "report_status", "top1_auto", "top3_auto",
                "claims_asserted", "claims_unsupported_auto", "decisive_coverage",
                "tool_calls_accepted", "cache_hits", "redundant_calls", "failed_calls",
                "total_tokens", "harness_wall_seconds", "b1_matched"]


def md_table(rows: list[dict], cols: list[str], headers: Optional[list[str]] = None) -> str:
    if not rows:
        return "_none_\n"
    h = headers or cols
    out = ["| " + " | ".join(h) + " |", "|" + "---|" * len(h)]
    for r in rows:
        out.append("| " + " | ".join(fmt(r.get(c)).replace("|", "/").replace("\n", " ")
                                      for c in cols) + " |")
    return "\n".join(out) + "\n"


def _sort_key(r: dict):
    sysorder = {"blackbox": 0, "b1": 1, "b0": 2}
    return (r.get("scenario_number") if r.get("scenario_number") is not None else 99,
            r["incident_id"], sysorder.get(r["system"], 9), r["ablation"] != "none",
            r["ablation"], r["repeat"])


def _pair(r: dict) -> Optional[tuple]:
    if r.get("top1_component") is None:
        return None
    return (" ".join(tokens(r["top1_component"])), " ".join(tokens(r["top1_mechanism"])))


# --------------------------------------------------------------------------- sections
def repeats_section(rows: list[dict]) -> tuple[str, dict]:
    diag = sorted({r["incident_id"] for r in rows if r["is_primary"] and r["diagnosable"]
                   and r["planned"]})
    bb = [r for r in rows if r["system"] == "blackbox" and r["ablation"] == "none"]
    by = {(r["incident_id"], r["repeat"]): r for r in bb}
    repeats = sorted({r["repeat"] for r in bb if r["incident_id"] in diag})
    L = ["# BlackBox repeat-run agreement", "",
         f"Diagnosable primary cases: {len(diag)}. Passes found: {repeats or 'none'}. "
         f"Correctness is {PROV}; agreement compares normalised top-1 component/mechanism text "
         "and report status.", ""]
    trows = []
    for iid in diag:
        rs = [by.get((iid, k)) for k in repeats]
        present = [r for r in rs if r is not None]
        statuses = [r["report_status"] or r["run_status"] for r in present]
        pairs = [_pair(r) for r in present]
        trows.append({
            "incident_id": iid,
            "passes": len(present),
            "statuses": ", ".join(fmt(s) for s in statuses),
            "status_agree": len(set(statuses)) == 1 if len(present) > 1 else None,
            "top1_agree": (len(set(pairs)) == 1) if len(present) > 1 else None,
            "top1_correct_per_pass": ", ".join(fmt(r["top1_auto"]) if r else "not_run" for r in rs),
        })
    L.append(md_table(trows, list(trows[0].keys()) if trows else ["incident_id"]))
    acc = {}
    for k in repeats:
        hits = sum(bool(by[(i, k)]["top1_auto"]) for i in diag if (i, k) in by)
        acc[k] = hits / len(diag) if diag else None
    vals = [v for v in acc.values() if v is not None]
    spread = {"per_pass_top1_accuracy": acc,
              "min": min(vals) if vals else None, "max": max(vals) if vals else None}
    L += ["", f"Top-1 accuracy per pass {PROV}: "
          + (", ".join(f"pass {k}: {fmt(v)}" for k, v in acc.items()) or "N/A"),
          f"Min / max across passes: {fmt(spread['min'])} / {fmt(spread['max'])}", "",
          "Note: all passes use identical decoding settings (temperature and seed from the "
          "frozen RunConfig), so disagreement measures serving-stack nondeterminism, not "
          "sampling variance. Three passes on four cases is exploratory.", ""]
    return "\n".join(L), spread


def ablations_section(rows: list[dict]) -> str:
    diag = sorted({r["incident_id"] for r in rows if r["is_primary"] and r["diagnosable"]
                   and r["planned"]})
    bb = [r for r in rows if r["system"] == "blackbox" and r["repeat"] == 0]
    by = {(r["incident_id"], r["ablation"]): r for r in bb}
    abls = ["none"] + sorted({r["ablation"] for r in bb if r["ablation"] != "none"})
    L = ["# Source ablations vs full evidence (BlackBox, single runs, exploratory)", "",
         f"Accuracy and claim support are {PROV}.", ""]
    cols = ["incident_id", "ablation", "run_status", "report_status", "top1_auto", "top3_auto",
            "claims_asserted", "claims_unsupported_auto", "decisive_coverage",
            "tool_calls_accepted", "failed_calls", "total_tokens"]
    trows = []
    for iid in diag:
        for a in abls:
            r = by.get((iid, a))
            trows.append(r if r else {"incident_id": iid, "ablation": a, "run_status": "not_run"})
    L.append(md_table(trows, cols))
    L += ["", "## Aggregate over diagnosable primary cases", ""]
    agg = []
    for a in abls:
        rs = [by[(i, a)] for i in diag if (i, a) in by]
        agg.append({
            "ablation": a, "runs": len(rs),
            "top1_auto": f"{sum(bool(r['top1_auto']) for r in rs)}/{len(diag)}",
            "top3_auto": f"{sum(bool(r['top3_auto']) for r in rs)}/{len(diag)}",
            "claims_asserted": sum(r["claims_asserted"] or 0 for r in rs),
            "unsupported_auto": sum(r["claims_unsupported_auto"] or 0 for r in rs),
            "tool_calls": sum(r["tool_calls_accepted"] or 0 for r in rs),
        })
    L.append(md_table(agg, list(agg[0].keys()) if agg else ["ablation"]))
    return "\n".join(L)


def b1_section(rows: list[dict]) -> str:
    main = [r for r in rows if r["ablation"] == "none" and r["repeat"] == 0]
    bb = {r["incident_id"]: r for r in main if r["system"] == "blackbox"}
    L = ["# B1 vs BlackBox on the same evidence", "",
         "Only B1 runs whose evidence fitted the prompt (`evidence_budget.matched == True`) "
         "and whose source BlackBox run completed with a usable report support same-evidence "
         "iteration claims. Other runs are listed but excluded. B1 tests reasoning on retrieved evidence, not retrieval or equal compute.", ""]
    trows = []
    for r in sorted((r for r in main if r["system"] == "b1"), key=_sort_key):
        b = bb.get(r["incident_id"], {})
        trows.append({"incident_id": r["incident_id"], "scenario": r["scenario_number"],
                      "b1_matched": r["b1_matched"], "omitted_ids": r.get("omitted_ids"),
                      "b1_source_status": r.get("b1_source_status"),
                      "included": ("yes" if r["b1_matched"] is True
                                   and r.get("b1_source_status") in (None, "completed")
                                   and b.get("usable_report") else "EXCLUDED"),
                      "b1_outcome_auto": r["outcome_correct_auto"],
                      "blackbox_outcome_auto": b.get("outcome_correct_auto"),
                      "b1_unsupported_auto": r["claims_unsupported_auto"],
                      "blackbox_unsupported_auto": b.get("claims_unsupported_auto")})
    L.append(md_table(trows, list(trows[0].keys()) if trows else ["incident_id"]))
    return "\n".join(L)


def hypothesis_trace(rec) -> str:
    out = rec.output
    L = [f"# Hypothesis trace: {rec.run_id}", "",
         f"Incident `{rec.incident_id}`, ablation `{rec.ablation}`, repeat {rec.repeat}, "
         f"harness status `{rec.run_status}`.",
         "Private reasoning text (raw_output) is not reproduced or scored.", ""]
    if out is None:
        L.append(f"_No output: {rec.output_error or rec.meta.get('exception')}_")
        return "\n".join(L)
    calls, _ = tool_calls(out)
    by_round = defaultdict(list)
    for c in calls:
        by_round[c.round].append(c)
    for s in out.steps:
        L += [f"## Round {s.round}", "",
              f"- Parsed: {s.parse_ok}" + (f" ({s.parse_error})" if s.parse_error else ""),
              f"- Model decision: {s.model_decision or '-'}"]
        for req in s.tool_requests:
            res = next((x for x in s.tool_results if x.call_id == req.call_id), None)
            cr = next((c for c in by_round[s.round] if c.tool == req.tool and c.args == req.args), None)
            L.append(f"- Tool `{req.tool}` {json.dumps(req.args, default=str)} -> "
                     f"{res.status if res else 'no result'}"
                     + (f", {len(res.records)} records" if res else "")
                     + (f", {len(cr.new_ids)} new" if cr else "")
                     + (" [REDUNDANT]" if cr and cr.redundant else "")
                     + (" [retry]" if res and res.is_retry else ""))
        if s.hypothesis_changes:
            L.append("- Hypothesis changes:")
            L += [f"    - {h}" for h in s.hypothesis_changes]
        if s.hypotheses_after:
            L += ["", "| id | component | mechanism | status | conf | supporting | contradicting |",
                  "|---|---|---|---|---|---|---|"]
            for h in s.hypotheses_after:
                L.append(f"| {h.hypothesis_id} | {h.component} | {h.mechanism} | {h.status} | "
                         f"{h.confidence} | {', '.join(h.supporting)} | {', '.join(h.contradicting)} |")
            L.append("")
        contra = [h for h in s.hypotheses_after if h.contradicting]
        if contra:
            L.append("- Contradictions recorded: " + "; ".join(
                f"{h.hypothesis_id} vs {', '.join(h.contradicting)}" for h in contra))
        if s.invalid_citations:
            L.append(f"- Invalid citations this round: {', '.join(s.invalid_citations)}")
        L += [f"- Stop decision: `{s.stop_decision}`" + (f" - {s.stop_reason}" if s.stop_reason else ""), ""]
    if out.report:
        L += ["## Final", "", f"Status `{out.report.status}`: {out.report.headline}"]
    return "\n".join(L)


def failure_catalogue(rows: list[dict]) -> list[dict]:
    F = []

    def add(r, symptom, cause, evidence):
        F.append({"run_id": r["run_id"], "incident_id": r["incident_id"],
                  "scenario": r["scenario_number"], "system": r["system"],
                  "ablation": r["ablation"], "repeat": r["repeat"], "symptom": symptom,
                  "likely_cause (automated guess, needs review)": cause, "evidence": evidence})

    for r in sorted(rows, key=_sort_key):
        rd = (r.get("run_dir") or f"runs/<batch>/{r['run_id']}") + "/"
        st = r["run_status"]
        if st == "not_run":
            add(r, "planned run not executed", "batch incomplete or interrupted", "plan.json")
            continue
        if st == "skipped_dependency":
            add(r, "B1 not run", "initial BlackBox run produced no usable output",
                rd + "meta.json")
            continue
        if st in ("exception", "failed", "timeout") or not r["usable_report"]:
            add(r, f"run status {st}, no usable report",
                (r.get("exception") or "see output.error / meta.exception")[:200],
                rd + "meta.json, output.json")
        if r.get("parse_failures"):
            add(r, f"{r['parse_failures']} round(s) with unparseable model output",
                "model did not follow the response schema", rd + "output.json steps[].parse_error")
        if r.get("invalid_citations_recomputed"):
            add(r, f"{r['invalid_citations_recomputed']} cited id(s) not in supplied evidence",
                "model invented or padded citation ids (seen in smoke test, CONTRACT s1)",
                rd + "output.json report")
        if r.get("unauthorized_attempted"):
            sev = "EXECUTED" if r.get("unauthorized_executed") else "refused"
            cause = ("followed planted instruction" if r["scenario_type"] == "prompt_injection"
                     else "requested a non-allowlisted tool")
            add(r, f"{r['unauthorized_attempted']} unauthorized action attempt(s), {sev}",
                cause, rd + "output.json unauthorized_attempts")
        if r.get("budget_violations") or r.get("limit_exceedances"):
            add(r, f"budget violations {r.get('budget_violations')}; {r.get('limit_exceedances')}",
                "model kept requesting tools past the limit", rd + "output.json steps")
        if r["usable_report"] and r["expected_status"] == "diagnosis" and not r["top1_auto"]:
            if r["abstained"]:
                add(r, "false abstention on a diagnosable case " + PROV,
                    "insufficient confidence or decisive evidence missed "
                    f"(coverage {fmt(r['decisive_coverage'])})", rd + "worksheet")
            elif r["decisive_coverage"] is not None and r["decisive_coverage"] < 1:
                add(r, "top-1 diagnosis not matched " + PROV,
                    f"retrieval: decisive coverage {fmt(r['decisive_coverage'])}",
                    rd + "worksheet / hypothesis trace")
            else:
                add(r, "top-1 diagnosis not matched " + PROV,
                    "reasoning or matcher wording: decisive evidence was supplied",
                    rd + "worksheet (check accepted wordings)")
        if r["usable_report"] and r["scenario_type"] == "healthy" and r["report_status"] == "diagnosis":
            add(r, "false causal diagnosis on healthy run", "overconfident attribution",
                rd + "worksheet")
        if r["usable_report"] and r["expected_status"] == "insufficient_evidence" \
                and r["report_status"] != "insufficient_evidence":
            add(r, f"did not abstain on insufficient-evidence case (status {r['report_status']})",
                "overconfident diagnosis", rd + "worksheet")
        if r.get("headline_ok") is False:
            add(r, "insufficient_evidence without the required headline", "report formatting",
                rd + "output.json report.headline")
        if r.get("redundant_calls"):
            add(r, f"{r['redundant_calls']} redundant tool call(s)",
                "repeated identical request without new evidence", rd + "hypothesis trace")
        if r.get("b1_matched") is False:
            add(r, "B1 unmatched: evidence truncated to fit context",
                f"{r.get('omitted_ids')} ids / {r.get('omitted_tokens')} tokens omitted",
                rd + "output.json evidence_budget")
    return F


def summary_md(batch: str, s: dict, rows: list[dict], spread: dict, n_fail: int,
               batch_meta: dict, stub: bool) -> str:
    L = []
    if stub:
        L.append(STUB_BANNER)
    L += [f"# Batch `{batch}` summary", "",
          f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} from run "
          f"records on disk. Phase: `{s.get('phase')}`.", "",
          "## Completed vs planned", "",
          f"- Planned runs: {s['planned_runs']}; records on disk: {s['records_on_disk']}; "
          f"planned but not run: {s['not_run']}",
          f"- Harness status counts: {json.dumps(s['status_counts'])}",
          f"- Excluded stub records: {len(s['excluded_stub_records'])}",
          f"- Answer keys used: `{s.get('answer_keys_dir')}` "
          "(sha256 " + ", ".join(k + "=" + (v or "")[:12]
                                 for k, v in (s.get("answer_key_sha256") or {}).items()) + ")",
          ""]
    if batch_meta.get("model"):
        L.append(f"- Model: `{batch_meta.get('model')}` digest `{batch_meta.get('model_digest')}`; "
                 f"git `{batch_meta.get('git_head')}` dirty={batch_meta.get('git_dirty')}")
        L.append("")
    agg = s["main_pass_by_system"]
    L += ["## 1. Measured results (objective, from run records and answer-key IDs)", ""]
    mcols = ["n_runs", "failed_or_incomplete_runs", "tool_calls_accepted", "redundant_calls",
             "redundant_rate", "failed_calls", "budget_violations", "unauthorized_attempted",
             "unauthorized_executed", "invalid_citations_recomputed", "decisive_coverage_mean",
             "decisive_coverage_pooled", "total_tokens", "wall_seconds", "b1_matched", "b1_unmatched"]
    L.append(md_table([{"system": k, **v} for k, v in agg.items()], ["system"] + mcols))
    L += ["Abstention metrics below are read from report status against the answer key's "
          "expected status (objective given the key):", ""]
    acols = ["insufficient_cases", "correct_abstentions", "correct_abstention_rate",
             "false_abstentions", "false_abstention_rate", "healthy_cases",
             "false_causal_diagnoses", "false_causal_rate"]
    L.append(md_table([{"system": k, **v} for k, v in agg.items()], ["system"] + acols))
    L += ["", "## 2. Provisional automated judgments (need two-reviewer confirmation)", "",
          f"Primary denominator = {s['diagnosable_primary_cases_planned']} diagnosable primary "
          "cases; abstentions, failures and missing runs count as misses. Matching is lexical "
          "against accepted wordings. Unsupported-claim rate here is an automated LOWER BOUND "
          "(no citation, or only non-supplied citations); cited claims await semantic review.", ""]
    pcols = ["diag_primary_present", "top1_correct_auto", "top1_accuracy_auto",
             "top3_correct_auto", "top3_accuracy_auto", "claims_asserted",
             "claims_unsupported_auto", "unsupported_rate_auto", "claims_pending_review",
             "claims_tentative"]
    L.append(md_table([{"system": k, **v} for k, v in agg.items()], ["system"] + pcols))
    rob = [r for r in rows if r["ablation"] == "none" and r["repeat"] == 0
           and r["scenario_number"] in (7, 8, 9)]
    L += ["", "Robustness variants (reported separately, not in the primary denominator):", ""]
    L.append(md_table(sorted(rob, key=_sort_key),
                      ["scenario_number", "scenario_type", "system", "run_status", "expected_status",
                       "report_status", "outcome_correct_auto", "unauthorized_attempted",
                       "unauthorized_executed", "failed_calls"]))
    L += ["", f"BlackBox repeat top-1 accuracy min/max {PROV}: {fmt(spread.get('min'))} / "
          f"{fmt(spread.get('max'))}", "",
          f"Failure catalogue entries: {n_fail} (see failure_catalogue.md).", "",
          "## 3. Untested assumptions", "",
          "- Answer keys and accepted wordings are taken as correct; teammate review of each "
          "key is a separate step not verified by this code.",
          "- The lexical matcher may accept or reject equivalent wordings incorrectly; every "
          "diagnosis verdict is in worksheets/diagnosis_review.csv for human confirmation.",
          "- Claims citing supplied evidence are not assumed supported; no human review has "
          "been recorded (reviewer columns are blank).",
          "- Repeat runs use identical decoding settings; agreement reflects serving "
          "nondeterminism only.",
          "- Single-run baseline and ablation cells: differences are exploratory, not significant.",
          "- B1 controls for evidence, not compute; unmatched B1 runs are excluded from "
          "same-evidence claims.",
          "- Tickets and build history are team-authored fixtures, not live integrations.",
          "- Compound claims are not split automatically; reviewers split them before grading.",
          ""]
    return "\n".join(L)


def build_report(batch: str, stub: bool, answer_keys_dir: Optional[Path] = None) -> Path:
    batch_dir = resolve_batch_dir(batch, stub)
    out_dir = results_dir_for(batch, stub)
    s = score_batch(batch_dir, out_dir, stub_mode=stub, answer_keys_dir=answer_keys_dir)
    rows = read_json(out_dir / "per_run.json")
    batch_meta = read_json(batch_dir / "batch.json") if (batch_dir / "batch.json").exists() else {}
    banner = STUB_BANNER + "\n" if stub else ""

    srt = sorted(rows, key=_sort_key)
    pd.DataFrame(srt)[[c for c in CASE_COLS if c in (srt[0] if srt else {})]].to_csv(
        out_dir / "per_case.csv", index=False)
    (out_dir / "per_case.md").write_text(
        banner + "# Per-case comparison\n\nColumns ending `_auto` are provisional.\n\n"
        + md_table(srt, CASE_COLS), encoding="utf-8")
    rep_md, spread = repeats_section(rows)
    (out_dir / "repeats.md").write_text(banner + rep_md, encoding="utf-8")
    (out_dir / "ablations.md").write_text(banner + ablations_section(rows), encoding="utf-8")
    (out_dir / "b1_same_evidence.md").write_text(banner + b1_section(rows), encoding="utf-8")

    F = failure_catalogue(rows)
    fcols = list(F[0].keys()) if F else ["run_id", "symptom"]
    write_csv(out_dir / "failure_catalogue.csv", F, fcols)
    (out_dir / "failure_catalogue.md").write_text(
        banner + "# Failure catalogue\n\nEvery failed, incomplete or erroneous run is listed; "
        "none are dropped. `likely_cause` is an automated guess for reviewers.\n\n"
        + md_table(F, fcols), encoding="utf-8")

    tdir = out_dir / "hypothesis_traces"
    tdir.mkdir(parents=True, exist_ok=True)
    for rec in load_batch_runs(batch_dir):
        if rec.stub != stub or rec.system != "blackbox":
            continue
        (tdir / f"{rec.run_id}.md").write_text(banner + hypothesis_trace(rec), encoding="utf-8")

    (out_dir / "report.md").write_text(summary_md(batch, s, rows, spread, len(F), batch_meta, stub),
                                       encoding="utf-8")
    return out_dir


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.report")
    ap.add_argument("--batch", required=True)
    ap.add_argument("--stub", action="store_true", help="development only (runs/_dev_stub/)")
    ap.add_argument("--answer-keys", default=None,
                    help="directory with the answer keys as they were when the runs were made")
    a = ap.parse_args(argv)
    batch_dir = resolve_batch_dir(a.batch, a.stub)
    if not batch_dir.exists():
        print(f"batch directory not found: {batch_dir}", file=sys.stderr)
        return 2
    from eval.score import AnswerKeyMismatch
    try:
        out = build_report(a.batch, a.stub, Path(a.answer_keys) if a.answer_keys else None)
    except AnswerKeyMismatch as e:
        print(f"refusing: {e}", file=sys.stderr)
        return 2
    print(f"report written to {out}")
    if a.stub:
        print("STUB batch: development output only, not a result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
