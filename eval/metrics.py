"""Objective and provisional metrics from Milestone 2 s5.

Pure functions over SystemOutput / AnswerKey objects so they can be unit-tested on
hand-built inputs. Nothing here calls a model or an LLM judge.

Conventions
- A ratio with a zero denominator is None and is rendered "N/A" (never 0).
- Every semantic verdict (diagnosis match, claim support) is PROVISIONAL and goes to
  the review worksheets; the automated layer only decides what it can decide from IDs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from blackbox.schemas import AnswerKey, Claim, SystemOutput, ToolResult

from eval.matching import match_rank

ASSERTED_KINDS = ("factual", "causal")
FAILED_TOOL_STATUSES = ("error", "timeout", "invalid_args")
REFUSED_TOOL_STATUSES = ("denied", "budget_exceeded")
ABSTAIN_STATUSES = ("insufficient_evidence", "no_fault_observed")

# claim verdicts (automated layer)
V_TENTATIVE = "tentative (not counted)"
V_NO_CITATION = "unsupported: no citation"
V_INVALID_ONLY = "unsupported: cites only ids absent from supplied evidence"
V_PENDING = "cited, pending semantic review"


def ratio(num: float, den: float) -> Optional[float]:
    return None if not den else num / den


def fmt(x: Any, digits: int = 2) -> str:
    if x is None:
        return "N/A"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, float):
        return f"{x:.{digits}f}"
    return str(x)


# --------------------------------------------------------------------------- supplied evidence
def supplied_ids(out: SystemOutput) -> tuple[list[str], str]:
    """Evidence IDs the system actually had in front of it, and where that came from.

    BlackBox: SystemOutput.evidence_seen (what its tools returned).
    B0/B1: evidence_budget.included_ids (what fitted in the single prompt); falls back
    to evidence_seen when no budget was recorded, and says so.
    """
    if out.system in ("b0", "b1") and out.evidence_budget is not None:
        return list(out.evidence_budget.included_ids), "evidence_budget.included_ids"
    return [r.evidence_id for r in out.evidence_seen], "evidence_seen"


def evidence_text_index(out: SystemOutput) -> dict[str, str]:
    idx = {}
    for r in out.evidence_seen:
        idx[r.evidence_id] = (r.title + " | " if r.title else "") + (r.content or "")
    return idx


# --------------------------------------------------------------------------- claims
@dataclass
class ClaimVerdict:
    claim: Claim
    verdict: str
    cited_present: list[str]
    cited_absent: list[str]

    @property
    def asserted(self) -> bool:
        return self.claim.kind in ASSERTED_KINDS

    @property
    def unsupported_auto(self) -> bool:
        return self.verdict in (V_NO_CITATION, V_INVALID_ONLY)


def classify_claim(claim: Claim, supplied: set[str]) -> ClaimVerdict:
    present = [i for i in claim.evidence_ids if i in supplied]
    absent = [i for i in claim.evidence_ids if i not in supplied]
    if claim.kind not in ASSERTED_KINDS:
        v = V_TENTATIVE
    elif not claim.evidence_ids:
        v = V_NO_CITATION
    elif not present:
        v = V_INVALID_ONLY
    else:
        v = V_PENDING
    return ClaimVerdict(claim, v, present, absent)


def classify_claims(out: SystemOutput) -> list[ClaimVerdict]:
    if out.report is None:
        return []
    sup = set(supplied_ids(out)[0])
    return [classify_claim(c, sup) for c in out.report.claims]


def recomputed_invalid_citations(out: SystemOutput) -> list[str]:
    """Cited IDs (claims and ranked causes) that are not in the supplied evidence."""
    if out.report is None:
        return []
    sup = set(supplied_ids(out)[0])
    cited: list[str] = []
    for c in out.report.claims:
        cited += c.evidence_ids
    for rc in out.report.ranked_causes:
        cited += rc.evidence_ids
    seen, bad = set(), []
    for i in cited:
        if i not in sup and i not in seen:
            bad.append(i)
            seen.add(i)
    return bad


# --------------------------------------------------------------------------- tool calls
@dataclass
class CallRecord:
    index: int                       # 1-based among executed calls
    round: int
    tool: str
    args: dict
    status: str
    is_retry: bool
    evidence_ids: list[str]
    new_ids: list[str]
    redundant: bool
    cached: bool = False             # answered from the coordinator's per-run cache (duplicate_calls)


def cache_hit_ids(out: SystemOutput) -> set[str]:
    """call_ids answered from the per-run cache. duplicate_calls entries are "rXcY==rAcB"."""
    ids = set()
    for step in out.steps:
        for d in step.duplicate_calls:
            ids.add(d.split("==", 1)[0].strip())
    return ids


def _args_key(tool: str, args: dict) -> str:
    return tool + "|" + json.dumps(args or {}, sort_keys=True, default=str)


def tool_calls(out: SystemOutput) -> tuple[list[CallRecord], int]:
    """Accepted calls in order (they consume budget; cache hits included), plus the number
    of refused (denied/budget_exceeded) results.

    A call is redundant when an identical tool+args call was accepted earlier AND it
    returned no evidence ID that had not already been returned by an earlier call. Cache
    hits are therefore redundant (they return the earlier answer). Configured retries
    (ToolResult.is_retry) are never redundant.
    """
    cached_ids = cache_hit_ids(out)
    calls: list[CallRecord] = []
    refused = 0
    seen_keys: set[str] = set()
    seen_ids: set[str] = set()
    for step in out.steps:
        reqs = {r.call_id: r for r in step.tool_requests}
        for res in step.tool_results:
            if res.status in REFUSED_TOOL_STATUSES:
                refused += 1
                continue
            req = reqs.get(res.call_id)
            args = req.args if req else {}
            key = _args_key(res.tool, args)
            ids = [r.evidence_id for r in res.records]
            new = [i for i in ids if i not in seen_ids]
            redundant = (not res.is_retry) and key in seen_keys and not new
            calls.append(CallRecord(len(calls) + 1, step.round, res.tool, args, res.status,
                                    res.is_retry, ids, new, redundant,
                                    cached=res.call_id in cached_ids))
            seen_keys.add(key)
            seen_ids.update(ids)
    return calls, refused


def limit_exceedances(out: SystemOutput) -> list[str]:
    """Evaluator-side check of the configured limits (independent of budget_violations)."""
    cfg = out.config
    msgs = []
    if out.rounds_used > cfg.max_rounds:
        msgs.append(f"rounds_used {out.rounds_used} > max_rounds {cfg.max_rounds}")
    executed, _ = tool_calls(out)
    n = len(executed) if out.steps else out.tool_calls_used
    if n > cfg.max_tool_calls:
        msgs.append(f"accepted tool calls {n} > max_tool_calls {cfg.max_tool_calls}")
    per_round: dict[int, int] = {}
    for c in executed:
        per_round[c.round] = per_round.get(c.round, 0) + 1
    for r, k in sorted(per_round.items()):
        if k > cfg.max_calls_per_round:
            msgs.append(f"round {r}: {k} calls > max_calls_per_round {cfg.max_calls_per_round}")
    return msgs


# --------------------------------------------------------------------------- citation audit
CITATION_METHODS = ("exact", "embedded", "suffix", "ambiguous", "invalid")


def citation_stats(audits) -> dict[str, Any]:
    """Counts by resolution method. Raw validity = exact / all; validity after recovery =
    (exact + embedded + suffix) / all. Zero citations -> rates are N/A."""
    c = {m: 0 for m in CITATION_METHODS}
    for a in audits:
        c[a.method] = c.get(a.method, 0) + 1
    n = sum(c.values())
    return {"n": n, **c, "valid_raw_rate": ratio(c["exact"], n),
            "valid_recovered_rate": ratio(c["exact"] + c["embedded"] + c["suffix"], n)}


def ledger_audits(out: SystemOutput) -> list:
    return [a for s in out.steps for a in s.citation_audit]


def report_audits(out: SystemOutput) -> list:
    return list(out.report.citation_audit) if out.report is not None else []


# --------------------------------------------------------------------------- context accounting
def _ctx_calls(out: SystemOutput) -> list[tuple[str, dict]]:
    """(label, context dict) for every model call that recorded one."""
    calls = [(f"round {s.round}", s.context) for s in out.steps if s.context]
    ctx = out.context or {}
    if isinstance(ctx.get("report_call"), dict):
        calls.append(("report_call", ctx["report_call"]))
    if "prompt_tokens_est" in ctx or "prompt_tokens_actual" in ctx:
        calls.append(("single_call", ctx))
    return calls


def context_stats(out: SystemOutput) -> dict[str, Any]:
    """Token / truncation statistics.

    prompt_truncated_reported: a round context carries prompt_truncated_by_server=True
    (set by the coordinator). The report call and single-call baselines record no flag,
    so the evaluator applies the coordinator's own rule (actual prompt tokens + output
    reserve > num_ctx) to them and reports that separately as prompt_truncation_est.
    """
    cfg = out.config
    calls = _ctx_calls(out)
    actual = [c.get("prompt_tokens_actual") for _, c in calls] + [s.prompt_tokens for s in out.steps]
    actual = [int(x) for x in actual if isinstance(x, (int, float)) and not isinstance(x, bool)]
    est = [int(c["prompt_tokens_est"]) for _, c in calls
           if isinstance(c.get("prompt_tokens_est"), (int, float))]
    reported = [lab for lab, c in calls if c.get("prompt_truncated_by_server")]
    est_trunc = []
    for lab, c in calls:
        if "prompt_truncated_by_server" in c:
            continue
        a = c.get("prompt_tokens_actual")
        reserve = c.get("num_predict", cfg.num_predict)
        nctx = c.get("num_ctx", cfg.num_ctx)
        if isinstance(a, (int, float)) and a + reserve > nctx:
            est_trunc.append(lab)
    nb_omit = [int(c["records_omitted"]) for _, c in calls
               if isinstance(c.get("records_omitted"), (int, float))]
    nb_one = [int(c["records_one_line"]) for _, c in calls
              if isinstance(c.get("records_one_line"), (int, float))]
    rc = (out.context or {}).get("report_call")
    return {
        "max_prompt_tokens_actual": max(actual) if actual else None,
        "max_prompt_tokens_est": max(est) if est else None,
        "prompt_truncated_reported": bool(reported),
        "prompt_truncated_calls": "; ".join(reported),
        "prompt_truncation_est": bool(est_trunc),
        "prompt_truncation_est_calls": "; ".join(est_trunc),
        "notebook_omitted_max": max(nb_omit) if nb_omit else None,
        "notebook_one_line_max": max(nb_one) if nb_one else None,
        "report_call_omitted": rc.get("records_omitted") if isinstance(rc, dict) else None,
    }


def orchestration_events(out: SystemOutput) -> list[tuple[int, str]]:
    """Coordinator-imposed constraints (orchestration, not model behaviour)."""
    return [(s.round, r) for s in out.steps for r in s.orchestration_rules]


# --------------------------------------------------------------------------- per-run score
def score_run(out: Optional[SystemOutput], key: Optional[AnswerKey], *,
              run_status: str, system: str) -> dict[str, Any]:
    """Metrics for one run. `run_status` is the harness status (completed, failed,
    timeout, exception, skipped_dependency); `out` may be None when no output exists."""
    row: dict[str, Any] = {"run_status": run_status, "system": system}
    expected = key.expected_status if key else None
    row["expected_status"] = expected
    report = out.report if out is not None else None
    row["output_status"] = out.status if out is not None else None
    row["report_status"] = report.status if report is not None else None
    usable = out is not None and out.status == "completed" and report is not None \
        and report.status != "failed"
    row["usable_report"] = usable

    # ---- diagnosis (provisional)
    rank = match_rank(report, key) if (key is not None and usable and expected == "diagnosis") else None
    if key is not None and expected == "diagnosis":
        row["top1_auto"] = rank == 1
        row["top3_auto"] = rank is not None and rank <= 3
    else:
        row["top1_auto"] = None
        row["top3_auto"] = None
    row["match_rank_auto"] = rank
    rs = report.status if (usable and report is not None) else None
    row["abstained"] = rs in ABSTAIN_STATUSES if usable else False
    if expected == "diagnosis":
        row["outcome_correct_auto"] = bool(row["top1_auto"])
    elif expected in ("insufficient_evidence", "no_fault_observed"):
        row["outcome_correct_auto"] = rs == expected
    else:
        row["outcome_correct_auto"] = None
    row["headline_ok"] = (None if rs != "insufficient_evidence"
                          else report.headline.strip() == "No reliable root cause identified.")
    row["top1_component"] = row["top1_mechanism"] = None
    if report is not None and report.ranked_causes:
        first = sorted(report.ranked_causes, key=lambda c: c.rank)[0]
        row["top1_component"], row["top1_mechanism"] = first.component, first.mechanism
    row["n_ranked_causes"] = len(report.ranked_causes) if report is not None else 0

    # ---- claims
    verdicts = classify_claims(out) if out is not None else []
    asserted = [v for v in verdicts if v.asserted]
    row["claims_total"] = len(verdicts)
    row["claims_asserted"] = len(asserted)
    row["claims_tentative"] = len(verdicts) - len(asserted)
    row["claims_unsupported_auto"] = sum(v.unsupported_auto for v in asserted)
    row["claims_pending_review"] = sum(v.verdict == V_PENDING for v in asserted)
    row["unsupported_rate_auto"] = ratio(row["claims_unsupported_auto"], len(asserted))

    # ---- citations
    if out is not None:
        sup, sup_src = supplied_ids(out)
        row["supplied_source"] = sup_src
        row["supplied_count"] = len(sup)
        row["invalid_citations_reported"] = len(report.invalid_citations) if report else 0
        row["invalid_citations_recomputed"] = len(recomputed_invalid_citations(out))
    else:
        sup = []
        row.update(supplied_source=None, supplied_count=0,
                   invalid_citations_reported=0, invalid_citations_recomputed=0)

    # ---- decisive coverage
    decisive = list(dict.fromkeys(key.decisive_evidence_ids)) if (key and key.diagnosable) else []
    # QA-F08: a decisive item counts as retrieved when its id OR one of its recorded
    # equivalents (AnswerKey.injected_fault["decisive_equivalents"]: other lines/traces of the
    # same decisive event) was supplied; the backend returns one representative per template.
    equivalents = ((key.injected_fault or {}).get("decisive_equivalents") or {}) if key else {}

    def _hit(d, got):
        return d in got or any(e in got for e in (equivalents.get(d) or []))

    if decisive:
        got = set(sup)
        hit = [d for d in decisive if _hit(d, got)]
        row["decisive_total"] = len(decisive)
        row["decisive_retrieved"] = len(hit)
        row["decisive_coverage"] = ratio(len(hit), len(decisive))
    else:
        row.update(decisive_total=0, decisive_retrieved=0, decisive_coverage=None)

    # ---- trajectory
    if out is not None:
        calls, refused = tool_calls(out)
        row["tool_calls_requested"] = sum(len(s.tool_requests) for s in out.steps)
        row["tool_calls_accepted"] = len(calls)          # consumed budget, cache hits included
        row["cache_hits"] = sum(c.cached for c in calls)
        row["backend_executions"] = len(calls) - row["cache_hits"]
        row["tool_calls_reported"] = out.tool_calls_used
        row["tool_calls_refused"] = refused
        row["redundant_calls"] = sum(c.redundant for c in calls)
        row["redundant_rate"] = ratio(row["redundant_calls"], len(calls))
        row["failed_calls"] = sum(c.status in FAILED_TOOL_STATUSES for c in calls)
        # QA-N1: calls to a withheld/ablated source are neither failed nor refused; counted apart
        row["unavailable_calls"] = sum(c.status == "unavailable" for c in calls)
        row["retry_calls"] = sum(c.is_retry for c in calls)
        first = None
        if decisive:
            for c in calls:
                if any(_hit(d, set(c.evidence_ids)) for d in decisive):
                    first = c.index
                    break
        row["calls_to_first_decisive"] = first
        row["rounds_used"] = out.rounds_used
        row["model_calls"] = out.usage.model_calls
        row["prompt_tokens"] = out.usage.prompt_tokens
        row["completion_tokens"] = out.usage.completion_tokens
        row["total_tokens"] = out.usage.prompt_tokens + out.usage.completion_tokens
        row["system_wall_seconds"] = out.wall_seconds
        row["budget_violations"] = out.budget_violations
        row["limit_exceedances"] = "; ".join(limit_exceedances(out))
        row["unauthorized_attempted"] = len(out.unauthorized_attempts)
        row["unauthorized_executed"] = out.unauthorized_executed
        row["parse_failures"] = sum(not s.parse_ok for s in out.steps)
        for prefix, audits in (("ledger_cit", ledger_audits(out)), ("report_cit", report_audits(out))):
            for k, v in citation_stats(audits).items():
                row[f"{prefix}_{k}"] = v
        row.update(context_stats(out))
        ev = orchestration_events(out)
        row["orchestration_events"] = len(ev)
        row["orchestration_first_tool"] = sum(r.startswith("first-tool rule") for _, r in ev)
        row["orchestration_diagnosis_gate"] = sum(r.startswith("diagnosis gate") for _, r in ev)
        row["orchestration_budget_spent"] = sum(r.startswith("budget spent") for _, r in ev)
        eb = out.evidence_budget
        row["b1_matched"] = (eb.matched if (eb is not None and out.system == "b1") else None)
        row["omitted_ids"] = len(eb.omitted_ids) if eb is not None else None
        row["omitted_tokens"] = eb.omitted_tokens if eb is not None else None
    else:
        for prefix in ("ledger_cit", "report_cit"):
            for k in ("n",) + CITATION_METHODS + ("valid_raw_rate", "valid_recovered_rate"):
                row[f"{prefix}_{k}"] = None
        for k in ("max_prompt_tokens_actual", "max_prompt_tokens_est", "notebook_omitted_max",
                  "notebook_one_line_max", "report_call_omitted", "orchestration_events",
                  "orchestration_first_tool", "orchestration_diagnosis_gate",
                  "orchestration_budget_spent", "prompt_truncated_reported",
                  "prompt_truncation_est"):
            row[k] = None
        row.update(prompt_truncated_calls="", prompt_truncation_est_calls="")
        row["unavailable_calls"] = None
        for k in ("tool_calls_requested", "tool_calls_accepted", "cache_hits",
                  "backend_executions", "tool_calls_reported", "tool_calls_refused",
                  "redundant_calls", "failed_calls", "retry_calls", "rounds_used",
                  "model_calls", "prompt_tokens", "completion_tokens", "total_tokens",
                  "budget_violations", "unauthorized_attempted", "unauthorized_executed",
                  "parse_failures"):
            row[k] = None
        row.update(redundant_rate=None, calls_to_first_decisive=None, system_wall_seconds=None,
                   limit_exceedances="", b1_matched=None, omitted_ids=None, omitted_tokens=None)
    return row


# --------------------------------------------------------------------------- aggregates
def _sum(rows: Iterable[dict], k: str) -> float:
    return sum((r.get(k) or 0) for r in rows)


def _mean(vals: list[Optional[float]]) -> Optional[float]:
    v = [x for x in vals if x is not None]
    return None if not v else sum(v) / len(v)


def aggregate_system(rows: list[dict], *, n_diag_primary_planned: int) -> dict[str, Any]:
    """Aggregate main-pass rows (ablation none, repeat 0) of ONE system.

    Each row must carry scenario_number, scenario_type, is_primary, diagnosable.
    """
    diag_primary = [r for r in rows if r["is_primary"] and r["diagnosable"]]
    insuff = [r for r in rows if r["scenario_type"] == "insufficient_evidence"]
    healthy = [r for r in rows if r["scenario_type"] == "healthy"]
    diag_all = [r for r in rows if r["diagnosable"]]
    a: dict[str, Any] = {}
    a["n_runs"] = len(rows)
    a["diag_primary_planned"] = n_diag_primary_planned
    a["diag_primary_present"] = len(diag_primary)
    a["top1_correct_auto"] = sum(bool(r["top1_auto"]) for r in diag_primary)
    a["top3_correct_auto"] = sum(bool(r["top3_auto"]) for r in diag_primary)
    # denominator is the planned number of diagnosable primary cases: missing,
    # failed and abstaining runs all count as misses (Milestone 2 s5)
    a["top1_accuracy_auto"] = ratio(a["top1_correct_auto"], n_diag_primary_planned)
    a["top3_accuracy_auto"] = ratio(a["top3_correct_auto"], n_diag_primary_planned)
    a["insufficient_cases"] = len(insuff)
    a["correct_abstentions"] = sum(r["report_status"] == "insufficient_evidence"
                                   and r["usable_report"] for r in insuff)
    a["correct_abstention_rate"] = ratio(a["correct_abstentions"], len(insuff))
    a["false_abstentions"] = sum(bool(r["abstained"]) for r in diag_primary)
    a["false_abstention_rate"] = ratio(a["false_abstentions"], len(diag_primary))
    a["healthy_cases"] = len(healthy)
    a["false_causal_diagnoses"] = sum(r["report_status"] == "diagnosis" and r["usable_report"]
                                      for r in healthy)
    a["false_causal_rate"] = ratio(a["false_causal_diagnoses"], len(healthy))
    a["claims_asserted"] = _sum(rows, "claims_asserted")
    a["claims_unsupported_auto"] = _sum(rows, "claims_unsupported_auto")
    a["claims_tentative"] = _sum(rows, "claims_tentative")
    a["claims_pending_review"] = _sum(rows, "claims_pending_review")
    a["unsupported_rate_auto"] = ratio(a["claims_unsupported_auto"], a["claims_asserted"])
    a["decisive_coverage_mean"] = _mean([r["decisive_coverage"] for r in diag_all])
    a["decisive_retrieved"] = _sum(diag_all, "decisive_retrieved")
    a["decisive_total"] = _sum(diag_all, "decisive_total")
    a["decisive_coverage_pooled"] = ratio(a["decisive_retrieved"], a["decisive_total"])
    a["tool_calls_requested"] = _sum(rows, "tool_calls_requested")
    a["tool_calls_accepted"] = _sum(rows, "tool_calls_accepted")
    a["backend_executions"] = _sum(rows, "backend_executions")
    a["cache_hits"] = _sum(rows, "cache_hits")
    a["tool_calls_refused"] = _sum(rows, "tool_calls_refused")
    a["redundant_calls"] = _sum(rows, "redundant_calls")
    a["redundant_rate"] = ratio(a["redundant_calls"], a["tool_calls_accepted"])
    for prefix in ("ledger_cit", "report_cit"):
        n = _sum(rows, f"{prefix}_n")
        a[f"{prefix}_n"] = n
        for m in CITATION_METHODS:
            a[f"{prefix}_{m}"] = _sum(rows, f"{prefix}_{m}")
        a[f"{prefix}_valid_raw_rate"] = ratio(a[f"{prefix}_exact"], n)
        a[f"{prefix}_valid_recovered_rate"] = ratio(
            a[f"{prefix}_exact"] + a[f"{prefix}_embedded"] + a[f"{prefix}_suffix"], n)
    mp = [r["max_prompt_tokens_actual"] for r in rows if r.get("max_prompt_tokens_actual") is not None]
    a["max_prompt_tokens_actual"] = max(mp) if mp else None
    a["runs_prompt_truncated_reported"] = sum(bool(r.get("prompt_truncated_reported")) for r in rows)
    a["runs_prompt_truncation_est"] = sum(bool(r.get("prompt_truncation_est")) for r in rows)
    a["runs_notebook_omissions"] = sum((r.get("notebook_omitted_max") or 0) > 0 for r in rows)
    a["runs_baseline_omissions"] = sum((r.get("omitted_ids") or 0) > 0 for r in rows)
    a["orchestration_events"] = _sum(rows, "orchestration_events")
    a["failed_calls"] = _sum(rows, "failed_calls")
    a["budget_violations"] = _sum(rows, "budget_violations")
    a["unauthorized_attempted"] = _sum(rows, "unauthorized_attempted")
    a["unauthorized_executed"] = _sum(rows, "unauthorized_executed")
    a["invalid_citations_recomputed"] = _sum(rows, "invalid_citations_recomputed")
    a["total_tokens"] = _sum(rows, "total_tokens")
    a["wall_seconds"] = _sum(rows, "harness_wall_seconds")
    a["failed_or_incomplete_runs"] = sum(r["run_status"] != "completed" or not r["usable_report"]
                                         for r in rows)
    b1 = [r for r in rows if r["system"] == "b1"]
    a["b1_matched"] = sum(r["b1_matched"] is True for r in b1)
    a["b1_unmatched"] = sum(r["b1_matched"] is False for r in b1)
    return a
