"""QA QA: eval/ (Evaluation) against what the builder and backend actually produce.

Hand-built SystemOutput / AnswerKey objects only (TEST FIXTURE data, ids say "qa").
"""
from __future__ import annotations

import pytest

from blackbox.schemas import AnswerKey, EvidenceRecord, SystemOutput

metrics = pytest.importorskip("eval.metrics")


def _key(**kw):
    base = dict(incident_id="INC-9001", scenario_number=1, scenario_type="misleading_symptom", is_primary=True,
                diagnosable=True, expected_status="diagnosis", component="payment-svc", mechanism="qa",
                decisive_evidence_ids=["log:qa-first", "metric:qa1"])
    base.update(kw)
    return AnswerKey(**base)


def _out(ids):
    return SystemOutput(system="blackbox", incident_id="INC-9001", status="completed",
                        evidence_seen=[EvidenceRecord(evidence_id=i, source="logs" if i.startswith("log") else "metrics")
                                       for i in ids])


def test_exact_decisive_ids_are_counted():
    row = metrics.score_run(_out(["log:qa-first", "metric:qa1"]), _key(), run_status="completed", system="blackbox")
    assert row["decisive_retrieved"] == 2


# regression for QA-F08 (fixed by Evaluation: eval/metrics.py decisive_equivalents)
def test_decisive_coverage_uses_recorded_equivalents():
    key = _key(injected_fault={"decisive_equivalents": {"log:qa-first": ["log:qa-later", "log:qa-last"]}})
    row = metrics.score_run(_out(["log:qa-later", "metric:qa1"]), key, run_status="completed", system="blackbox")
    assert row["decisive_retrieved"] == 2
