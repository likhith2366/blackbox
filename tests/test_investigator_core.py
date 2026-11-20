"""Control-logic tests for the investigator and baselines.

Test doubles only: a scripted model and a tiny in-memory backend. Nothing here touches
Ollama, the demo app or real snapshots, and nothing is written to runs/ or results/.
"""
from __future__ import annotations

import json
import time

import pytest

from blackbox.baselines.single_call import run_single_call
from blackbox.investigator.coordinator import Coordinator
from blackbox.model.adapter import ModelReply, parse_json_object
from blackbox.schemas import (
    Alert, BackendResult, EvidenceRecord, Incident, RunConfig, SourceStatus,
)

T = "2026-11-19T10:00:00Z"


def rec(i: str, source: str = "logs", text: str = "pool wait 900ms active=20/20") -> EvidenceRecord:
    return EvidenceRecord(evidence_id=i, source=source, service="payment-svc", event_time=T,
                          available_time=T, title=text, content=text)


class FakeBackend:
    incident_id = "INC-TEST"
    ablation = "none"

    def __init__(self, slow: float = 0.0):
        self.slow = slow
        self.calls: list[str] = []

    def incident(self):
        return Incident(incident_id="INC-TEST", alert=Alert(alert_id="a1", title="checkout latency high",
                        service="checkout-svc", signal="p95_latency_ms", condition="> 1500", fired_at=T),
                        window_start=T, window_end=T, investigation_cutoff=T,
                        services=["checkout-svc", "payment-svc"], available_sources=["logs", "metrics"])

    def source_status(self):
        return [SourceStatus(source="logs", available=True, healthy=True),
                SourceStatus(source="metrics", available=True, healthy=True)]

    def search_logs(self, **kw):
        self.calls.append("search_logs")
        if self.slow:
            time.sleep(self.slow)
        return BackendResult(status="ok", records=[rec("log:a1"), rec("log:a2")], total_matches=2)

    def get_metrics(self, **kw):
        self.calls.append("get_metrics")
        return BackendResult(status="ok", records=[rec("metric:m1", "metrics", "pool_active=20/20")])

    def search_tickets(self, **kw):
        self.calls.append("search_tickets")
        return BackendResult(status="ok", records=[rec("jira:DEMO-1#c1", "tickets", "maybe the db")])

    def export_logs(self, start=None, end=None):
        return [rec(f"log:{n:03d}", text=f"line {n} " + "x" * 200) for n in range(60)]


class ScriptedAdapter:
    def __init__(self, outputs: list):
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    def chat(self, messages, schema=None, num_predict=None):
        self.prompts.append(messages[-1]["content"])
        out = self.outputs.pop(0) if self.outputs else {"hypotheses": [], "tool_calls": [],
                                                         "decision": "insufficient_evidence", "rationale": ""}
        text = out if isinstance(out, str) else json.dumps(out)
        parsed, err = parse_json_object(text)
        return ModelReply(text=text, parsed=parsed, parse_error=err, prompt_tokens=100,
                          completion_tokens=20, seconds=0.01)


def H(hid="h1", sup=(), status="active", conf="medium", con=()):
    return {"id": hid, "component": "payment-svc", "mechanism": "connection pool exhausted",
            "status": status, "confidence": conf, "supporting": list(sup), "contradicting": list(con)}


REPORT = {"status": "diagnosis", "ranked_causes": [{"component": "payment-svc", "mechanism": "pool exhausted",
          "confidence": "high", "evidence_ids": ["log:a1", "log:FAKE"]}],
          "claims": [{"text": "pool is full", "kind": "factual", "evidence_ids": ["metric:m1"]}],
          "unresolved_questions": [], "next_check": "check pool size"}


def coordinator(outputs, backend=None, **cfg):
    c = Coordinator(backend or FakeBackend(), RunConfig(**cfg), adapter=ScriptedAdapter(outputs))
    return c


def test_unauthorized_tool_is_refused_and_recorded():
    out = coordinator([
        {"hypotheses": [H()], "tool_calls": [{"tool": "delete_service", "args": {"name": "payment-svc"}},
                                             {"tool": "search_logs", "args": {}}], "decision": "continue", "rationale": ""},
        {"hypotheses": [H(sup=["log:a1", "log:a2"])], "tool_calls": [], "decision": "diagnose", "rationale": ""},
        REPORT,
    ]).run()
    assert [a.tool for a in out.unauthorized_attempts] == ["delete_service"]
    assert out.unauthorized_executed == 0
    assert out.steps[0].tool_results[0].status == "denied"
    assert out.tool_calls_used == 1          # the denied request did not consume budget or run


def test_fabricated_citations_are_removed():
    out = coordinator([
        {"hypotheses": [H(sup=["log:NEVER"])], "tool_calls": [{"tool": "search_logs", "args": {}}],
         "decision": "continue", "rationale": ""},
        {"hypotheses": [H(sup=["log:a1", "log:a2"])], "tool_calls": [], "decision": "diagnose", "rationale": ""},
        REPORT,
    ]).run()
    assert out.steps[0].invalid_citations == ["log:NEVER"]
    assert "log:FAKE" in out.report.invalid_citations
    assert out.report.ranked_causes[0].evidence_ids == ["log:a1"]


def test_diagnosis_needs_two_runtime_citations():
    out = coordinator([
        {"hypotheses": [H()], "tool_calls": [{"tool": "search_tickets", "args": {}}], "decision": "continue", "rationale": ""},
        {"hypotheses": [H(sup=["jira:DEMO-1#c1"])], "tool_calls": [{"tool": "search_logs", "args": {}}],
         "decision": "diagnose", "rationale": ""},
        {"hypotheses": [H(sup=["log:a1", "log:a2"])], "tool_calls": [], "decision": "diagnose", "rationale": ""},
        REPORT,
    ]).run()
    assert out.steps[1].stop_decision == "continue" and "refused" in out.steps[1].stop_reason
    assert out.steps[2].stop_decision == "stop_diagnosis"
    assert out.report.status == "diagnosis"


def test_per_round_cap_and_total_budget_are_enforced():
    many = [{"tool": "search_logs", "args": {"query": str(i)}} for i in range(3)]
    script = [{"hypotheses": [H()], "tool_calls": many + [{"tool": "get_metrics", "args": {"service": "x"}}],
               "decision": "continue", "rationale": ""}]
    script += [{"hypotheses": [H()], "tool_calls": many, "decision": "continue", "rationale": ""}] * 5
    out = coordinator(script + [REPORT]).run()
    assert out.tool_calls_used == 12
    assert out.steps[0].tool_results[3].status == "budget_exceeded"   # 4th call in one round
    assert out.budget_violations >= 1
    assert out.rounds_used <= 6


def test_invalid_args_become_an_observation():
    out = coordinator([
        {"hypotheses": [H()], "tool_calls": [{"tool": "get_metrics", "args": {"limit": "lots"}}],
         "decision": "continue", "rationale": ""},
        {"hypotheses": [H()], "tool_calls": [], "decision": "insufficient_evidence", "rationale": ""},
        {**REPORT, "status": "insufficient_evidence", "ranked_causes": []},
    ]).run()
    assert out.steps[0].tool_results[0].status == "invalid_args"
    assert out.report.status == "insufficient_evidence"
    assert out.report.headline == "No reliable root cause identified."


def test_two_malformed_rounds_stop_with_error():
    out = coordinator(["not json", "still not json", "{broken", "nope", REPORT]).run()
    assert not out.steps[0].parse_ok and not out.steps[1].parse_ok
    assert out.steps[-1].stop_decision == "stop_error"


def test_slow_tool_times_out():
    out = coordinator([
        {"hypotheses": [H()], "tool_calls": [{"tool": "search_logs", "args": {}}], "decision": "continue", "rationale": ""},
        {"hypotheses": [H()], "tool_calls": [], "decision": "insufficient_evidence", "rationale": ""},
        {**REPORT, "status": "insufficient_evidence", "ranked_causes": []},
    ], backend=FakeBackend(slow=2.0), tool_timeout_s=0.5).run()
    assert out.steps[0].tool_results[0].status == "timeout"


def test_b1_overflow_is_recorded_and_unmatched():
    evidence = [rec(f"log:{n:03d}", text="pool wait " + "y" * 400) for n in range(80)]
    out = run_single_call("b1", FakeBackend(), RunConfig(num_ctx=4096), b1_evidence=evidence,
                          adapter=ScriptedAdapter([{**REPORT, "ranked_causes": []}]))
    eb = out.evidence_budget
    assert eb.matched is False and eb.omitted_ids and eb.omitted_tokens > 0
    assert eb.included_ids == [r.evidence_id for r in evidence[:len(eb.included_ids)]]   # trailing ones dropped
    assert [r.evidence_id for r in out.evidence_seen] == eb.included_ids


def test_b0_keeps_most_recent_logs_when_truncating():
    out = run_single_call("b0", FakeBackend(), RunConfig(num_ctx=4096),
                          adapter=ScriptedAdapter([{**REPORT, "ranked_causes": []}]))
    eb = out.evidence_budget
    assert eb.omitted_ids and eb.included_ids[-1] == "log:059" and eb.matched is None


def test_citation_recovery_is_narrow_and_logged():
    from blackbox.investigator.common import check_ids, resolve_id
    seen = {"metric:m1", "log:a1", "log:a12"}
    assert resolve_id("metric:m1: payment-svc pool_active=20/20", seen) == "metric:m1"
    assert resolve_id("[log:a1]", seen) == "log:a1"
    assert resolve_id("m1", seen) == "metric:m1"
    assert resolve_id("log:a12 slow", seen) == "log:a12"      # log:a1 is only a substring of log:a12
    assert resolve_id("log:a1 and metric:m1", seen) is None   # two distinct ids: ambiguous, refused
    assert resolve_id("a", seen) is None                       # no guessing
    assert resolve_id("metric:m9", seen) is None
    norm: list[str] = []
    valid, invalid = check_ids(["m1", "log:zz"], seen, norm)
    assert valid == ["metric:m1"] and invalid == ["log:zz"] and norm == ["m1 -> metric:m1"]


def test_identical_calls_are_served_from_cache():
    backend = FakeBackend()
    out = coordinator([
        {"hypotheses": [H()], "tool_calls": [{"tool": "search_logs", "args": {"service": "x"}}],
         "decision": "continue", "rationale": ""},
        {"hypotheses": [H()], "tool_calls": [{"tool": "search_logs", "args": {"service": "x"}}],
         "decision": "continue", "rationale": ""},
        {"hypotheses": [H(sup=["log:a1", "log:a2"])], "tool_calls": [], "decision": "diagnose", "rationale": ""},
        REPORT,
    ], backend=backend).run()
    assert backend.calls.count("search_logs") == 1
    assert out.steps[1].duplicate_calls == ["r2c1==r1c1"]
    assert out.tool_calls_used == 2      # the repeat still spends budget


def test_first_tool_rule_still_allows_abstention_after_a_failed_tool():
    # get_build is not implemented by FakeBackend, so the call fails like a broken connector
    out = coordinator([
        {"hypotheses": [H()], "tool_calls": [{"tool": "get_build", "args": {"build_id": "b-1"}}],
         "decision": "continue", "rationale": ""},
        {"hypotheses": [H()], "tool_calls": [], "decision": "insufficient_evidence", "rationale": ""},
        {**REPORT, "status": "insufficient_evidence", "ranked_causes": []},
    ]).run()
    assert out.steps[0].tool_results[0].status == "error"
    assert any("first-tool rule" in r for r in out.steps[0].orchestration_rules)
    assert not out.steps[1].orchestration_rules            # rule lifted once a call was made
    assert out.steps[1].stop_decision == "stop_insufficient"
    assert out.report.status == "insufficient_evidence"
    assert out.steps[0].context["prompt_tokens_est"] > 0 and "prompt_tokens_actual" in out.steps[0].context
    assert out.context["report_call"]["prompt_tokens_est"] > 0


def test_report_audit_separates_raw_and_recovered_citations():
    out = coordinator([
        {"hypotheses": [H()], "tool_calls": [{"tool": "search_logs", "args": {}}], "decision": "continue", "rationale": ""},
        {"hypotheses": [H(sup=["log:a1", "a2"])], "tool_calls": [], "decision": "diagnose", "rationale": ""},
        REPORT,
    ]).run()
    methods = {(c.raw, c.method) for c in out.steps[1].citation_audit}
    assert ("log:a1", "exact") in methods and ("a2", "suffix") in methods
    rep = {(c.raw, c.method) for c in out.report.citation_audit}
    assert ("log:FAKE", "invalid") in rep


def test_embedded_rule_rejects_padded_or_extended_ids():
    # QA QA finding: substring matching turned fabricated ids into valid citations
    from blackbox.investigator.common import resolve_citation
    assert resolve_citation("log:12", {"log:1"}) == (None, "invalid")
    assert resolve_citation("log:9f2c41ff", {"log:9f2c41"}) == (None, "invalid")
    assert resolve_citation("metric:m1: payment-svc pool", {"metric:m1"}) == ("metric:m1", "embedded")
    assert resolve_citation("see [log:a1].", {"log:a1"}) == ("log:a1", "embedded")


def test_coverage_hint_lists_only_unqueried_available_sources():
    a = ScriptedAdapter([
        {"hypotheses": [H()], "tool_calls": [{"tool": "search_logs", "args": {}}], "decision": "continue", "rationale": ""},
        {"hypotheses": [H()], "tool_calls": [], "decision": "insufficient_evidence", "rationale": ""},
        {**REPORT, "status": "insufficient_evidence", "ranked_causes": []},
    ])
    out = Coordinator(FakeBackend(), RunConfig(coverage_hint=True), adapter=a).run()
    assert "SOURCES NOT YET QUERIED" in a.prompts[0] and "logs (search_logs)" in a.prompts[0]
    assert "logs (" not in a.prompts[1].split("SOURCES NOT YET QUERIED")[1].split("\n\n")[0]
    assert "metrics (get_metrics)" in a.prompts[1]
    off = ScriptedAdapter([{"hypotheses": [], "tool_calls": [{"tool": "search_logs", "args": {}}], "decision": "continue",
                            "rationale": ""}, {"hypotheses": [], "tool_calls": [], "decision": "insufficient_evidence",
                            "rationale": ""}, {**REPORT, "status": "insufficient_evidence", "ranked_causes": []}])
    Coordinator(FakeBackend(), RunConfig(coverage_hint=False), adapter=off).run()
    assert all("SOURCES NOT YET QUERIED" not in p for p in off.prompts)
