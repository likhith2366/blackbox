"""Forward the demo services' own spans and metric points to the observability stack, live (OTLP/HTTP JSON).

    python -m demo.telemetry_bridge [--dir <telemetry dir>] [--otlp http://127.0.0.1:4318] [--from-start]

The shop services (libs/telemetry in the company repo) write every finished span and every metric point
as one JSON line under <telemetry dir>/{spans,metrics}/<service>.jsonl. This bridge plays the role of an
OpenTelemetry SDK exporter: it tails those files as they are written and sends each record, unchanged in
content and timestamp, to the Alloy collector, which forwards spans to Tempo and metrics to Prometheus.
Logs are tailed by Alloy itself (tools/observability/alloy/config.alloy).

By default it starts at the END of each file, so only telemetry produced after the bridge starts is sent:
nothing prewritten is replayed as live. --from-start also sends what the running services wrote before.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from demo.paths import RUNTIME

KIND = {"internal": 1, "server": 2, "client": 3, "producer": 4, "consumer": 5}


def _ns(ts: str) -> int:
    s = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(round(dt.timestamp() * 1e6)) * 1000


def _attr(k, v):
    if isinstance(v, bool):
        val = {"boolValue": v}
    elif isinstance(v, int):
        val = {"intValue": str(v)}
    elif isinstance(v, float):
        val = {"doubleValue": v}
    else:
        val = {"stringValue": v if isinstance(v, str) else json.dumps(v)}
    return {"key": str(k), "value": val}


def _resource(service: str, extra: dict | None = None):
    attrs = {"service.name": service, "telemetry.source": "demo-shop libs/telemetry JSONL (forwarded live)"}
    attrs.update({str(k): v for k, v in (extra or {}).items()})
    return {"attributes": [_attr(k, v) for k, v in attrs.items()]}


def span_payload(records: list[dict]) -> dict:
    by_service: dict[str, list] = {}
    res_of: dict[str, dict] = {}
    for r in records:
        svc = r.get("service", "unknown")
        res_of.setdefault(svc, r.get("resource") or {})
        span = {"traceId": r["trace_id"], "spanId": r["span_id"], "name": r["name"],
                "kind": KIND.get(r.get("kind", "internal"), 1),
                "startTimeUnixNano": str(_ns(r["start"])), "endTimeUnixNano": str(_ns(r["end"])),
                "attributes": [_attr(k, v) for k, v in (r.get("attributes") or {}).items()],
                "status": {"code": 2 if r.get("status") == "error" else 1,
                           "message": r.get("status_message") or ""}}
        if r.get("parent_span_id"):
            span["parentSpanId"] = r["parent_span_id"]
        by_service.setdefault(svc, []).append(span)
    return {"resourceSpans": [{"resource": _resource(s, res_of[s]),
                               "scopeSpans": [{"scope": {"name": "demo-shop.telemetry"}, "spans": sp}]}
                              for s, sp in by_service.items()]}


def metric_payload(records: list[dict]) -> dict:
    by_service: dict[str, dict[str, list]] = {}
    for r in records:
        pts = by_service.setdefault(r.get("service", "unknown"), {}).setdefault(r["metric"], [])
        pts.append({"timeUnixNano": str(_ns(r["ts"])), "asDouble": float(r["value"])})
    return {"resourceMetrics": [{"resource": _resource(s),
                                 "scopeMetrics": [{"scope": {"name": "demo-shop.telemetry"},
                                                   "metrics": [{"name": m, "gauge": {"dataPoints": p}}
                                                               for m, p in metrics.items()]}]}
                                for s, metrics in by_service.items()]}


class Tail:
    """Complete new lines from every <dir>/*.jsonl, remembering a byte offset per file."""

    def __init__(self, folder: Path, from_start: bool):
        self.folder = folder
        self.offsets: dict[Path, int] = {}
        if not from_start:
            for p in folder.glob("*.jsonl"):
                self.offsets[p] = p.stat().st_size

    def read(self) -> list[dict]:
        out = []
        for p in sorted(self.folder.glob("*.jsonl")):
            off = self.offsets.get(p, 0)
            if p.stat().st_size < off:          # file was recreated
                off = 0
            with open(p, "rb") as fh:
                fh.seek(off)
                data = fh.read()
            if b"\n" not in data:
                continue
            complete = data[: data.rfind(b"\n") + 1]
            self.offsets[p] = off + len(complete)
            for raw in complete.split(b"\n"):
                if raw.strip():
                    try:
                        out.append(json.loads(raw))
                    except ValueError:
                        pass
        return out


def post(url: str, body: dict) -> None:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=None, help="telemetry dir (default: demo/.runtime/telemetry/stack)")
    ap.add_argument("--otlp", default="http://127.0.0.1:4318")
    ap.add_argument("--from-start", action="store_true")
    ap.add_argument("--once", action="store_true", help="send what is pending and exit (tests)")
    a = ap.parse_args(argv)
    tdir = a.dir or (RUNTIME / "telemetry" / "stack")
    (tdir / "spans").mkdir(parents=True, exist_ok=True)
    (tdir / "metrics").mkdir(parents=True, exist_ok=True)
    spans, metrics = Tail(tdir / "spans", a.from_start), Tail(tdir / "metrics", a.from_start)
    print(f"bridge: {tdir} -> {a.otlp} ({'from start' if a.from_start else 'new records only'})", flush=True)
    sent = {"spans": 0, "points": 0}
    s, m = [], []          # kept until the collector accepts them
    while True:
        try:
            s += spans.read()
            m += metrics.read()
            ns, nm = len(s), len(m)
            if s:
                post(f"{a.otlp}/v1/traces", span_payload(s[:5000]))
                sent["spans"] += len(s[:5000])
                s = s[5000:]
            if m:
                post(f"{a.otlp}/v1/metrics", metric_payload(m[:5000]))
                sent["points"] += len(m[:5000])
                m = m[5000:]
            if ns or nm:
                print(f"{datetime.now(timezone.utc):%H:%M:%S} sent {ns} spans, {nm} metric points "
                      f"(total {sent['spans']} / {sent['points']})", flush=True)
        except (urllib.error.URLError, OSError) as exc:
            print(f"collector not reachable ({exc}); retrying", file=sys.stderr, flush=True)
        if a.once:
            return 0
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
