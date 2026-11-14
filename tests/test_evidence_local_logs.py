"""Live local-log connector over real service log files (skips if no capture is on disk)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from blackbox.connectors.local_logs import LocalLogs

CAP = Path(__file__).resolve().parents[1] / "demo" / ".runtime" / "captures" / "INC-0000" / "telemetry"


@pytest.fixture
def ll():
    if not (CAP / "logs").is_dir():
        pytest.skip("no local capture telemetry on this machine")
    return LocalLogs(CAP)


def test_verbatim_and_bounded(ll):
    res = ll.search(service="payment-svc", level="ERROR", limit=3)
    assert res.status == "ok" and len(res.records) == 3 and res.truncated and res.total_matches > 3
    lines = (CAP / "logs" / "payment-svc.jsonl").read_text(encoding="utf-8").splitlines()
    for r in res.records:
        n = r.attributes["line"]
        assert r.attributes["live"] is True and r.fixture is False
        assert lines[n].startswith(r.content)
        assert json.loads(lines[n])["ts"] == r.event_time


def test_stable_ids_and_statuses(ll):
    a = ll.search(query="charge failed", limit=5)
    b = ll.search(query="charge failed", limit=5)
    assert [r.evidence_id for r in a.records] == [r.evidence_id for r in b.records]
    assert ll.search(service="no-such-svc").status == "unavailable"
    assert ll.search(query="zzzz-no-such-token").status == "empty"
    assert LocalLogs(CAP / "missing").search().status == "unavailable"
    exp = ll.export()
    assert [r.event_time for r in exp] == sorted(r.event_time for r in exp)
