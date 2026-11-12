"""Metric arithmetic on small hand-built objects. Never touches runs/ or results/."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blackbox.schemas import (AnswerKey, Claim, EvidenceBudget, EvidenceRecord,  # noqa: E402
                              FinalReport, InvestigationStep, RankedCause, RunConfig,
                              SystemOutput, ToolRequest, ToolResult, Usage)
from eval.metrics import (V_INVALID_ONLY, V_NO_CITATION, V_PENDING, V_TENTATIVE,  # noqa: E402
                          aggregate_system, classify_claims, limit_exceedances, ratio,
                          recomputed_invalid_citations, score_run, tool_calls)


def rec(i):
    return EvidenceRecord(evidence_id=i, source="logs", content=f"text {i}")


def key(**kw):
    base = dict(incident_id="INC-1", scenario_number=1, scenario_type="misleading_symptom",
                is_primary=True, diagnosable=True, expected_status="diagnosis",
                component="payment-svc", mechanism="connection pool exhaustion",
                decisive_evidence_ids=["log:a", "log:d"])
    base.update(kw)
    return AnswerKey(**base)


def step(round_, calls):
    """calls: list of (call_id, tool, args, status, ids, is_retry)"""
    return InvestigationStep(
        round=round_,
        tool_requests=[ToolRequest(call_id=c, round=round_, tool=t, args=a)
                       for c, t, a, *_ in calls],
        tool_results=[ToolResult(call_id=c, tool=t, status=s, records=[rec(i) for i in ids],
                                 is_retry=rt) for c, t, a, s, ids, rt in calls])


def bb_output(status="diagnosis", causes=(("payment-svc", "connection pool exhaustion"),),
              claims=None, steps=None, seen=("log:a", "log:b", "log:c")):
    rep = FinalReport(incident_id="INC-1", system="blackbox", status=status, headline="h",
                      ranked_causes=[RankedCause(rank=i + 1, component=c, mechanism=m,
                                                 evidence_ids=["log:a"])
                                     for i, (c, m) in enumerate(causes)],
                      claims=claims if claims is not None else [])
    return SystemOutput(system="blackbox", incident_id="INC-1", status="completed", report=rep,
                        steps=steps or [], evidence_seen=[rec(i) for i in seen],
                        usage=Usage(prompt_tokens=100, completion_tokens=20), rounds_used=2)


def test_ratio_zero_denominator_is_none():
    assert ratio(0, 0) is None
    assert ratio(1, 4) == 0.25


def test_claim_classification_and_rate():
    claims = [Claim(claim_id="1", text="a", kind="factual", evidence_ids=["log:a"]),
              Claim(claim_id="2", text="b", kind="causal"),
              Claim(claim_id="3", text="c", kind="factual", evidence_ids=["log:zzz"]),
              Claim(claim_id="4", text="d", kind="causal", evidence_ids=["log:b", "log:zzz"]),
              Claim(claim_id="5", text="e", kind="tentative")]
    out = bb_output(claims=claims)
    v = [c.verdict for c in classify_claims(out)]
    assert v == [V_PENDING, V_NO_CITATION, V_INVALID_ONLY, V_PENDING, V_TENTATIVE]
    row = score_run(out, key(), run_status="completed", system="blackbox")
    assert row["claims_asserted"] == 4
    assert row["claims_tentative"] == 1
    assert row["claims_unsupported_auto"] == 2
    assert row["unsupported_rate_auto"] == 0.5
    assert row["claims_pending_review"] == 2
    assert recomputed_invalid_citations(out) == ["log:zzz"]
    assert row["invalid_citations_recomputed"] == 1


def test_no_asserted_claims_rate_is_na():
    out = bb_output(claims=[Claim(claim_id="1", text="t", kind="tentative")])
    row = score_run(out, key(), run_status="completed", system="blackbox")
    assert row["unsupported_rate_auto"] is None


def test_baseline_supplied_evidence_is_included_ids():
    rep = FinalReport(incident_id="INC-1", system="b1", status="diagnosis", headline="h",
                      claims=[Claim(claim_id="1", text="x", kind="factual", evidence_ids=["log:c"])])
    out = SystemOutput(system="b1", incident_id="INC-1", status="completed", report=rep,
                       evidence_seen=[rec("log:a"), rec("log:c")],
                       evidence_budget=EvidenceBudget(context_tokens=8192, reserved_tokens=2400,
                                                      evidence_token_budget=5000,
                                                      evidence_tokens_total=9000,
                                                      included_ids=["log:a"], omitted_ids=["log:c"],
                                                      omitted_tokens=4000, matched=False))
    row = score_run(out, key(), run_status="completed", system="b1")
    # log:c was omitted from the prompt, so citing it is unsupported
    assert row["claims_unsupported_auto"] == 1
    assert row["decisive_retrieved"] == 1 and row["decisive_total"] == 2
    assert row["decisive_coverage"] == 0.5
    assert row["b1_matched"] is False
    assert row["supplied_source"] == "evidence_budget.included_ids"


def test_redundancy_failed_calls_first_decisive():
    steps = [
        step(1, [("t1", "search_logs", {"service": "p"}, "ok", ["log:b"], False),
                 ("t2", "get_metrics", {"service": "p"}, "error", [], False)]),
        step(2, [("t3", "get_metrics", {"service": "p"}, "ok", ["log:c"], True),     # retry
                 ("t4", "search_logs", {"service": "p"}, "ok", ["log:b"], False),    # redundant
                 ("t5", "search_logs", {"service": "p"}, "ok", ["log:a"], False)]),  # same args, new id
        step(3, [("t6", "get_build", {"build_id": "x"}, "denied", [], False)]),
    ]
    out = bb_output(steps=steps)
    calls, refused = tool_calls(out)
    assert [c.redundant for c in calls] == [False, False, False, True, False]
    assert refused == 1
    row = score_run(out, key(), run_status="completed", system="blackbox")
    assert row["tool_calls_accepted"] == 5
    assert row["redundant_calls"] == 1
    assert row["redundant_rate"] == 0.2
    assert row["failed_calls"] == 1
    assert row["retry_calls"] == 1
    assert row["calls_to_first_decisive"] == 5
    assert row["tool_calls_refused"] == 1


def test_retry_is_never_redundant():
    steps = [step(1, [("t1", "search_logs", {}, "error", [], False),
                      ("t2", "search_logs", {}, "error", [], True)])]
    calls, _ = tool_calls(bb_output(steps=steps))
    assert [c.redundant for c in calls] == [False, False]


def test_limit_exceedances():
    steps = [step(1, [(f"t{i}", "search_logs", {"q": i}, "ok", [], False) for i in range(4)])]
    out = bb_output(steps=steps)
    out.config = RunConfig(max_calls_per_round=3)
    assert any("round 1: 4 calls" in m for m in limit_exceedances(out))


def test_top_k_and_abstention_fields():
    k = key()
    hit2 = bb_output(causes=[("checkout", "timeout"), ("payment-svc", "connection pool exhaustion")])
    r = score_run(hit2, k, run_status="completed", system="blackbox")
    assert (r["top1_auto"], r["top3_auto"], r["match_rank_auto"]) == (False, True, 2)
    ab = bb_output(status="insufficient_evidence", causes=())
    r = score_run(ab, k, run_status="completed", system="blackbox")
    assert r["top1_auto"] is False and r["abstained"] is True
    failed = score_run(None, k, run_status="exception", system="blackbox")
    assert failed["top1_auto"] is False and failed["top3_auto"] is False
    assert failed["abstained"] is False
    healthy = key(scenario_number=7, scenario_type="healthy", is_primary=False, diagnosable=False,
                  expected_status="no_fault_observed", component=None, mechanism=None,
                  decisive_evidence_ids=[])
    r = score_run(bb_output(status="no_fault_observed", causes=()), healthy,
                  run_status="completed", system="blackbox")
    assert r["top1_auto"] is None and r["outcome_correct_auto"] is True
    assert r["decisive_coverage"] is None


def _row(system, num, stype, primary, diag, **kw):
    base = dict(system=system, scenario_number=num, scenario_type=stype, is_primary=primary,
                diagnosable=diag, run_status="completed", usable_report=True, top1_auto=None,
                top3_auto=None, abstained=False, report_status="diagnosis",
                claims_asserted=0, claims_unsupported_auto=0, claims_tentative=0,
                claims_pending_review=0, decisive_coverage=None, decisive_retrieved=0,
                decisive_total=0, tool_calls_accepted=0, redundant_calls=0, failed_calls=0,
                budget_violations=0, unauthorized_attempted=0, unauthorized_executed=0,
                invalid_citations_recomputed=0, total_tokens=0, harness_wall_seconds=1.0,
                b1_matched=None)
    base.update(kw)
    return base


def test_aggregate_denominators():
    rows = [
        _row("blackbox", 1, "misleading_symptom", True, True, top1_auto=True, top3_auto=True,
             decisive_coverage=1.0, decisive_retrieved=2, decisive_total=2,
             claims_asserted=4, claims_unsupported_auto=1),
        _row("blackbox", 2, "misleading_symptom", True, True, top1_auto=False, top3_auto=True,
             decisive_coverage=0.5, decisive_retrieved=1, decisive_total=2),
        _row("blackbox", 3, "unrelated_change", True, True, top1_auto=False, top3_auto=False,
             abstained=True, report_status="insufficient_evidence", decisive_coverage=0.0,
             decisive_retrieved=0, decisive_total=1),
        # scenario 4 run missing entirely -> still in the denominator of 4
        _row("blackbox", 5, "insufficient_evidence", True, False, report_status="insufficient_evidence"),
        _row("blackbox", 6, "insufficient_evidence", True, False, report_status="diagnosis"),
        _row("blackbox", 7, "healthy", False, False, report_status="diagnosis",
             tool_calls_accepted=4, redundant_calls=1),
    ]
    a = aggregate_system(rows, n_diag_primary_planned=4)
    assert a["top1_accuracy_auto"] == 0.25
    assert a["top3_accuracy_auto"] == 0.5
    assert a["correct_abstention_rate"] == 0.5
    assert a["false_abstentions"] == 1 and abs(a["false_abstention_rate"] - 1 / 3) < 1e-9
    assert a["false_causal_rate"] == 1.0
    assert a["unsupported_rate_auto"] == 0.25
    assert a["decisive_coverage_mean"] == 0.5
    assert a["decisive_coverage_pooled"] == 0.6
    assert a["redundant_rate"] == 0.25


def test_aggregate_zero_denominators_are_na():
    a = aggregate_system([], n_diag_primary_planned=0)
    for k in ("top1_accuracy_auto", "correct_abstention_rate", "false_abstention_rate",
              "false_causal_rate", "unsupported_rate_auto", "decisive_coverage_mean",
              "redundant_rate"):
        assert a[k] is None, k


def test_decisive_equivalents_count_as_retrieved():
    """QA-F08: an equivalent line of the same decisive event counts as that decisive item."""
    k = key(decisive_evidence_ids=["log:first", "log:d"],
            injected_fault={"decisive_equivalents": {"log:first": ["log:later"]}})
    steps = [step(1, [("t1", "search_logs", {}, "ok", ["log:x"], False)]),
             step(2, [("t2", "search_logs", {"q": 1}, "ok", ["log:later"], False)])]
    out = bb_output(steps=steps, seen=("log:x", "log:later"))
    row = score_run(out, k, run_status="completed", system="blackbox")
    assert row["decisive_retrieved"] == 1 and row["decisive_coverage"] == 0.5
    assert row["calls_to_first_decisive"] == 2


def test_unavailable_calls_counted_apart():
    steps = [step(1, [("t1", "search_tickets", {}, "unavailable", [], False)])]
    row = score_run(bb_output(steps=steps), key(), run_status="completed", system="blackbox")
    assert row["unavailable_calls"] == 1 and row["failed_calls"] == 0
