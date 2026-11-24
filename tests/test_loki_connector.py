"""Loki connector and shipper against an in-process fake Loki (the push and query_range HTTP API subset used).

The fake answers like Loki 3: structured metadata comes back merged into each stream's labels.
No real Loki, model or network is needed.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from blackbox.connectors.loki import LokiLogs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import push_logs_to_loki as shipper  # noqa: E402


class FakeLoki:
    def __init__(self):
        self.entries = []          # (labels incl. metadata, ts_ns, line)
        self.queries = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                self.send_response(code)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                if u.path == "/ready":
                    return self._send(200, "ready")
                if u.path != "/loki/api/v1/query_range":
                    return self._send(404, "")
                q = urllib.parse.parse_qs(u.query)
                expr, start, end, limit = q["query"][0], int(q["start"][0]), int(q["end"][0]), int(q["limit"][0])
                fake.queries.append(expr)
                sel = dict(re.findall(r'(\w+)="([^"]*)"', expr.split("}")[0]))
                m = re.search(r"\|~ `(.*)`", expr)
                rx = re.compile(m.group(1)) if m else None
                streams = {}
                for labels, t, line in sorted(fake.entries, key=lambda e: e[1]):
                    if not (start <= t < end) or any(labels.get(k) != v for k, v in sel.items()):
                        continue
                    if rx and not rx.search(line):
                        continue
                    streams.setdefault(tuple(sorted(labels.items())), []).append([str(t), line])
                result = [{"stream": dict(k), "values": v} for k, v in streams.items()]
                return self._send(200, {"status": "success", "data": {"resultType": "streams", "result": result}})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                for s in body["streams"]:
                    for v in s["values"]:
                        labels = dict(s["stream"], **(v[2] if len(v) > 2 else {}))
                        fake.entries.append((labels, int(v[0]), v[1]))
                return self._send(204, "")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def loki():
    f = FakeLoki()
    yield f
    f.server.shutdown()


W = {"start": "2026-11-20T00:00:00Z", "end": "2026-11-21T00:00:00Z"}   # the snapshot's day (default lookback is 1 h)


def _snapshot_rows(inc="INC-9001"):
    p = ROOT / "scenarios" / "snapshots" / inc / "logs.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_snapshot_replay_keeps_ids_and_lines(loki):
    shipper.snapshot(loki.url, "INC-9001")
    rows = _snapshot_rows()
    assert len(loki.entries) == len(rows)
    lk = LokiLogs(loki.url, incident="INC-9001")
    r = lk.search(service="payment-svc", level="ERROR", query="pool acquire", limit=5, **W)
    assert r.status == "ok" and r.records
    snap_ids = {x["evidence_id"] for x in rows}
    assert all(rec.evidence_id in snap_ids for rec in r.records)            # same ids the answer keys use
    assert all("pool acquire" in rec.content for rec in r.records)
    assert 'incident="INC-9001"' in loki.queries[-1] and 'level="ERROR"' in loki.queries[-1]


def test_whole_word_matching_like_the_other_backends(loki):
    shipper.snapshot(loki.url, "INC-9001")
    lk = LokiLogs(loki.url, incident="INC-9001")
    assert lk.search(query="heckout", **W).status == "empty"        # Loki's regex hits inside "checkout"; client drops it
    assert lk.search(query="checkout", **W).status == "ok"


def test_time_window_and_cutoff_are_applied(loki):
    shipper.snapshot(loki.url, "INC-9001")
    lk = LokiLogs(loki.url, incident="INC-9001")
    all_ = lk.export(**W)
    first, last = all_[0].event_time, all_[-1].event_time
    assert first < last
    later = lk.export(start=all_[len(all_) // 2].event_time, end=W["end"])
    assert 0 < len(later) < len(all_)


def test_unreachable_loki_is_reported_not_hidden():
    lk = LokiLogs("http://127.0.0.1:9", timeout=1)
    ok, why = lk.ready()
    assert not ok
    r = lk.search(limit=1)
    assert r.status == "unavailable" and "Loki not reachable" in r.error
    assert lk.export() == []


def test_label_values_are_validated():
    lk = LokiLogs("http://127.0.0.1:9")
    assert lk.search(service='x"} or {job=~".+').status == "error"
    assert 'service="payment-svc"' in lk.logql(service="payment-svc")
    assert lk.logql(query="a`b").count("`") == 2


def test_live_backend_serves_search_logs_from_loki(loki):
    from blackbox.evidence.live import LiveBackend
    from blackbox.evidence.backend import load_backend
    shipper.snapshot(loki.url, "INC-9001")
    lb = LiveBackend(load_backend("INC-9001"), github=None, local_logs=LokiLogs(loki.url, incident="INC-9001"))
    r = lb.search_logs(level="ERROR", limit=3, **W)
    assert r.status == "ok"
    st = {s.source: s for s in lb.source_status()}
    assert "Loki" in st["logs"].detail
    cutoff = lb.incident().investigation_cutoff
    assert all(rec.event_time <= cutoff for rec in lb.export_logs())


def test_telemetry_mode_ships_jsonl_files(loki, tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    lines = [{"ts": "2026-11-20T03:44:19.577Z", "level": "ERROR", "service": "payment-svc", "msg": "db pool acquire timed out"},
             {"ts": "2026-11-20T03:44:20.000Z", "level": "INFO", "service": "payment-svc", "msg": "charge approved"}]
    (logs / "payment-svc.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    shipper.telemetry(loki.url, tmp_path, follow=False)
    r = LokiLogs(loki.url).search(level="ERROR", start="2026-11-20T03:00:00Z", end="2026-11-20T04:00:00Z")
    assert r.status == "ok" and r.total_matches == 1 and r.records[0].service == "payment-svc"
