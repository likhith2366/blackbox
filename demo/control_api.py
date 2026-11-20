"""Local demo-control API for the shop UI (Evidence). Binds to 127.0.0.1:8710 only.

    python -m demo.control_api            # http://127.0.0.1:8710

This is the operator's controller, NOT an evidence source: BlackBox's tools and API never call
it and never read controller state. Every action goes through demo.controller (real commits,
builds, deploys or operator actions) under the exclusive demo lock. A running scenario capture
holds that lock, so UI actions are refused while a capture runs, and a capture refuses to start
while a UI action holds it.

Endpoints
  GET  /api/health   services' real /health answers + lock holder
  GET  /api/faults   supported faults (ids and descriptions shown only in the control area)
  GET  /api/state    fault(s) applied since the last reset (from the controller's own record)
  POST /api/reset    healthy baseline: repo main -> baseline tag, fresh DB copy, build+deploy both services
  POST /api/inject   {"fault_id": "..."}  apply one supported fault to the running demo
  POST /api/stop     stop both services
  POST /api/traffic  {"rps": 1-12, "duration_s": 5-180, "seed": int}  bounded seeded order traffic
  GET  /api/traffic  current/last traffic run (sent/ok/failed so far)
  POST /api/incidents/snapshot   turn the current demo session into a DEMO incident (INC-9xxx)
  GET  /api/incidents/demo       demo incidents created so far (ids + alert only)
"""
from __future__ import annotations

import json
import threading
import time

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from demo import controller, deploy
from demo.controller_faults import CHANGES
from demo.lock import DemoLocked, demo_lock, holder
from demo.loadgen import LoadGenerator
from demo.paths import RUNTIME, SERVICES, load_state, save_state
from demo.timeutil import iso, iso_now

HOST, PORT = "127.0.0.1", 8710
LIVE_TELEMETRY = RUNTIME / "telemetry" / "live"

SUPPORTED = {
    "retry-policy": "Shared HTTP client: 250ms timeout, 4 retries on timeout/5xx, no backoff (checkout redeploy)",
    "pool-fastpath": "payment-svc micro-charge fast path that never returns its DB connection (payment redeploy)",
    "index-migration": "payment-svc migration dropping the customer/time index (payment redeploy)",
    "endpoint-port": "checkout config points payment base_url at port 18112 (checkout redeploy)",
    "payment-stop": "operator stops payment-svc",
    "pool-override": "payment-svc restarted with PAYMENT_DB_POOL_SIZE=1 env override",
}

app = FastAPI(title="BlackBox demo control (local only)")


class InjectReq(BaseModel):
    fault_id: str


class TrafficReq(BaseModel):
    rps: float = 5.0
    duration_s: float = 60.0
    seed: int = 7


_traffic: dict = {"gen": None, "info": None}
_traffic_lock = threading.Lock()
TRAFFIC_MARKER = RUNTIME / "ui_traffic.json"


def _busy_with_capture():
    h = holder()
    return h if h and str(h.get("owner", "")).startswith("scenario capture") else None


def _traffic_running() -> bool:
    g = _traffic["gen"]
    return bool(g and g.thread and g.thread.is_alive())


def _applied_since_reset() -> list[dict]:
    if not controller.PRIVATE_STATE.exists():
        return []
    hist = json.loads(controller.PRIVATE_STATE.read_text(encoding="utf-8"))
    out = []
    for ev in hist:
        if ev.get("action") == "reset":
            out = []
        elif ev.get("action") == "apply":
            out.append({"fault_id": ev["change_id"], "at": ev["at"], "commit": ev.get("commit"),
                        "deployments": ev.get("deployments")})
    return out


@app.get("/api/health")
def health():
    return {"services": {s: deploy.health(s) for s in SERVICES}, "lock": holder()}


@app.get("/api/faults")
def faults():
    return [{"fault_id": k, "description": v, "kind": CHANGES[k]["kind"]} for k, v in SUPPORTED.items()]


@app.get("/api/state")
def state():
    return {"applied_since_reset": _applied_since_reset(), "lock": holder()}


@app.post("/api/reset")
def reset():
    t = time.time()
    try:
        with demo_lock("control API reset"):
            if _traffic_running():
                raise HTTPException(409, "traffic generation is running; wait for it to finish")
            started = time.time()
            session_dir = RUNTIME / "telemetry" / "sessions" / time.strftime("%Y%m%dT%H%M%S", time.gmtime(started))
            recs = controller.baseline(str(session_dir))
            st = load_state()
            st["session"] = {"started": iso(started), "telemetry_dir": str(session_dir)}
            st["ui_traffic_runs"] = []
            save_state(st)
    except DemoLocked as e:
        raise HTTPException(409, str(e))
    return {"status": "healthy baseline deployed", "seconds": round(time.time() - t, 1),
            "deployments": [{k: r[k] for k in ("deploy_id", "service", "version", "build_id")} for r in recs]}


@app.post("/api/inject")
def inject(req: InjectReq):
    if req.fault_id not in SUPPORTED:
        raise HTTPException(400, f"unsupported fault {req.fault_id!r}")
    if not all(deploy.health(s) for s in SERVICES):
        raise HTTPException(409, "demo is not running; reset first")
    t = time.time()
    try:
        with demo_lock(f"control API inject"):
            ev = controller.apply(req.fault_id)
    except DemoLocked as e:
        raise HTTPException(409, str(e))
    return {"status": "applied", "fault_id": req.fault_id, "seconds": round(time.time() - t, 1),
            "commit": ev.get("commit"), "deployments": ev.get("deployments")}


@app.post("/api/stop")
def stop():
    try:
        with demo_lock("control API stop"):
            res = {s: deploy.stop(s, reason="operator request") for s in reversed(SERVICES)}
    except DemoLocked as e:
        raise HTTPException(409, str(e))
    return {"stopped": res}


@app.post("/api/traffic")
def traffic(req: TrafficReq):
    if not (1 <= req.rps <= 12) or not (5 <= req.duration_s <= 180):
        raise HTTPException(400, "rps must be 1..12 and duration_s 5..180")
    if _busy_with_capture():
        raise HTTPException(409, f"demo environment busy: {_busy_with_capture()['owner']}")
    if not all(deploy.health(s) for s in SERVICES):
        raise HTTPException(409, "demo is not running; reset first")
    st = load_state()
    if not st.get("session"):
        raise HTTPException(409, "no demo session; reset first")
    with _traffic_lock:
        if _traffic_running():
            raise HTTPException(409, "traffic generation already running")
        out = __import__("pathlib").Path(st["session"]["telemetry_dir"]) / "loadgen.jsonl"
        gen = LoadGenerator([(0, req.rps)], req.seed, out_path=out)
        info = {"started": iso_now(), "rps": req.rps, "duration_s": req.duration_s, "seed": req.seed}
        _traffic["gen"], _traffic["info"] = gen, info
        TRAFFIC_MARKER.write_text(json.dumps({"pid": __import__("os").getpid(),
                                              "until": time.time() + req.duration_s + 15}), encoding="utf-8")
        gen.start(req.duration_s)
        st = load_state()
        st.setdefault("ui_traffic_runs", []).append(info)
        save_state(st)
    return {"status": "started", **info}


@app.get("/api/traffic")
def traffic_status():
    g, info = _traffic["gen"], _traffic["info"]
    if g is None:
        return {"running": False, "last": None}
    return {"running": _traffic_running(), "last": info, "stats": dict(g.stats)}


@app.post("/api/incidents/snapshot")
def snapshot():
    from scenarios.demo_incident import DemoIncidentError, create_demo_incident
    try:
        with demo_lock("control API demo snapshot"):
            res = create_demo_incident()
    except DemoLocked as e:
        raise HTTPException(409, str(e))
    except DemoIncidentError as e:
        raise HTTPException(409, str(e))
    return {"status": "created", "development": True, **res}


@app.get("/api/incidents/demo")
def demo_incidents():
    from scenarios.demo_incident import load_demo_manifest
    man = load_demo_manifest()["incidents"]
    return [{"incident_id": k, "alert_fired_at": v.get("alert_fired_at"), "status": v.get("status")}
            for k, v in sorted(man.items()) if v.get("scenario_type") == "demo"]


def main():
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
