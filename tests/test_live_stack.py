"""Live-stack backend contract: explicit statuses, cutoff, isolation, redaction, no snapshot fallback.

Uses fake connectors and closed ports only; no Docker, model or network is needed.
"""
from __future__ import annotations

import builtins
import io
import sys
import urllib.error
from pathlib import Path

import pytest

from blackbox.connectors.jenkins import JenkinsConnector
from blackbox.connectors.jira import JiraConnector
from blackbox.connectors.loki import LokiLogs
from blackbox.connectors.prometheus import PrometheusMetrics
from blackbox.connectors.tempo import TempoTraces
from blackbox.evidence.live_stack import LiveStackBackend
from blackbox.investigator.tools import tools_for
from blackbox.schemas import Alert, BackendResult, EvidenceRecord, Incident

CUT = "2026-11-24T03:17:30.000Z"
INC = Incident(incident_id="LIVE-T", alert=Alert(alert_id="A", title="t", service="checkout-svc", signal="error_rate",
               condition="error_rate > 0.1 for 15s", fired_at="2026-11-24T03:17:10.000Z"),
               window_start="2026-11-24T03:15:00.000Z", window_end=CUT, investigation_cutoff=CUT,
               services=["checkout-svc", "payment-svc"], available_sources=["logs", "metrics", "traces"])
CLOSED = "http://127.0.0.1:9"     # nothing listens here


class Recorder:
    """Fake connector: records the arguments it was called with and answers from a script."""
    configured = True
    detail = "fake"

    def __init__(self, answer):
        self.calls, self.answer = [], answer

    def ready(self):
        return True, "ok"

    def __getattr__(self, name):
        def call(*a, **kw):
            self.calls.append((name, a, kw))
            return self.answer
        return call


def rec(i="log:x", content="x", truncated=False, **attrs):
    return EvidenceRecord(evidence_id=i, source="logs", content=content, truncated=truncated,
                          event_time="2026-11-24T03:16:00.000Z", attributes=attrs)


def test_unavailable_versus_no_match():
    down = LiveStackBackend(INC, loki=LokiLogs(CLOSED, timeout=1), prometheus=PrometheusMetrics(CLOSED, timeout=1),
                            tempo=TempoTraces(CLOSED, timeout=1))
    assert down.search_logs(limit=1).status == "unavailable"
    assert down.get_metrics("payment-svc").status == "unavailable"
    assert down.search_traces(service="payment-svc").status == "unavailable"
    empty = LiveStackBackend(INC, loki=Recorder(BackendResult(status="empty", total_matches=0)))
    r = empty.search_logs(query="nothing")
    assert r.status == "empty" and r.error is None


def test_window_never_passes_the_cutoff():
    loki, prom, tempo = (Recorder(BackendResult(status="empty")) for _ in range(3))
    b = LiveStackBackend(INC, loki=loki, prometheus=prom, tempo=tempo)
    b.search_logs(end="2026-11-24T04:00:00.000Z")
    b.get_metrics("payment-svc", end="2099-01-01T00:00:00Z")
    b.search_traces()
    b.get_trace("abc")
    assert loki.calls[0][2]["end"] == CUT and loki.calls[0][2]["start"] == INC.window_start
    assert prom.calls[0][2]["end"] == CUT
    assert tempo.calls[0][2]["end"] == CUT
    assert tempo.calls[1][2]["cutoff"] == CUT


@pytest.fixture
def no_local_credentials(monkeypatch):
    """Ignore this machine's .env.local and environment, so 'not configured' is really not configured."""
    import blackbox.connectors.jira as jira_mod
    monkeypatch.setattr(jira_mod, "load_env_local", lambda *a, **k: {})
    for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JENKINS_URL", "JENKINS_USER", "JENKINS_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)


def test_unconfigured_sources_are_isolated_and_honest(no_local_credentials):
    b = LiveStackBackend(INC, loki=Recorder(BackendResult(status="ok", records=[rec()])), jira=JiraConnector("", "", ""),
                         jenkins=JenkinsConnector("", "", ""))
    assert b.search_logs().status == "ok"                       # Loki unaffected by Jira/Jenkins
    t = b.search_tickets(query="pool")
    assert t.status == "unavailable" and "not configured" in t.error
    assert b.list_jenkins_builds().status == "unavailable"
    assert b.get_metrics("payment-svc").status == "unavailable"   # Prometheus missing -> said, not faked
    st = {s.source: s for s in b.source_status()}
    assert not st["tickets"].available and "not configured" in st["tickets"].detail
    assert not st["metrics"].available
    tools = tools_for(b)
    assert "open_record" in tools and "list_jenkins_builds" not in tools


def test_secrets_are_redacted(monkeypatch):
    jira = JiraConnector("https://example.invalid", "a@b.c", "SECRET-TOKEN-123")
    err = urllib.error.HTTPError("u", 500, "boom SECRET-TOKEN-123", {}, io.BytesIO(b""))
    out = jira._fail(err)
    assert "SECRET-TOKEN-123" not in (out.error or "")
    jk = JenkinsConnector("http://127.0.0.1:9", "u", "JK-TOKEN-999", timeout=1)
    assert "JK-TOKEN-999" not in jk._redact("token JK-TOKEN-999 leaked")
    r = jk.list_builds(job="demo")
    assert r.status == "unavailable" and "JK-TOKEN-999" not in (r.error or "")
    assert "SECRET" not in repr(jira.__dict__.get("detail"))


def test_live_stack_never_reads_snapshots_or_local_jsonl(monkeypatch):
    seen = []
    real_open = builtins.open

    def guard(file, *a, **kw):
        p = str(file).replace("\\", "/")
        if "scenarios/snapshots" in p or "/telemetry/" in p:
            seen.append(p)
        return real_open(file, *a, **kw)

    monkeypatch.setattr(builtins, "open", guard)
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (seen.append(str(self)) if "snapshots" in str(self)
                                                                   else None) or "")
    down = LiveStackBackend(INC, loki=LokiLogs(CLOSED, timeout=1), prometheus=PrometheusMetrics(CLOSED, timeout=1),
                            tempo=TempoTraces(CLOSED, timeout=1))
    for call in (lambda: down.search_logs(), lambda: down.get_metrics("payment-svc"), lambda: down.search_traces(),
                 lambda: down.get_trace("abc"), lambda: down.list_deployments(), lambda: down.search_commits(),
                 lambda: down.get_config("payment-svc"), lambda: down.search_tickets()):
        assert call().status in ("unavailable", "error", "empty")
    assert seen == []
    src = Path(sys.modules["blackbox.evidence.live_stack"].__file__)
    text = real_open(src, encoding="utf-8").read()
    assert "evidence.backend" not in text and "snapshots" not in text.split('"""', 2)[2]


def test_open_record_returns_the_full_original_in_parts():
    long = "x" * 1300
    loki = Recorder(BackendResult(status="ok", records=[rec("log:long", long[:600], True, full_text=long)]))
    b = LiveStackBackend(INC, loki=loki)
    b.search_logs()
    r = b.open_record("log:long")
    assert r.status == "ok" and len(r.records) == 3
    assert "".join(x.content for x in r.records) == long
    assert b.open_record("log:unknown").status == "empty"


def test_provenance_is_stamped_on_every_record():
    b = LiveStackBackend(INC, loki=Recorder(BackendResult(status="ok", records=[rec()])))
    r = b.search_logs()
    assert r.records[0].attributes["backend"] == "loki" and r.records[0].attributes["mode"] == "live"
