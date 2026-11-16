"""Agent 4 QA: blackbox/evidence/backend.py (Agent 2) against interface.py and CONTRACT.md.

Runs on the labelled QA fixture snapshot (tests/integration/fixtures/qa_snapshot.py), so it
needs no pilot. Skips if the backend module does not exist yet.
"""
from __future__ import annotations

import inspect
import json

import pytest

backend_mod = pytest.importorskip("blackbox.evidence.backend")

from blackbox.evidence.interface import EvidenceBackend  # noqa: E402
from blackbox.investigator.tools import TOOLS  # noqa: E402
from blackbox.schemas import BackendResult  # noqa: E402


def _ids(res: BackendResult) -> list[str]:
    return [r.evidence_id for r in res.records]


def _text(res: BackendResult) -> str:
    return json.dumps([r.model_dump() for r in res.records])


# ----------------------------------------------------------------- signatures
PROTOCOL_METHODS = [n for n, v in vars(EvidenceBackend).items()
                    if callable(v) and not n.startswith("_")]


def test_entry_point_signatures_match_contract_s4():
    lb = inspect.signature(backend_mod.load_backend)
    assert list(lb.parameters) == ["incident_id", "ablation", "snapshot_root"]
    assert lb.parameters["ablation"].default == "none" and lb.parameters["snapshot_root"].default is None
    li = inspect.signature(backend_mod.list_incidents)
    assert list(li.parameters) == ["snapshot_root"] and li.parameters["snapshot_root"].default is None


@pytest.mark.parametrize("name", PROTOCOL_METHODS)
def test_method_signature_matches_interface(name):
    impl = getattr(backend_mod.SnapshotBackend, name)
    want = inspect.signature(getattr(EvidenceBackend, name))
    got = inspect.signature(impl)
    assert list(got.parameters) == list(want.parameters), name
    for p, wp in want.parameters.items():
        assert got.parameters[p].default == wp.default, f"{name}.{p} default"


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_every_tool_argument_is_a_backend_parameter(tool):
    model, method, _ = TOOLS[tool]
    params = set(inspect.signature(getattr(EvidenceBackend, method)).parameters) - {"self"}
    assert set(model.model_fields) <= params, (tool, set(model.model_fields) - params)


def test_backend_is_an_evidence_backend(mini_snapshot):
    b = backend_mod.load_backend("INC-9001", snapshot_root=mini_snapshot)
    assert isinstance(b, EvidenceBackend)
    assert b.incident().incident_id == "INC-9001"
    assert backend_mod.list_incidents(mini_snapshot) == ["INC-9001"]


def test_missing_incident_and_bad_id_raise_clear_errors(mini_snapshot):
    with pytest.raises(FileNotFoundError, match="INC-9999"):
        backend_mod.load_backend("INC-9999", snapshot_root=mini_snapshot)
    with pytest.raises(ValueError):
        backend_mod.load_backend("../INC-9001", snapshot_root=mini_snapshot)
    with pytest.raises(ValueError):
        backend_mod.load_backend("INC-9001", ablation="no_logs", snapshot_root=mini_snapshot)


# ----------------------------------------------------------------- cutoff
def test_nothing_after_cutoff_is_returned(mini_snapshot):
    b = backend_mod.load_backend("INC-9001", snapshot_root=mini_snapshot)
    everything = [b.search_logs(limit=50), b.search_traces(limit=50), b.get_metrics("checkout-svc"),
                  b.get_ticket("DEMO-9"), b.search_tickets(), b.list_deployments(), b.search_commits()]
    blob = "".join(_text(r) for r in everything) + json.dumps([r.model_dump() for r in b.export_logs()])
    assert "POST-CUTOFF" not in blob
    assert "trace:t2" not in blob and "0.9" not in _text(b.get_metrics("checkout-svc"))
    cutoff = b.incident().investigation_cutoff
    for res in everything:
        for r in res.records:
            assert r.available_time is not None and r.available_time <= cutoff, r.evidence_id


# ----------------------------------------------------------------- ablations
def test_no_jira_removes_tickets_and_ticket_keys(mini_snapshot):
    b = backend_mod.load_backend("INC-9001", ablation="no_jira", snapshot_root=mini_snapshot)
    assert b.search_tickets().status == "unavailable" and b.get_ticket("DEMO-9").status == "unavailable"
    blob = _text(b.search_commits()) + _text(b.search_logs(limit=50)) + _text(b.get_commit(backend_mod_sha()))
    assert "DEMO-9" not in blob and "DEMO-3" not in blob
    st = {s.source: s for s in b.source_status()}
    assert st["tickets"].available is False
    assert "tickets" not in b.incident().available_sources


def backend_mod_sha():
    return "b" * 7


def test_no_provenance_unlinks_deployments_and_redacts_startup_lines(mini_snapshot):
    b = backend_mod.load_backend("INC-9001", ablation="no_provenance", snapshot_root=mini_snapshot)
    dep = b.list_deployments()
    assert dep.status == "ok"
    blob = _text(dep) + _text(b.search_logs(query="starting"))
    for leak in ("b-2", "b-1", "bbbbbbb", "aaaaaaa", "sha256:", "1.1.0", "1.0.0"):
        assert leak not in blob, leak
    assert b.get_build("b-2").status == "unavailable"
    assert b.search_commits().status == "ok"          # commits stay searchable


def test_no_traces_removes_spans_but_keeps_logs(mini_snapshot):
    b = backend_mod.load_backend("INC-9001", ablation="no_traces", snapshot_root=mini_snapshot)
    assert b.search_traces().status == "unavailable" and b.get_trace("t1").status == "unavailable"
    assert b.search_logs().status == "ok"


# ----------------------------------------------------------------- caching and connector failure
def test_store_cache_is_keyed_by_snapshot_and_ablation(mini_snapshot):
    a = backend_mod.load_backend("INC-9001", ablation="none", snapshot_root=mini_snapshot)
    j = backend_mod.load_backend("INC-9001", ablation="no_jira", snapshot_root=mini_snapshot)
    assert a._st is not j._st
    assert a.search_tickets().status == "ok" and j.search_tickets().status == "unavailable"
    again = backend_mod.load_backend("INC-9001", ablation="none", snapshot_root=mini_snapshot)
    assert again._st is a._st                      # shared immutable store, same key
    # returned records are copies: mutating one must not change the shared store
    r = a.search_logs().records[0]
    r.content = "MUTATED"
    assert "MUTATED" not in _text(again.search_logs())


def test_connector_failure_is_per_backend_instance(tmp_path):
    from qa_snapshot import write_mini_snapshot
    root = tmp_path / "s"
    write_mini_snapshot(root / "INC-9002", runtime={"failures": [
        {"source": "logs", "after_successful_calls": 1, "error": "QA FIXTURE log gateway 502"}], "coverage": {}})
    b = backend_mod.load_backend("INC-9002", snapshot_root=root)
    assert b.search_logs().status == "ok"
    second = b.search_logs()
    assert second.status == "error" and "502" in (second.error or "")
    assert b.get_metrics("checkout-svc").status == "ok"   # other sources unaffected
    fresh = backend_mod.load_backend("INC-9002", snapshot_root=root)
    assert fresh.search_logs().status == "ok"              # a new run starts healthy


# ----------------------------------------------------------------- grouping vs answer-key ids
def test_log_grouping_representative_depends_on_query_window(mini_snapshot):
    """Characterisation (passes): the same log template is represented by different ids
    depending on the requested window. The answer key's decisive log id is the FIRST hit
    after the change, so exact-id decisive coverage needs AnswerKey equivalents
    (see test_qa_eval_contract.py::test_decisive_coverage_uses_recorded_equivalents)."""
    b = backend_mod.load_backend("INC-9001", snapshot_root=mini_snapshot)
    whole = [r for r in b.search_logs(query="timed out").records]
    late = [r for r in b.search_logs(query="timed out", start="2026-11-19T10:02:00Z").records]
    assert whole[0].evidence_id == "log:qa0002" and late[0].evidence_id == "log:qa0004"


# regression for QA-F07 (fixed by Agent 1 at 9335b82)
def test_search_logs_truncated_only_when_limit_cut_results(mini_snapshot):
    b = backend_mod.load_backend("INC-9001", snapshot_root=mini_snapshot)
    res = b.search_logs(query="timed out", limit=20)
    assert len(res.records) == 1                       # one template, three lines
    assert res.truncated is False
