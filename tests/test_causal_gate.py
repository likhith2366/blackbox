"""Tests for the causal-evidence gate (development option diagnosis_gate: causal).

Records are built in memory in the shapes the snapshot backend produces. No model, no snapshots.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from blackbox.investigator import causal_gate
from blackbox.investigator.coordinator import Coordinator
from blackbox.schemas import BackendResult, EvidenceRecord, Hypothesis, RunConfig, SourceStatus

from tests.test_investigator_core import FakeBackend, ScriptedAdapter

ROOT = Path(__file__).resolve().parents[1]
DAY = "2026-11-20"


def series(service, name, values, start="10:00:00", step=5):
    h, m, s = map(int, start.split(":"))
    pts = []
    for n, v in enumerate(values):
        t = h * 3600 + m * 60 + s + n * step
        pts.append(f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d} {v}")
    text = f"{service} {name} (5.0s points, {DAY}T{start}.000Z .. ): min={min(values)} max={max(values)}\n" + "; ".join(pts)
    return EvidenceRecord(evidence_id=f"metric:{service}-{name}", source="metrics", service=service,
                          event_time=f"{DAY}T{start}.000Z", title=f"{service} {name}", content=text,
                          attributes={"metric": name})


ERR = series("checkout-svc", "error_rate", [0, 0, 0, 0, 0.8, 0.9, 1])               # onset 10:00:20
UPERR = series("checkout-svc", "upstream_errors_per_s", [0, 0, 0, 0, 5, 7, 9])
POOL = series("payment-svc", "db_pool_in_use", [1, 1, 2, 4, 4, 4, 4])
DEPLOY = EvidenceRecord(evidence_id="deploy:d-1", source="deployments", service="payment-svc",
                        event_time=f"{DAY}T10:00:05.000Z", version="2.5.0", title="deployment of payment-svc 2.5.0",
                        content="deployment d-1 of payment-svc version 2.5.0 build=b-1 commit=abc1234")
COMMIT_MSG = EvidenceRecord(evidence_id="commit:abc1234", source="commits", service="payment-svc",
                            event_time=f"{DAY}T10:00:01.000Z", title="fast path for micro charges",
                            content="commit abc1234\nfast path for micro charges\nfiles: M services/payment/charge.py")
COMMIT_DIFF = COMMIT_MSG.model_copy(update={"content": COMMIT_MSG.content + "\n\ndiff --git a/charge.py b/charge.py\n"
                                            "@@ -10,6 +10,8 @@\n+    if amount < 100:\n+        return ok()\n"
                                            "     conn.release()"})
LOG_POOL = EvidenceRecord(evidence_id="log:p1", source="logs", service="payment-svc",
                          event_time=f"{DAY}T10:00:22.000Z", title="ERROR payment-svc: pool acquire timed out",
                          content="ERROR payment-svc pool acquire timed out after 2000ms",
                          attributes={"first_seen": f"{DAY}T10:00:22.000Z"})
LOG_TIMEOUT = EvidenceRecord(evidence_id="log:t1", source="logs", service="checkout-svc",
                             event_time=f"{DAY}T10:00:21.000Z", title="ERROR checkout-svc: payment call timed out",
                             content="ERROR checkout-svc payment call timed out after 250ms",
                             attributes={"first_seen": f"{DAY}T10:00:21.000Z"})
ALL = {r.evidence_id: r for r in (ERR, UPERR, POOL, DEPLOY, COMMIT_MSG, LOG_POOL, LOG_TIMEOUT)}


def hyp(component, mechanism, *ids):
    return Hypothesis(hypothesis_id="h1", component=component, mechanism=mechanism, status="supported",
                      confidence="high", supporting=list(ids))


def test_two_symptom_metrics_never_fill_every_role():
    for mech in ("payment integration error due to recent commit", "connection pool exhausted"):
        ok, why, roles = causal_gate.check(hyp("checkout-svc", mech, ERR.evidence_id, UPERR.evidence_id), ALL)
        assert not ok
        assert roles["mechanism"] == []
    ok, why, _ = causal_gate.check(hyp("checkout-svc", "payment integration error due to recent commit",
                                       ERR.evidence_id, UPERR.evidence_id), ALL)
    assert "change evidence missing" in why


def test_symptom_only_mechanism_text_is_refused():
    ok, why, _ = causal_gate.check(hyp("checkout-svc", "payment-svc timeout", ERR.evidence_id, LOG_TIMEOUT.evidence_id), ALL)
    assert not ok and "mechanism not stated" in why
    ok, why, _ = causal_gate.check(hyp("payment-svc", "error rate spike due to new deployment (version 2.5.0)",
                                       DEPLOY.evidence_id, ERR.evidence_id), ALL)
    assert not ok and "mechanism not stated" in why


def test_deploy_without_mechanism_is_refused():
    h = hyp("payment-svc", "fast path in 2.5.0 leaks database connections", DEPLOY.evidence_id, ERR.evidence_id,
            UPERR.evidence_id)
    ok, why, roles = causal_gate.check(h, ALL)
    assert not ok and "mechanism evidence missing" in why
    assert roles["change"] == ["deploy:d-1"]
    # a commit message without its diff is not mechanism evidence either
    ok, why, _ = causal_gate.check(hyp("payment-svc", "fast path leaks database connections", COMMIT_MSG.evidence_id,
                                       ERR.evidence_id), ALL)
    assert not ok and "mechanism evidence missing" in why


def test_supported_change_based_claim_passes():
    seen = dict(ALL, **{COMMIT_DIFF.evidence_id: COMMIT_DIFF})
    h = hyp("payment-svc", "new fast path returns before releasing its DB connection, pool exhausted",
            DEPLOY.evidence_id, COMMIT_DIFF.evidence_id, ERR.evidence_id)
    ok, why, roles = causal_gate.check(h, seen)
    assert ok, why
    assert roles["timing"] == "consistent" and "commit:abc1234" in roles["mechanism"]
    # a component resource metric is mechanism evidence too (no diff needed)
    h = hyp("payment-svc", "connection leak after deployment 2.5.0 exhausts the pool", DEPLOY.evidence_id,
            POOL.evidence_id, LOG_TIMEOUT.evidence_id)
    ok, why, _ = causal_gate.check(h, ALL)
    assert ok, why


def test_effect_before_the_change_is_refused():
    late = DEPLOY.model_copy(update={"evidence_id": "deploy:d-2", "event_time": f"{DAY}T10:05:00.000Z"})
    seen = dict(ALL, **{late.evidence_id: late})
    h = hyp("payment-svc", "connection leak after deployment exhausts the pool", late.evidence_id, POOL.evidence_id,
            ERR.evidence_id)
    ok, why, roles = causal_gate.check(h, seen)
    assert not ok and "timing inconsistent" in why and roles["timing"] == "inconsistent"


def test_supported_runtime_fault_without_a_change_passes():
    h = hyp("payment-svc", "connection pool exhausted", POOL.evidence_id, LOG_POOL.evidence_id)
    ok, why, roles = causal_gate.check(h, ALL)
    assert ok, why
    assert roles["change_based"] is False and roles["change"] == []
    # a log that shares a specific mechanism term can be the mechanism record; the effect must be another record
    ok, why, _ = causal_gate.check(hyp("payment-svc", "connection pool exhausted", LOG_POOL.evidence_id), ALL)
    assert not ok and "runtime effect missing" in why
    ok, why, _ = causal_gate.check(hyp("payment-svc", "connection pool exhausted", LOG_POOL.evidence_id,
                                       ERR.evidence_id), ALL)
    assert ok, why


def test_unavailable_change_sources_keep_the_claim_refused_and_say_so():
    seen = {r.evidence_id: r for r in (ERR, UPERR, LOG_TIMEOUT)}
    h = hyp("checkout-svc", "retry logic from the recent commit multiplies attempts", ERR.evidence_id,
            LOG_TIMEOUT.evidence_id)
    ok, why, _ = causal_gate.check(h, seen, unavailable=["commits", "deployments"])
    assert not ok and "change evidence missing" in why and "unavailable in this snapshot" in why


def test_unknown_component_is_refused():
    ok, why, _ = causal_gate.check(hyp("the database", "connection pool exhausted", POOL.evidence_id,
                                       LOG_POOL.evidence_id), ALL)
    assert not ok and "component missing" in why


# ---------------------------------------------------------------- coordinator integration

class SymptomBackend(FakeBackend):
    def get_metrics(self, **kw):
        self.calls.append("get_metrics")
        return BackendResult(status="ok", records=[ERR, UPERR])

    def source_status(self):
        return [SourceStatus(source="logs", available=True, healthy=True),
                SourceStatus(source="metrics", available=True, healthy=True),
                SourceStatus(source="commits", available=False, healthy=False)]


def _hyp_dict(mech="payment integration error due to recent commit"):
    return {"id": "h1", "component": "checkout-svc", "mechanism": mech, "status": "supported", "confidence": "high",
            "supporting": [ERR.evidence_id, UPERR.evidence_id], "contradicting": [], "note": ""}


def _run(gate, outputs):
    cfg = RunConfig(max_rounds=4, diagnosis_gate=gate)
    co = Coordinator(SymptomBackend(), cfg, adapter=ScriptedAdapter(outputs))
    return co.run()


def _rounds():
    return [
        {"hypotheses": [], "tool_calls": [{"tool": "get_metrics", "args": {"service": "checkout-svc"}}],
         "decision": "continue", "rationale": "look"},
        {"hypotheses": [_hyp_dict()], "tool_calls": [], "decision": "diagnose", "rationale": "commit"},
        {"hypotheses": [_hyp_dict()], "tool_calls": [{"tool": "search_logs", "args": {"query": "timeout"}}],
         "decision": "continue", "rationale": "more"},
        {"hypotheses": [_hyp_dict()], "tool_calls": [], "decision": "insufficient_evidence", "rationale": "stop"},
    ]


def test_default_gate_is_v1_and_frozen_config_keeps_it():
    assert RunConfig().diagnosis_gate == "v1"
    frozen = yaml.safe_load((ROOT / "configs" / "frozen_v1.yaml").read_text(encoding="utf-8"))
    assert RunConfig(**frozen).diagnosis_gate == "v1"
    dev = yaml.safe_load((ROOT / "configs" / "dev_v2c.yaml").read_text(encoding="utf-8"))
    assert RunConfig(**dev).diagnosis_gate == "causal" and RunConfig(**dev).rationale_first is False


def test_default_gate_still_accepts_two_runtime_citations():
    out = _run("v1", _rounds())
    assert out.steps[1].stop_decision == "stop_diagnosis"
    assert out.report.status == "diagnosis"


def test_causal_gate_refuses_with_a_reason_lets_the_model_continue_and_keeps_the_trace():
    out = _run("causal", _rounds())
    s = out.steps[1]
    assert s.stop_decision == "continue"
    assert s.stop_reason.startswith("diagnosis refused: h1: change evidence missing")
    assert "commits unavailable in this snapshot" in s.stop_reason
    assert any(r.startswith("causal gate roles:") for r in s.orchestration_rules)
    assert any(r.startswith("diagnosis gate:") for r in s.orchestration_rules)
    # the model keeps its budget and decides on its own; no evidence was fetched for it
    assert [t.tool for t in out.steps[2].tool_requests] == ["search_logs"]
    assert out.steps[-1].stop_decision == "stop_insufficient"
    assert out.report.status == "insufficient_evidence"


def test_causal_gate_at_the_round_limit_does_not_turn_the_ledger_into_a_diagnosis():
    rounds = _rounds()[:3]
    out = _run("causal", rounds + [{"hypotheses": [_hyp_dict()], "tool_calls": [], "decision": "continue",
                                     "rationale": "x"}])
    assert out.report.status == "insufficient_evidence"


def test_onset_is_the_latest_episode_not_an_earlier_one():
    two = series("checkout-svc", "error_rate", [0, 0.9, 0.9, 0, 0, 0, 0, 0.8, 0.9])     # spikes at :05 and :35
    t = causal_gate.onset(two)
    assert t.strftime("%H:%M:%S") == "10:00:35"


def test_change_based_claim_needs_a_change_inside_the_incident_window():
    seen = dict(ALL, **{COMMIT_DIFF.evidence_id: COMMIT_DIFF})
    h = hyp("payment-svc", "new fast path returns before releasing its DB connection, pool exhausted",
            DEPLOY.evidence_id, COMMIT_DIFF.evidence_id, ERR.evidence_id)
    ok, why, _ = causal_gate.check(h, seen, window_start=f"{DAY}T09:59:00.000Z")
    assert ok, why
    ok, why, roles = causal_gate.check(h, seen, window_start=f"{DAY}T10:30:00.000Z")
    assert not ok and "outside the incident window" in why and roles["timing"] == "change before window"
