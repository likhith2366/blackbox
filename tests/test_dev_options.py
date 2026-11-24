"""Development options change_expansion and synthesis (off by default; frozen config unchanged)."""
from __future__ import annotations

from pathlib import Path

import yaml

from blackbox.investigator.coordinator import Coordinator
from blackbox.schemas import BackendResult, EvidenceRecord, RunConfig

from tests.test_investigator_core import FakeBackend, ScriptedAdapter, T

ROOT = Path(__file__).resolve().parents[1]


class DeployBackend(FakeBackend):
    def list_deployments(self, **kw):
        self.calls.append("list_deployments")
        return BackendResult(status="ok", records=[EvidenceRecord(
            evidence_id="deploy:d-9", source="deployments", service="payment-svc", event_time=T, available_time=T,
            content="deployment d-9 of payment-svc", attributes={"build_id": "b-9", "commit": "abc1234"})])

    def get_build(self, build_id):
        self.calls.append(f"get_build:{build_id}")
        return BackendResult(status="ok", records=[EvidenceRecord(evidence_id=f"build:{build_id}", source="builds",
                                                                  content="build of abc1234")])

    def get_commit(self, sha):
        self.calls.append(f"get_commit:{sha}")
        return BackendResult(status="ok", records=[EvidenceRecord(
            evidence_id="commit:abc1234", source="commits",
            content="fast path returns before POOL.release(conn)\n" + "x" * 1500 + "\nDIFF-TAIL")])


def rounds():
    return [{"hypotheses": [], "tool_calls": [{"tool": "list_deployments", "args": {}}], "decision": "continue",
             "rationale": "what changed"},
            {"hypotheses": [], "tool_calls": [], "decision": "insufficient_evidence", "rationale": "stop"}]


SYN = {"hypotheses": [{"id": "s1", "component": "payment-svc", "mechanism": "fast path skips POOL.release, pool exhausted",
                       "chain": {"cause": {"statement": "commit", "evidence_ids": ["commit:abc1234", "deploy:d-9"]},
                                 "runtime_effect": {"statement": "pool full", "evidence_ids": ["metric:m1"]},
                                 "symptom": {"statement": "timeouts", "evidence_ids": ["log:a1", "log:NOT-RETRIEVED"]}},
                       "contradicting": []}],
       "decision": "diagnosis", "top_hypothesis": "s1", "rationale": "r"}


def test_options_are_off_by_default_and_in_the_frozen_config():
    assert RunConfig().change_expansion is False and RunConfig().synthesis is False
    frozen = RunConfig(**yaml.safe_load((ROOT / "configs" / "frozen_v1.yaml").read_text(encoding="utf-8")))
    assert frozen.change_expansion is False and frozen.synthesis is False


def test_change_expansion_follows_a_deployment_in_the_window():
    b = DeployBackend()
    out = Coordinator(b, RunConfig(change_expansion=True), adapter=ScriptedAdapter(rounds())).run()
    assert "get_build:b-9" in b.calls and "get_commit:abc1234" in b.calls and "get_metrics" in b.calls
    s = out.steps[0]
    assert any(r.startswith("change expansion: get_commit") for r in s.orchestration_rules)
    assert any(t.call_id.startswith("r1auto") for t in s.tool_requests)
    assert {"build:b-9", "commit:abc1234"} <= {r.evidence_id for r in out.evidence_seen}


def test_expansion_off_leaves_the_round_unchanged():
    b = DeployBackend()
    Coordinator(b, RunConfig(), adapter=ScriptedAdapter(rounds())).run()
    assert not any(c.startswith("get_commit") for c in b.calls)


def test_synthesis_rewrites_the_ledger_and_still_needs_the_gate():
    b = DeployBackend()
    outs = rounds() + [SYN]
    out = Coordinator(b, RunConfig(change_expansion=True, synthesis=True), adapter=ScriptedAdapter(outs)).run()
    syn = out.context["synthesis"]
    assert syn["parsed"]["top_hypothesis"] == "s1" and "gate" in syn
    assert out.report.status == "diagnosis"          # cites deploy + commit + runtime records: passes gate v1
    # the unretrieved id was dropped, not cited
    assert all("NOT-RETRIEVED" not in i for c in out.report.ranked_causes for i in c.evidence_ids)
    assert syn["notebook"]["records"] >= 4


def test_synthesis_abstention_is_kept():
    b = DeployBackend()
    outs = rounds() + [{**SYN, "decision": "insufficient_evidence", "top_hypothesis": None}]
    out = Coordinator(b, RunConfig(synthesis=True), adapter=ScriptedAdapter(outs)).run()
    assert out.report.status == "insufficient_evidence"


def test_gate_ranking_accepts_the_first_hypothesis_the_gate_allows():
    bad = {"id": "s1", "component": "checkout-svc", "mechanism": "overload",
           "chain": {"cause": {"statement": "x", "evidence_ids": ["log:a1"]}, "runtime_effect": {"statement": "x",
                     "evidence_ids": []}, "symptom": {"statement": "x", "evidence_ids": []}}, "contradicting": []}
    good = {**SYN["hypotheses"][0], "id": "s2"}
    syn = {**SYN, "hypotheses": [bad, good], "top_hypothesis": "s1"}
    b = DeployBackend()
    out = Coordinator(b, RunConfig(change_expansion=True, synthesis=True, synthesis_ranking="gate"),
                      adapter=ScriptedAdapter(rounds() + [syn])).run()
    assert out.report.status == "diagnosis" and out.context["synthesis"]["accepted_rank"] == 2
    b2 = DeployBackend()
    out2 = Coordinator(b2, RunConfig(change_expansion=True, synthesis=True), adapter=ScriptedAdapter(rounds() + [syn])).run()
    assert out2.report.status == "insufficient_evidence"          # default: only the top choice is checked


def test_runtime_context_fetches_error_and_lifecycle_logs_before_synthesis():
    b = DeployBackend()
    outs = rounds() + [SYN]
    out = Coordinator(b, RunConfig(synthesis=True, runtime_context=True), adapter=ScriptedAdapter(outs)).run()
    ctx = out.context["synthesis"]["runtime_context"]
    assert len(ctx) == 4 and {c["args"].get("level") for c in ctx} == {"ERROR", None}
    assert b.calls.count("search_logs") == 4
