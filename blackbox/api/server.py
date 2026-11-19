"""Thin HTTP API around the existing investigator and saved-run store (CONTRACT section 12).

No orchestration lives here: investigations call blackbox.systems.run_system unchanged. Jobs run
one at a time and only while this process holds the GPU lock. Responses never include tokens,
answer keys, scores, scenario types or demo-controller state.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from blackbox.api import gpu_lock
from blackbox.schemas import RunConfig

import os
# the API may be served from a separate checkout; it reads runs/snapshots of the main repo
REPO = Path(os.environ.get("BLACKBOX_ROOT") or Path(__file__).resolve().parents[2])
RUNS = REPO / "runs"
SNAPSHOTS = REPO / "scenarios" / "snapshots"
JOBS_DIR = RUNS / "ui_jobs"
CONFIG = REPO / "configs" / "dev.yaml"
DEFAULT_REPO = "likhith2366/blackbox-demo-shop"
HIDDEN_META = ("answer", "score", "scenario", "fault", "controller")   # never sent to the browser

app = FastAPI(title="BlackBox API", docs_url="/api/docs", openapi_url="/api/openapi.json")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ------------------------------------------------------------------ saved runs
def _run_id(output_json: Path) -> str:
    return output_json.parent.relative_to(RUNS).as_posix().replace("/", "~")


def _run_dir(run_id: str) -> Path:
    d = (RUNS / run_id.replace("~", "/")).resolve()
    if RUNS.resolve() not in d.parents or not (d / "output.json").exists():
        raise HTTPException(404, f"no saved run {run_id}")
    return d


def _clean_meta(meta: dict) -> dict:
    return {k: v for k, v in meta.items() if not any(h in k.lower() for h in HIDDEN_META)}


def _mode(out: dict) -> str:
    live = any(e.get("evidence_id", "").startswith("gh:") or (e.get("attributes") or {}).get("live")
               for e in out.get("evidence_seen", []))
    return "live" if live else "snapshot"


def _summary(output_json: Path) -> Optional[dict]:
    try:
        out = json.loads(output_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if out.get("stub"):
        return None
    rep = out.get("report") or {}
    calls = sum(len(s.get("tool_requests", [])) for s in out.get("steps", []))
    return {"run_id": _run_id(output_json), "batch": output_json.parent.relative_to(RUNS).parts[0],
            "path": output_json.parent.relative_to(REPO).as_posix(), "system": out.get("system"),
            "incident_id": out.get("incident_id"), "ablation": out.get("ablation"), "status": out.get("status"),
            "report_status": rep.get("status"), "headline": rep.get("headline"),
            "wall_seconds": out.get("wall_seconds"), "rounds": out.get("rounds_used"), "tool_calls": calls,
            "code_revision": (out.get("config") or {}).get("code_revision"), "mode": _mode(out),
            "modified": datetime.fromtimestamp(output_json.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


@app.get("/api/runs")
def list_runs(batch: Optional[str] = None) -> list[dict]:
    items = []
    for p in RUNS.rglob("output.json"):
        rel = p.relative_to(RUNS).parts
        if rel[0].startswith("_dev_stub") or "snapshot_root" in rel or (batch and rel[0] != batch):
            continue
        s = _summary(p)
        if s:
            items.append(s)
    return sorted(items, key=lambda s: s["modified"], reverse=True)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    d = _run_dir(run_id)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8")) if (d / "meta.json").exists() else {}
    return {"run_id": run_id, "meta": _clean_meta(meta),
            "output": json.loads((d / "output.json").read_text(encoding="utf-8"))}


@app.get("/api/runs/{run_id}/export")
def export_run(run_id: str):
    d = _run_dir(run_id)
    return FileResponse(d / "output.json", media_type="application/json",
                        filename=f"{run_id.replace('~', '_')}.json")


# ------------------------------------------------------------------ incidents and sources
def _incident_files() -> list[Path]:
    return sorted(SNAPSHOTS.glob("INC-*/incident.json"))


@app.get("/api/incidents")
def list_incidents() -> list[dict]:
    out = []
    for f in _incident_files():
        inc = json.loads(f.read_text(encoding="utf-8"))
        out.append({k: inc.get(k) for k in ("incident_id", "alert", "window_start", "window_end",
                                             "investigation_cutoff", "services", "available_sources")})
    return out


@app.get("/api/project")
def project() -> dict:
    sources: list[dict] = []
    try:
        from blackbox.connectors.github_api import GitHubConnector
        gh = GitHubConnector(DEFAULT_REPO, timeout_s=8)
        res = gh.repo_metadata()
        ok = res.status == "ok"
        for s in ("commits", "configs"):
            sources.append({"source": s, "mode": "live" if ok else "unavailable", "healthy": ok,
                            "detail": f"GitHub REST API, {DEFAULT_REPO} (read-only)", "error": None if ok else res.error})
        runs = gh.list_workflow_runs(limit=1) if hasattr(gh, "list_workflow_runs") else None
        if runs is not None:
            sources.append({"source": "builds (GitHub Actions)", "mode": "live" if runs.status in ("ok", "empty") else "unavailable",
                            "healthy": runs.status in ("ok", "empty"), "detail": "workflow runs, jobs, steps, artifact metadata",
                            "error": runs.error if runs.status not in ("ok", "empty") else None})
    except Exception as exc:  # connector import or network problems are reported, not hidden
        sources.append({"source": "commits", "mode": "unavailable", "healthy": False,
                        "detail": "GitHub REST API", "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
    try:
        from blackbox.connectors.local_logs import LocalLogs
        r = LocalLogs().search(limit=1)
        sources.append({"source": "logs", "mode": "live" if r.status in ("ok", "empty") else "unavailable",
                        "healthy": r.status in ("ok", "empty"),
                        "detail": "application JSONL log files (local)", "error": r.error})
    except Exception as exc:
        sources.append({"source": "logs", "mode": "unavailable", "healthy": False,
                        "detail": "application JSONL log files (local)", "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
    n = len(_incident_files())
    for s in ("logs", "traces", "metrics", "deployments", "builds", "commits", "configs", "tickets"):
        sources.append({"source": s, "mode": "snapshot" if n else "unavailable", "healthy": bool(n),
                        "detail": f"frozen snapshots ({n} incidents); tickets/commit history are team-authored fixtures"
                        if s in ("tickets", "commits") else f"frozen snapshots ({n} incidents)", "error": None})
    for s, why in (("jira", "not configured on this machine (no site or credentials); snapshot ticket fixtures are used"),
                   ("jenkins", "not configured on this machine; build records come from the demo build tooling")):
        sources.append({"source": s, "mode": "unavailable", "healthy": False, "detail": why, "error": None})
    return {"repo": DEFAULT_REPO, "sources": sources}


@app.get("/api/health")
def health() -> dict:
    h = gpu_lock.holder()
    cfg = RunConfig()
    with JOBS_LOCK:
        queued = sum(1 for j in JOBS.values() if j["status"] in ("queued", "waiting_gpu"))
        running = sum(1 for j in JOBS.values() if j["status"] == "running")
    return {"ok": True, "model": cfg.model, "gpu": {"busy": h is not None, "holder": (h or {}).get("owner"),
            "since": (h or {}).get("started")}, "queue": {"queued": queued, "running": running}}


# ------------------------------------------------------------------ jobs
class InvestigationRequest(BaseModel):
    incident_id: str
    mode: str = "snapshot"
    problem_description: Optional[str] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None


JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
WORK: "queue.Queue[str]" = queue.Queue()


def _event(job: dict, kind: str, **payload: Any) -> None:
    ev = {"type": kind, "t": _now(), **payload}
    with JOBS_LOCK:
        job["events"].append(ev)
    with open(Path(job["dir"]) / "events.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev, default=str) + "\n")


def _public(job: dict) -> dict:
    return {k: job[k] for k in ("job_id", "status", "created", "started", "ended", "run_id", "error", "request")}


@app.post("/api/investigations", status_code=202)
def submit(req: InvestigationRequest) -> dict:
    snap = SNAPSHOTS / req.incident_id / "incident.json"
    if not snap.exists():
        raise HTTPException(404, f"unknown incident {req.incident_id}")
    if req.mode not in ("snapshot", "live"):
        raise HTTPException(400, "mode must be snapshot or live")
    inc = json.loads(snap.read_text(encoding="utf-8"))
    lo, hi = _parse(inc["window_start"]), _parse(inc["investigation_cutoff"])
    for name in ("window_start", "window_end"):
        v = getattr(req, name)
        if v and not (lo <= _parse(v) <= hi):
            raise HTTPException(400, f"{name} must lie inside the incident window {inc['window_start']} .. {inc['investigation_cutoff']}")
    job_id = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
    d = JOBS_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    job = {"job_id": job_id, "status": "queued", "created": _now(), "started": None, "ended": None,
           "run_id": None, "error": None, "request": req.model_dump(), "events": [], "dir": str(d)}
    (d / "request.json").write_text(json.dumps(req.model_dump(), indent=2), encoding="utf-8")
    with JOBS_LOCK:
        JOBS[job_id] = job
        position = sum(1 for j in JOBS.values() if j["status"] in ("queued", "waiting_gpu"))
    _event(job, "queued", position=position)
    WORK.put(job_id)
    return {"job_id": job_id, "status": "queued", "position": position}


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    with JOBS_LOCK:
        return [_public(j) for j in sorted(JOBS.values(), key=lambda j: j["created"], reverse=True)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, f"no job {job_id}")
    return _public(job)


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, f"no job {job_id}")

    def stream():
        sent = 0
        while True:
            with JOBS_LOCK:
                evs = list(job["events"][sent:])
                done = job["status"] in ("completed", "failed")
            for ev in evs:
                yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
            sent += len(evs)
            if done and not evs:
                return
            if not evs:
                yield ": keep-alive\n\n"
                time.sleep(1.0)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class _WithUserContext:
    """Adds the user's problem description and window to the model-visible incident, labelled as user input."""

    def __init__(self, base, req: InvestigationRequest):
        self._base, self._req = base, req
        self.incident_id, self.ablation = base.incident_id, base.ablation

    def incident(self):
        inc = self._base.incident()
        notes = inc.notes
        if self._req.problem_description:
            notes += f"\nUser-provided problem description (unverified): {self._req.problem_description.strip()[:1000]}"
        upd = {"notes": notes}
        if self._req.window_start:
            upd["window_start"] = self._req.window_start
        if self._req.window_end:
            upd["window_end"] = self._req.window_end
        return inc.model_copy(update=upd)

    def __getattr__(self, name):
        return getattr(self._base, name)


def _worker() -> None:
    import yaml
    from blackbox.evidence.backend import load_backend
    from blackbox.systems import run_system
    while True:
        job_id = WORK.get()
        job = JOBS[job_id]
        req = InvestigationRequest(**job["request"])
        announced = False
        while not gpu_lock.try_acquire(f"BlackBox UI job {job_id}"):
            if not announced:
                with JOBS_LOCK:
                    job["status"] = "waiting_gpu"
                _event(job, "waiting_gpu", holder=(gpu_lock.holder() or {}).get("owner"))
                announced = True
            time.sleep(5)
        try:
            with JOBS_LOCK:
                job["status"], job["started"] = "running", _now()
            _event(job, "started", mode=req.mode)
            if req.mode == "live":
                from blackbox.evidence.live import load_live_backend
                from blackbox.connectors.capture import capture_dir_for
                # the incident's own captured application logs, read live from the files the services wrote;
                # never the demo's current telemetry, which belongs to whatever is running now
                tel = REPO / "demo" / ".runtime" / "captures" / req.incident_id / "telemetry"
                base = load_live_backend(req.incident_id, DEFAULT_REPO, telemetry_dir=tel if tel.exists() else None,
                                         capture_dir=capture_dir_for(f"ui-{job_id}"))
            else:
                base = load_backend(req.incident_id)
            cfg = RunConfig(**(yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}))
            out = run_system("blackbox", _WithUserContext(base, req), cfg,
                             on_event=lambda kind, payload: _event(job, kind, data=payload))
            d = Path(job["dir"])
            (d / "output.json").write_text(out.model_dump_json(indent=2), encoding="utf-8")
            (d / "meta.json").write_text(json.dumps({"phase": "BlackBox UI job (not evaluation)", "mode": req.mode,
                                                    "config_file": str(CONFIG.relative_to(REPO)),
                                                    "code_revision": out.config.code_revision,
                                                    "request": req.model_dump(), "status": out.status}, indent=2),
                                         encoding="utf-8")
            with JOBS_LOCK:
                job["status"], job["run_id"] = "completed", _run_id(d / "output.json")
            _event(job, "completed", run_id=job["run_id"], status=out.status)
        except Exception as exc:
            with JOBS_LOCK:
                job["status"], job["error"] = "failed", f"{type(exc).__name__}: {str(exc)[:300]}"
            _event(job, "failed", error=job["error"])
        finally:
            gpu_lock.release()
            with JOBS_LOCK:
                job["ended"] = _now()


threading.Thread(target=_worker, daemon=True, name="gpu-job-worker").start()
