"""Fault controller (PRIVATE: never reachable from blackbox/).

    python -m demo.controller inject <fault_id>   # apply a fault to the running demo
    python -m demo.controller reset               # restore the healthy baseline and redeploy
    python -m demo.controller list                # private fault ids

Faults are real changes: commits to the company repo built and deployed through
demo.buildtool/demo.deploy, or operator actions (stop a process, restart it with an
environment override). Ground truth of what was applied is appended to
evaluator_private/controller_state.json.
"""
from __future__ import annotations

import argparse
import json
import time

from demo import buildtool, db_seed, deploy, workspace
from demo.controller_faults import CHANGES, FAULT_IDS
from demo.paths import REPO, SERVICES, load_state, save_state
from demo.timeutil import iso_now

PRIVATE_STATE = REPO / "evaluator_private" / "controller_state.json"


def _record(event: dict) -> None:
    PRIVATE_STATE.parent.mkdir(parents=True, exist_ok=True)
    hist = json.loads(PRIVATE_STATE.read_text(encoding="utf-8")) if PRIVATE_STATE.exists() else []
    hist.append(event)
    PRIVATE_STATE.write_text(json.dumps(hist, indent=2), encoding="utf-8")


def baseline(telemetry_dir: str | None = None) -> list[dict]:
    """Stop everything, recreate repo + database, deploy the healthy baseline."""
    for svc in reversed(SERVICES):
        deploy.stop(svc, reason="redeploy")
    st = load_state()
    if telemetry_dir:
        st["telemetry_dir"] = telemetry_dir
    save_state(st)
    head = workspace.reset_workspace()
    db_seed.reset_working_db()
    recs = []
    for svc in ("payment-svc", "checkout-svc"):
        m = buildtool.build(svc, head, reuse=True)
        recs.append(deploy.deploy(svc, m["build_id"]))
    _record({"at": iso_now(), "action": "reset", "head": head,
             "deployments": [r["deploy_id"] for r in recs]})
    return recs


def apply(change_id: str) -> dict:
    ch = CHANGES[change_id]
    ev = {"change_id": change_id, "family": ch["family"], "kind": ch["kind"], "at": iso_now()}
    if ch["kind"] == "commit":
        sha = workspace.commit_change(ch["who"], ch["message"], ch.get("edits", []), ch.get("new_files"))
        ev["commit"] = sha
        ev["builds"], ev["deployments"] = [], []
        for svc in ch["deploy"]:
            m = buildtool.build(svc, sha)
            ev["builds"].append(m["build_id"])
            ev["deployments"].append(deploy.deploy(svc, m["build_id"])["deploy_id"])
    elif ch["kind"] == "stop":
        deploy.stop(ch["service"], reason=ch["reason"])
        ev["service"] = ch["service"]
    elif ch["kind"] == "restart_env":
        cur = load_state()["deployed"][ch["service"]]
        rec = deploy.deploy(ch["service"], cur["build_id"], actor="operator", trigger="manual restart",
                            env_overrides=ch["env"])
        ev["service"] = ch["service"]
        ev["env"] = ch["env"]
        ev["deployments"] = [rec["deploy_id"]]
    elif ch["kind"] == "db_maintenance":
        import sqlite3
        from demo.paths import WORK_DB
        conn = sqlite3.connect(WORK_DB, timeout=30)
        conn.execute(ch["sql"])
        conn.commit()
        conn.close()
        ev["sql"] = ch["sql"]
    else:
        raise ValueError(ch["kind"])
    ev["done_at"] = iso_now()
    _record({"action": "apply", **ev})
    return ev


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("inject")
    i.add_argument("fault_id", choices=sorted(CHANGES))
    sub.add_parser("reset")
    sub.add_parser("list")
    args = ap.parse_args()
    if args.cmd == "list":
        for k in FAULT_IDS:
            print(k, "-", CHANGES[k]["family"])
        print("(other changes: " + ", ".join(k for k in CHANGES if k not in FAULT_IDS) + ")")
    elif args.cmd == "reset":
        from demo.lock import demo_lock
        t = time.time()
        with demo_lock("controller CLI reset"):
            recs = baseline()
        print(json.dumps({"status": "healthy baseline deployed", "deployments": recs,
                          "seconds": round(time.time() - t, 1)}, indent=2))
    else:
        if not all(deploy.health(s) for s in SERVICES):
            raise SystemExit("demo is not running; start it with: python -m demo.run --healthy")
        from demo.lock import demo_lock
        with demo_lock(f"controller CLI inject"):
            print(json.dumps(apply(args.fault_id), indent=2))


if __name__ == "__main__":
    main()
