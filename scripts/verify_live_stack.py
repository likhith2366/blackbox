"""Cross-check one failing request across the live stack and BlackBox's own tools (development check, read-only).

    python scripts/verify_live_stack.py [--since ISO] [--out runs/_live/verification.json]

1. Raw source APIs: newest ERROR log line in Loki (with its trace_id), that trace in Tempo, and the payment-svc
   pool / error-rate series in Prometheus around the log time.
2. The same records through BlackBox's live-stack backend tools (search_logs, get_trace, search_traces,
   get_metrics, list_deployments, get_build, get_config, get_commit, list_workflow_runs).
3. Compares ids, timestamps and values; prints a table and saves everything verbatim.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackbox.connectors.prometheus import _iso, _ts  # noqa: E402
from blackbox.evidence.live_stack import load_live_stack  # noqa: E402
from blackbox.schemas import Alert, Incident  # noqa: E402

LOKI, PROM, TEMPO = "http://127.0.0.1:3100", "http://127.0.0.1:9090", "http://127.0.0.1:3200"


def get(url):
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None)
    ap.add_argument("--out", default=str(ROOT / "runs" / "_live" / "verification.json"))
    a = ap.parse_args()
    now = datetime.now(timezone.utc).timestamp()
    since = _ts(a.since) if a.since else now - 900
    report = {"checked_at": _iso(now), "raw": {}, "blackbox": {}, "compare": []}

    # ---- 1. raw source APIs
    q = '{job="blackbox-demo", service="payment-svc", level="ERROR"}'
    loki = get(f"{LOKI}/loki/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": q, "start": int(since * 1e9), "end": int(now * 1e9), "limit": 1, "direction": "backward"}))
    stream = loki["data"]["result"][0]
    ts_ns, line = stream["values"][0]
    rec = json.loads(line)
    report["raw"]["loki"] = {"query": q, "ts_ns": ts_ns, "line": rec, "labels": stream["stream"]}
    trace_id = rec.get("trace_id")
    tempo = get(f"{TEMPO}/api/traces/{trace_id}")
    raw_spans = []
    for b in tempo.get("batches", []):
        svc = next((x["value"].get("stringValue") for x in b["resource"]["attributes"] if x["key"] == "service.name"), None)
        for ss in b.get("scopeSpans", []):
            for sp in ss["spans"]:
                raw_spans.append({"service": svc, "name": sp["name"], "spanId": sp["spanId"],
                                  "start": sp["startTimeUnixNano"], "status": sp.get("status", {})})
    report["raw"]["tempo"] = {"trace_id": trace_id, "spans": raw_spans}
    t = int(ts_ns) / 1e9
    prom = {}
    for m in ("db_pool_in_use", "error_rate"):
        d = get(f"{PROM}/api/v1/query_range?" + urllib.parse.urlencode(
            {"query": f'{m}{{job="payment-svc"}}', "start": t - 60, "end": t + 5, "step": 5}))
        prom[m] = d["data"]["result"][0]["values"] if d["data"]["result"] else []
    report["raw"]["prometheus"] = prom

    # ---- 2. the same through BlackBox's live tools (incident window ends just after the log line)
    cutoff = _iso(t + 5)
    inc = Incident(incident_id="LIVE-VERIFY", alert=Alert(alert_id="ALR-V", title="verification", service="payment-svc",
                   signal="error_rate", condition="manual", fired_at=_iso(t)), window_start=_iso(t - 120),
                   window_end=cutoff, investigation_cutoff=cutoff, services=["checkout-svc", "payment-svc"],
                   available_sources=["logs", "traces", "metrics", "deployments", "builds", "commits", "configs"])
    bb = load_live_stack(inc)
    logs = bb.search_logs(service="payment-svc", level="ERROR", start=_iso(t - 0.5), end=_iso(t + 0.5), limit=50)
    trace = bb.get_trace(trace_id)
    metrics = bb.get_metrics("payment-svc", metric="db_pool_in_use")
    deps = bb.list_deployments(service="payment-svc")
    running = bb.deploys.deployed("payment-svc", cutoff)
    build = bb.get_build(running["build_id"]) if running else None
    config = bb.get_config("payment-svc")
    commit = bb.get_commit(running["commit"]) if running else None
    base_commit = bb.get_commit("b7ae74f")
    runs = bb.list_workflow_runs(limit=2)
    for name, r in (("search_logs", logs), ("get_trace", trace), ("get_metrics", metrics), ("list_deployments", deps),
                    ("get_build", build), ("get_config", config), ("get_commit(running)", commit),
                    ("get_commit(b7ae74f)", base_commit), ("list_workflow_runs", runs)):
        report["blackbox"][name] = None if r is None else {
            "status": r.status, "error": r.error, "records": [x.model_dump(mode="json") for x in r.records[:6]]}

    # ---- 3. compare
    bl = next((x for x in logs.records if x.content.strip() == line.strip()), None)
    report["compare"].append({"what": "log line", "raw": f"{rec['ts']} {rec['msg']}", "blackbox":
                              f"{bl.evidence_id} {bl.event_time}" if bl else "not returned",
                              "same": bool(bl and bl.event_time == rec["ts"])})
    raw_ids = sorted(s for s in (x["spanId"] for x in raw_spans))
    from blackbox.connectors.tempo import _hex
    bb_ids = sorted(r.evidence_id.split(":")[1] for r in trace.records)
    report["compare"].append({"what": "trace spans", "raw": f"{len(raw_ids)} spans", "blackbox": f"{len(bb_ids)} spans",
                              "same": sorted(_hex(x) for x in raw_ids) == bb_ids})
    raw_pool = [(float(x[0]), float(x[1])) for x in prom["db_pool_in_use"] if float(x[0]) <= t + 5]
    bb_pool = metrics.records[0].attributes if metrics.records else {}
    report["compare"].append({"what": "db_pool_in_use", "raw": f"max={max(v for _, v in raw_pool):g} last={raw_pool[-1][1]:g}"
                              if raw_pool else "none", "blackbox": f"max={bb_pool.get('max')} last={bb_pool.get('last')}",
                              "same": bool(raw_pool) and bb_pool.get("last") == raw_pool[-1][1]})
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"log: {rec['ts']} {rec['msg']} trace={trace_id}")
    for c in report["compare"]:
        print(f"  {c['what']:16s} raw: {c['raw']:40s} blackbox: {c['blackbox']:45s} same={c['same']}")
    for name, v in report["blackbox"].items():
        if v:
            first = v["records"][0] if v["records"] else {}
            print(f"  {name:22s} {v['status']:11s} {(first.get('evidence_id') or '')[:40]:40s} "
                  f"backend={first.get('attributes', {}).get('backend')} {v['error'] or ''}")
    print("saved", a.out)


if __name__ == "__main__":
    main()
