"""Contract tests for the evidence backend over real built snapshots (Agent 2).

Uses only the public API (load_backend / list_incidents / EvidenceBackend methods).
Tests that need a specific snapshot are skipped when it has not been built yet.
"""
from __future__ import annotations

import json
import re

import pytest

from blackbox.evidence.backend import list_incidents, load_backend
from blackbox.evidence.interface import EvidenceBackend
from scenarios.verify import SNAPSHOTS

PILOT = "INC-0000"
CONNECTOR = "INC-8290"     # replay with a failing log connector
NOT_OK = ("ok", "empty")


def need(inc):
    if not (SNAPSHOTS / inc / "incident.json").exists():
        pytest.skip(f"{inc} not built")


def tickets(inc):
    return json.loads((SNAPSHOTS / inc / "tickets.json").read_text(encoding="utf-8"))


def test_list_and_protocol():
    need(PILOT)
    assert PILOT in list_incidents()
    be = load_backend(PILOT)
    assert isinstance(be, EvidenceBackend)
    assert be.incident().incident_id == PILOT
    with pytest.raises(ValueError):
        load_backend("../evaluator_private")


def test_post_cutoff_ticket_items_hidden():
    need(PILOT)
    be = load_backend(PILOT)
    cutoff = be.incident().investigation_cutoff
    late = [(t["key"], c["id"]) for t in tickets(PILOT) for c in t["comments"] if c["at"] > cutoff]
    assert late, "pilot must contain a post-cutoff ticket update"
    for key, cid in late:
        res = be.get_ticket(key)
        ids = {r.evidence_id for r in res.records}
        assert f"jira:{key}#{cid}" not in ids
        assert all(r.available_time <= cutoff for r in res.records)
    # the alert ticket is resolved only after the cutoff: status must be as of the cutoff
    alert_ticket = be.get_ticket("OPS-30").records[0]
    assert "status: Resolved" not in alert_ticket.content


def test_no_record_after_cutoff_in_logs_and_export():
    need(PILOT)
    be = load_backend(PILOT)
    cutoff = be.incident().investigation_cutoff
    exp = be.export_logs()
    assert exp and all(r.available_time <= cutoff for r in exp)
    assert [r.event_time for r in exp] == sorted(r.event_time for r in exp)
    raw_last = max(json.loads(l)["ts"] for l in (SNAPSHOTS / PILOT / "logs.jsonl").read_text().splitlines())
    assert raw_last > cutoff  # the capture continues past the cutoff, so the filter is exercised


def test_limits_and_determinism():
    need(PILOT)
    be = load_backend(PILOT)
    a = be.search_logs(limit=2)
    b = be.search_logs(limit=2)
    assert [r.evidence_id for r in a.records] == [r.evidence_id for r in b.records]
    assert len(a.records) <= 2 and a.truncated
    for r in be.search_logs(service="checkout-svc", limit=20).records:
        assert len(r.content) <= 600
    tr = be.search_traces(status="error", limit=3)
    assert tr.status == "ok" and len(tr.records) <= 3 and tr.truncated


@pytest.mark.parametrize("ablation,source", [("no_jira", "tickets"), ("no_provenance", "builds"),
                                             ("no_traces", "traces")])
def test_ablation_removes_source(ablation, source):
    need(PILOT)
    be = load_backend(PILOT, ablation)
    st = {s.source: s for s in be.source_status()}
    assert not st[source].available
    assert source not in be.incident().available_sources


def test_no_jira_hides_ticket_keys_everywhere():
    need(PILOT)
    be = load_backend(PILOT, "no_jira")
    assert be.search_tickets().status not in NOT_OK
    pat = re.compile(r"\b(?:DEMO|OPS)-\d+\b")
    for r in be.search_commits(limit=50).records:
        assert not pat.search(r.content + r.title)
    sha = be.search_commits(limit=1).records[0].evidence_id
    assert not pat.search(be.get_commit(sha).records[0].content)


def test_no_provenance_strips_deploy_links_and_startup_ids():
    need(PILOT)
    be = load_backend(PILOT, "no_provenance")
    for r in be.list_deployments().records:
        assert r.version is None
        assert not re.search(r"\bb-\d+\b|sha256:|commit=", r.content)
    assert be.get_build("b-1201").status not in NOT_OK
    starts = be.search_logs(query="starting", limit=50).records
    assert starts
    for r in starts:
        assert not re.search(r'"(commit|build_id|artifact_digest)":"(?!\[redacted\])', r.content)
    assert be.search_commits(limit=1).status == "ok"   # commits stay searchable


def test_no_traces_keeps_trace_ids_as_strings():
    need(PILOT)
    be = load_backend(PILOT, "no_traces")
    assert be.search_traces().status not in NOT_OK
    assert be.get_trace("0" * 32).status not in NOT_OK
    assert any("trace_id" in r.content for r in be.search_logs(level="ERROR").records)


def test_cache_is_per_ablation():
    need(PILOT)
    full = load_backend(PILOT)
    nj = load_backend(PILOT, "no_jira")
    assert full.search_tickets().status == "ok"
    assert nj.search_tickets().status not in NOT_OK
    assert load_backend(PILOT).search_tickets().status == "ok"


def test_connector_failure_mid_investigation():
    need(CONNECTOR)
    be = load_backend(CONNECTOR)
    statuses = [be.search_logs(limit=5).status for _ in range(4)]
    assert statuses[:2] == ["ok", "ok"]
    assert statuses[2:] == ["error", "error"]
    assert be.get_metrics("payment-svc").status == "ok"     # other sources keep working
    fresh = load_backend(CONNECTOR)                           # counters are per backend instance
    assert fresh.search_logs(limit=5).status == "ok"
