"""Ship BlackBox demo logs into Grafana Loki (POST /loki/api/v1/push). Lines are sent verbatim.

    # live demo: the JSONL files the running services write (follow = keep tailing)
    python scripts/push_logs_to_loki.py telemetry [--dir demo/.runtime/telemetry/live] [--follow]
    # a frozen incident snapshot, replayed with its original timestamps and evidence ids
    python scripts/push_logs_to_loki.py snapshot --incident INC-9001

Labels: job="blackbox-demo", service, level (+ incident for snapshots). A snapshot line also carries its
snapshot evidence id as Loki structured metadata, so a replayed investigation cites the same ids the
answer keys use. URL: --url, env BLACKBOX_LOKI_URL, default http://127.0.0.1:3100.
Grafana Alloy or Promtail can replace the `telemetry` mode in a real deployment (same labels).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackbox.connectors.local_logs import resolve_telemetry_dir  # noqa: E402
from blackbox.connectors.loki import JOB, resolve_url  # noqa: E402

BATCH = 1000


def ns(ts: str) -> int:
    s = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(round(dt.timestamp() * 1e6)) * 1000


def push(url: str, entries: list[tuple[dict, int, str, dict | None]]) -> int:
    """entries: (labels, ts_ns, line, structured_metadata). Returns lines sent."""
    sent = 0
    for i in range(0, len(entries), BATCH):
        streams = defaultdict(list)
        for labels, t, line, meta in entries[i:i + BATCH]:
            v = [str(t), line] + ([meta] if meta else [])
            streams[tuple(sorted(labels.items()))].append(v)
        body = {"streams": [{"stream": dict(k), "values": sorted(v, key=lambda x: int(x[0]))} for k, v in streams.items()]}
        req = urllib.request.Request(f"{url}/loki/api/v1/push", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status not in (200, 204):
                raise SystemExit(f"Loki push failed: HTTP {r.status}")
        sent += len(entries[i:i + BATCH])
    return sent


def parse(line: str, fallback_service: str):
    try:
        rec = json.loads(line)
        return rec, ns(rec["ts"])
    except (ValueError, KeyError, TypeError):
        return None, None


def telemetry(url: str, tdir: Path, follow: bool):
    logs = tdir / "logs"
    if not logs.is_dir():
        raise SystemExit(f"no log directory at {logs}")
    offsets: dict[Path, int] = {}
    while True:
        entries = []
        for p in sorted(logs.glob("*.jsonl")):
            with open(p, "rb") as fh:
                fh.seek(offsets.get(p, 0))
                data = fh.read()
            complete = data.rsplit(b"\n", 1)[0] + b"\n" if b"\n" in data else b""
            offsets[p] = offsets.get(p, 0) + len(complete)
            for raw in complete.split(b"\n"):
                line = raw.decode("utf-8", "replace").rstrip("\r")
                rec, t = parse(line, p.stem) if line.strip() else (None, None)
                if rec is None:
                    continue
                labels = {"job": JOB, "service": rec.get("service") or p.stem, "level": str(rec.get("level", "INFO")).upper()}
                entries.append((labels, t, line, None))
        if entries:
            print(f"pushed {push(url, entries)} lines", flush=True)
        if not follow:
            return
        time.sleep(2)


def snapshot(url: str, incident: str):
    path = ROOT / "scenarios" / "snapshots" / incident / "logs.jsonl"
    if not path.is_file():
        raise SystemExit(f"no snapshot logs at {path}")
    entries = []
    for row in (json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()):
        raw = row["raw"]
        line = json.dumps(raw, separators=(",", ":"))
        labels = {"job": JOB, "incident": incident, "service": row.get("service") or raw.get("service", "unknown"),
                  "level": str(raw.get("level", "INFO")).upper()}
        entries.append((labels, ns(row["ts"]), line, {"evidence_id": row["evidence_id"]}))
    print(f"{incident}: pushed {push(url, entries)} lines from {path.relative_to(ROOT)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["telemetry", "snapshot"])
    ap.add_argument("--url", default=None)
    ap.add_argument("--dir", type=Path, default=None)
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--incident", default=None)
    a = ap.parse_args()
    url = resolve_url(a.url)
    if a.mode == "telemetry":
        telemetry(url, resolve_telemetry_dir(a.dir), a.follow)
    else:
        if not a.incident:
            raise SystemExit("--incident is required for snapshot mode")
        snapshot(url, a.incident)


if __name__ == "__main__":
    main()
