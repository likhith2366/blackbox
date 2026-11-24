"""Summarise runs/live_faults (development only, read-only). Provisional lexical scoring; read the reports.

    python scripts/score_live_sweep.py [--md docs/LIVE_FAULT_SWEEP_TABLE.md]

For each injected fault: whether an alert fired, the report status, the top cause, which tools/back-ends were
used, whether the faulty deployment/commit was retrieved and cited, and a provisional keyword check of the top
cause against the fault family (component AND mechanism words). A human should confirm each verdict.
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = Path(sys.argv[sys.argv.index("--runs") + 1]) if "--runs" in sys.argv else ROOT / "runs" / "live_faults"
# component words, mechanism words (a cause must hit one of each)
FAMILY = {
    "retry_amplification": (["checkout", "httpclient", "client"], ["retr", "amplif", "attempt"]),
    "connection_leak": (["payment"], ["leak", "not release", "never release", "release", "pool exhaust"]),
    "missing_index": (["payment"], ["index", "full scan", "table scan", "slow quer"]),
    "config_mismatch_endpoint": (["checkout"], ["port", "endpoint", "sidecar", "base_url", "wrong address"]),
    "stopped_dependency": (["payment"], ["stopped", "down", "not running", "unreachable", "connection refused", "unavailable"]),
    "config_override_pool_size": (["payment"], ["pool size", "pool_size", "pool of 1", "size 1", "override"]),
}


def main():
    rows = []
    for d in sorted(p for p in RUNS.iterdir() if p.is_dir()) if RUNS.exists() else []:
        truth = json.loads((d / "truth.json").read_text(encoding="utf-8")) if (d / "truth.json").exists() else {}
        o = json.loads((d / "output.json").read_text(encoding="utf-8")) if (d / "output.json").exists() else {}
        row = {"fault": d.name, "family": truth.get("family"), "alert": "no" if o.get("status") == "no_alert" else "yes"}
        if o.get("status") != "no_alert" and o:
            rep = o.get("report") or {}
            causes = rep.get("ranked_causes") or []
            top = causes[0] if causes else {}
            tools = Counter(t["tool"] for s in o.get("steps", []) for t in s.get("tool_requests", []))
            backends = Counter(r.get("attributes", {}).get("backend") for r in o.get("evidence_seen", []))
            seen = {r["evidence_id"] for r in o.get("evidence_seen", [])}
            cited = {i for c in causes for i in c.get("evidence_ids", [])} | {i for c in rep.get("claims", []) for i in c.get("evidence_ids", [])}
            change = {f"deploy:{x}" for x in truth.get("deployments", [])}
            if truth.get("commit"):
                change.add(f"commit:{truth['commit'][:7]}")
            comp, mech = FAMILY.get(truth.get("family"), ([], []))
            text = f"{top.get('component', '')} {top.get('mechanism', '')}".lower()
            row.update({"status": rep.get("status"), "top_cause": f"{top.get('component', '')} :: {top.get('mechanism', '')}"[:120],
                        "keyword_match": bool(top) and any(w in text for w in comp) and any(w in text for w in mech),
                        "change_retrieved": sorted(change & seen), "change_cited": sorted(change & cited),
                        "tools": dict(tools), "backends": dict(backends), "calls": o.get("tool_calls_used"),
                        "rounds": o.get("rounds_used"), "wall_s": o.get("wall_seconds")})
        rows.append(row)
    cols = ["fault", "family", "alert", "status", "keyword_match", "change_retrieved", "change_cited", "calls", "wall_s", "top_cause"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(r.get(c, "")) for c in cols) + " |" for r in rows]
    print("\n".join(lines))
    for r in rows:
        if r.get("tools"):
            print(r["fault"], "tools:", r["tools"], "backends:", r["backends"])
    (RUNS / "_summary.json").write_text(json.dumps(rows, indent=1), encoding="utf-8") if RUNS.exists() else None
    if "--md" in sys.argv:
        Path(sys.argv[sys.argv.index("--md") + 1]).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
