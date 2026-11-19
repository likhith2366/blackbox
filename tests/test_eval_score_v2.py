"""Scoring v2: decisive ids match only exactly or via an explicitly listed equivalent."""
import importlib.util
from pathlib import Path

from blackbox.schemas import (AnswerKey, Claim, EvidenceBudget, EvidenceRecord, FinalReport,
                              RankedCause, SystemOutput)

_spec = importlib.util.spec_from_file_location(
    "score_v2", Path(__file__).resolve().parents[1] / "scripts" / "score_v2.py")
sv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sv2)


def key(decisive=("log:aea2b46ce0", "commit:e7a0cc0"), eq=None, diagnosable=True):
    return AnswerKey(incident_id="INC-1", scenario_number=1, scenario_type="misleading_symptom",
                     is_primary=True, diagnosable=diagnosable, expected_status="diagnosis",
                     decisive_evidence_ids=list(decisive),
                     injected_fault={"decisive_equivalents": eq or {"log:aea2b46ce0": ["log:1111111111"]}})


def bb(seen=(), cited=()):
    rep = FinalReport(incident_id="INC-1", system="blackbox", status="diagnosis", headline="h",
                      ranked_causes=[RankedCause(rank=1, component="c", mechanism="m",
                                                 evidence_ids=list(cited))],
                      claims=[Claim(claim_id="c1", text="t", kind="factual", evidence_ids=list(cited))])
    return SystemOutput(system="blackbox", incident_id="INC-1", status="completed", report=rep,
                        evidence_seen=[EvidenceRecord(evidence_id=i, source="logs") for i in seen])


def test_exact_and_listed_equivalent_count():
    r = sv2.score_v2(bb(seen=["log:1111111111", "commit:e7a0cc0"]), key())
    assert r["retrieved_v2"] == 2
    assert r["retrieved_detail"] == {"log:aea2b46ce0": "equivalent:log:1111111111",
                                     "commit:e7a0cc0": "exact"}


def test_near_miss_ids_do_not_count():
    """Prefixes, extensions, case changes and template neighbours are NOT equivalents."""
    near = ["log:aea2b46ce", "log:aea2b46ce01", "LOG:AEA2B46CE0", "log:aea2b46ce0 ",
            "commit:e7a0cc", "commit:e7a0cc0f", "commit:e7a0cc0820edbbd", "log:111111111",
            "log:11111111110"]
    r = sv2.score_v2(bb(seen=near, cited=near), key())
    assert r["retrieved_v2"] == 0 and r["cited_v2"] == 0
    assert set(r["retrieved_detail"].values()) == {None}


def test_equivalent_of_one_decisive_does_not_count_for_another():
    k = key(decisive=("log:a", "log:b"), eq={"log:a": ["log:a2"]})
    r = sv2.score_v2(bb(seen=["log:a2"]), k)
    assert r["retrieved_detail"] == {"log:a": "equivalent:log:a2", "log:b": None}


def test_cited_counts_only_report_citations():
    r = sv2.score_v2(bb(seen=["log:aea2b46ce0", "commit:e7a0cc0"], cited=["commit:e7a0cc0"]), key())
    assert r["retrieved_v2"] == 2 and r["cited_v2"] == 1


def test_baseline_uses_included_ids_and_non_diagnosable_is_zero():
    out = SystemOutput(system="b0", incident_id="INC-1", status="completed",
                       evidence_seen=[EvidenceRecord(evidence_id="commit:e7a0cc0", source="commits")],
                       evidence_budget=EvidenceBudget(context_tokens=8192, reserved_tokens=1,
                                                      evidence_token_budget=1, evidence_tokens_total=1,
                                                      included_ids=["log:1111111111"]))
    r = sv2.score_v2(out, key())
    assert r["retrieved_detail"] == {"log:aea2b46ce0": "equivalent:log:1111111111", "commit:e7a0cc0": None}
    assert sv2.score_v2(out, key(diagnosable=False))["decisive_total"] == 0


def test_failed_run_without_output_scores_zero():
    r = sv2.score_v2(None, key())
    assert r["retrieved_v2"] == 0 and r["decisive_total"] == 2


def test_v2_agrees_with_v1_on_rule():
    """v1 (eval/metrics.py, frozen) implements the same exact-or-listed rule."""
    from eval.metrics import score_run
    for seen in (["log:1111111111"], ["log:aea2b46ce"], ["commit:e7a0cc0", "log:aea2b46ce0"], []):
        out = bb(seen=seen)
        assert score_run(out, key(), run_status="completed", system="blackbox")["decisive_retrieved"] \
            == sv2.score_v2(out, key())["retrieved_v2"]
