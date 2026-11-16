"""Agent 4 QA: blackbox/connectors/local_logs.py (Agent 2) on a TEST FIXTURE telemetry dir.

Runs without the demo: the fixture directory is written into tmp_path in the same layout the
demo services use (<dir>/logs/<service>.jsonl). Every line says "QA FIXTURE".
"""
from __future__ import annotations

import json

import pytest

ll = pytest.importorskip("blackbox.connectors.local_logs")

LINES = {
    "checkout-svc": [
        {"ts": "2026-11-19T10:00:01.000Z", "level": "INFO", "service": "checkout-svc", "msg": "QA FIXTURE order received"},
        {"ts": "2026-11-19T10:00:02.000Z", "level": "ERROR", "service": "checkout-svc",
         "msg": "QA FIXTURE payment call timed out after 250ms", "trace_id": "t1"},
        {"ts": "2026-11-19T10:05:00.000Z", "level": "ERROR", "service": "checkout-svc", "msg": "QA FIXTURE late line"},
    ],
    "payment-svc": [
        {"ts": "2026-11-19T10:00:03.000Z", "level": "ERROR", "service": "payment-svc",
         "msg": "QA FIXTURE charge failed", "error": "OperationalError: database is locked " + "x" * 700},
    ],
}


@pytest.fixture
def tdir(tmp_path):
    logs = tmp_path / "telemetry" / "logs"
    logs.mkdir(parents=True)
    for svc, rows in LINES.items():
        text = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)
        if svc == "checkout-svc":
            text += '{"ts":"2026-11-19T10:00:04.000Z","level":"INFO","serv'     # line still being written
        (logs / f"{svc}.jsonl").write_text(text, encoding="utf-8")
    return tmp_path / "telemetry"


def test_records_are_verbatim_live_lines(tdir):
    res = ll.LocalLogs(tdir).search(service="checkout-svc", level="ERROR")
    assert res.status == "ok"
    r = next(x for x in res.records if "timed out" in x.content)
    assert json.loads(r.content) == LINES["checkout-svc"][1]
    assert r.evidence_id.startswith("log:") and r.fixture is False
    assert r.attributes["live"] is True and r.attributes["trace_id"] == "t1" and "retrieved_at" in r.attributes
    assert r.available_time == "2026-11-19T10:00:02.000Z"


def test_reading_never_modifies_files(tdir):
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (tdir / "logs").iterdir()}
    c = ll.LocalLogs(tdir)
    c.search(limit=100)
    c.export()
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (tdir / "logs").iterdir()}
    assert sorted(p.name for p in (tdir / "logs").iterdir()) == ["checkout-svc.jsonl", "payment-svc.jsonl"]


def test_missing_dir_and_unknown_service_are_unavailable(tmp_path, tdir):
    assert ll.LocalLogs(tmp_path / "nope").search().status == "unavailable"
    assert ll.LocalLogs(tdir).search(service="ghost-svc").status == "unavailable"
    assert ll.LocalLogs(tmp_path / "nope").export() == []


def test_partial_line_window_limit_and_truncation(tdir):
    c = ll.LocalLogs(tdir)
    every = c.export()
    assert len(every) == 4                                   # partial last line skipped
    early = c.search(end="2026-11-19T10:01:00Z", limit=100)
    assert all(r.available_time <= "2026-11-19T10:01:00Z" for r in early.records) and early.total_matches == 3
    lim = c.search(limit=1)
    assert len(lim.records) == 1 and lim.truncated and lim.total_matches == 4
    big = next(r for r in every if r.service == "payment-svc")
    assert big.truncated and len(big.content) <= 600


def test_bad_arguments_are_an_error_not_an_exception(tdir):
    assert ll.LocalLogs(tdir).search(start="not-a-time").status == "error"


@pytest.mark.xfail(strict=True, reason="QA-F10 (Agent 2, LOW): local_logs query tokens match substrings ('out' "
                   "matches 'checkout-svc'), unlike the snapshot backend which Agent 1 changed to whole words")
def test_query_matches_whole_words_like_the_snapshot_backend(tdir):
    res = ll.LocalLogs(tdir).search(query="out", limit=100)
    assert res.status == "empty"


@pytest.mark.xfail(strict=True, reason="QA-F11 (Agent 2, LOW): one log line with an unparseable 'ts' makes the "
                   "whole search return status=error instead of skipping that line")
def test_one_bad_timestamp_does_not_fail_the_search(tdir):
    with open(tdir / "logs" / "payment-svc.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"ts":"garbage","level":"INFO","service":"payment-svc","msg":"QA FIXTURE bad ts"}\n')
    assert ll.LocalLogs(tdir).search(service="checkout-svc").status == "ok"   # other file unaffected
    assert ll.LocalLogs(tdir).search(service="payment-svc").status == "ok"


def test_ids_depend_on_absolute_path(tdir, tmp_path):
    """Characterisation for QA-N7: the same lines read from another location get different ids."""
    import shutil
    moved = tmp_path / "moved"
    shutil.copytree(tdir, moved)
    a = [r.evidence_id for r in ll.LocalLogs(tdir).export()]
    b = [r.evidence_id for r in ll.LocalLogs(moved).export()]
    assert a and a != b
