"""eval.validate_snapshot on synthetic snapshots with a fake backend module (tmp dirs only)."""
import json
import sys
import types
from pathlib import Path

import pytest

from blackbox.schemas import (Alert, AnswerKey, BackendResult, EvidenceRecord, Incident,
                              SourceStatus)
from eval import validate_snapshot as vs

IID = "INC-0042"
CUTOFF = "2026-01-01T00:10:00Z"


def _incident():
    return Incident(incident_id=IID, alert=Alert(alert_id="a1", title="checkout p95 high",
                    service="checkout-svc", signal="p95_latency_ms", condition="p95 > 1500",
                    fired_at="2026-01-01T00:08:00Z"),
                    window_start="2026-01-01T00:00:00Z", window_end=CUTOFF,
                    investigation_cutoff=CUTOFF, services=["checkout-svc", "payment-svc"],
                    available_sources=["logs", "metrics", "commits"])


class FakeBackend:
    def __init__(self, records, ablation="none", raise_in=None):
        self.incident_id, self.ablation = IID, ablation
        self._recs, self._raise = records, raise_in

    def incident(self):
        return _incident()

    def source_status(self):
        return [SourceStatus(source="logs", available=True, healthy=True),
                SourceStatus(source="tickets", available=False, healthy=True, detail="not in snapshot")]

    def _r(self, src):
        if self._raise == src:
            raise KeyError("boom")
        return BackendResult(status="ok", records=[r for r in self._recs if r.source == src])

    def search_logs(self, **kw): return self._r("logs")
    def search_traces(self, **kw): return BackendResult(status="empty")
    def get_trace(self, t): return BackendResult(status="empty")
    def get_metrics(self, service, **kw): return self._r("metrics")
    def list_deployments(self, **kw): return BackendResult(status="empty")
    def get_build(self, b): return BackendResult(status="empty")
    def search_commits(self, **kw): return self._r("commits")
    def get_commit(self, sha): return BackendResult(status="ok", records=[r for r in self._recs if r.evidence_id == f"commit:{sha}"])
    def get_config(self, service, version=None): return BackendResult(status="empty")
    def search_tickets(self, **kw): return BackendResult(status="empty")
    def get_ticket(self, k): return BackendResult(status="empty")
    def export_logs(self, start=None, end=None): return [r for r in self._recs if r.source == "logs"]


def _rec(eid, src, t="2026-01-01T00:05:00Z", content="ok"):
    return EvidenceRecord(evidence_id=eid, source=src, available_time=t, content=content)


@pytest.fixture
def env(tmp_path, monkeypatch):
    snaps, keys = tmp_path / "snaps", tmp_path / "keys"
    (snaps / IID).mkdir(parents=True)
    (snaps / IID / "incident.json").write_text(_incident().model_dump_json())
    (snaps / IID / "logs.jsonl").write_text('{"a": 1}\n')
    keys.mkdir()
    key = AnswerKey(incident_id=IID, scenario_number=1, scenario_type="misleading_symptom",
                    is_primary=True, diagnosable=True, expected_status="diagnosis",
                    component="payment-svc", mechanism="connection pool exhaustion",
                    decisive_evidence_ids=["log:1", "commit:abc1234", "log:missing"],
                    injected_fault={"faults": [{"change_id": "pool-fastpath",
                                                "family": "connection_leak"}]})
    (keys / f"{IID}.json").write_text(key.model_dump_json())
    state = {"records": [_rec("log:1", "logs"), _rec("metric:1", "metrics"),
                         _rec("commit:abc1234", "commits")], "raise_in": None}
    mod = types.ModuleType("blackbox.evidence.backend")
    mod.load_backend = lambda iid, ablation="none", snapshot_root=None: FakeBackend(
        state["records"], ablation, state["raise_in"])
    mod.list_incidents = lambda snapshot_root=None: [IID]
    monkeypatch.setitem(sys.modules, "blackbox.evidence.backend", mod)

    def run():
        return vs.validate(IID, snaps, keys, tmp_path / "nomanifest.json", ("none", "no_jira"))
    return snaps, state, run


def test_clean_snapshot_passes_and_reports_completeness(env):
    _, _, run = env
    r = run()
    assert r["ok"], (r["schema"], r["cutoff"], r["leakage"])
    info = "\n".join(r["completeness"]["info"])
    assert "decisive log:1: retrievable via" in info
    assert "decisive commit:abc1234: retrievable" in info
    assert "decisive log:missing: NOT retrievable" in info
    assert "tickets: available=False" in info       # unavailable source is not a failure


def test_cutoff_schema_and_leak_failures(env):
    snaps, state, run = env
    state["records"] = state["records"] + [
        _rec("log:2", "logs", t="2026-01-01T00:30:00Z"),                       # post-cutoff
        EvidenceRecord(evidence_id="log:3", source="logs"),                   # no available_time
        _rec("metric:9", "logs"),                                             # prefix mismatch
        _rec("log:4", "logs", content="note: Connection Pool Exhaustion via pool_fastpath")]
    (snaps / IID / "pool_fastpath_notes.txt").write_text("x")
    r = run()
    assert not r["ok"]
    assert any("log:2" in f for f in r["cutoff"]["failures"])
    assert any("log:3" in f and "available_time" in f for f in r["schema"]["failures"])
    assert any("metric:9" in f and "prefix" in f for f in r["schema"]["failures"])
    leaks = "\n".join(r["leakage"]["failures"])
    assert "connection pool exhaustion" in leaks and "pool_fastpath" in leaks
    assert "file/dir name 'pool_fastpath_notes.txt'" in leaks


def test_raising_method_is_schema_failure(env):
    _, state, run = env
    state["raise_in"] = "metrics"
    r = run()
    assert any("raised instead of returning" in f for f in r["schema"]["failures"])


def test_missing_answer_key_and_snapshot(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "blackbox.evidence.backend", None)  # import fails
    r = vs.validate("INC-0099", tmp_path, tmp_path, tmp_path / "m.json")
    assert not r["ok"]
    assert any("answer key missing" in f for f in r["schema"]["failures"])
    assert any("snapshot directory missing" in f for f in r["schema"]["failures"])
    assert r["backend_checked"] is False
