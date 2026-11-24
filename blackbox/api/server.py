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
SETTINGS = {"standard": CONFIG, "improved": REPO / "configs" / "dev_v3e.yaml"}   # improved = development options
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


@app.get("/api/incidents/{incident_id}/timeline")
def incident_timeline(incident_id: str) -> dict:
    """Deploy, build, commit and ERROR-log events of one incident, read through the evidence backend so
    the investigation cutoff applies. For people browsing the incident; never shown to the model."""
    from blackbox.evidence.backend import load_backend
    if not (SNAPSHOTS / incident_id / "incident.json").exists():
        raise HTTPException(404, f"unknown incident {incident_id}")
    b = load_backend(incident_id, snapshot_root=SNAPSHOTS)
    inc = b.incident()
    events: list[dict] = [{"time": inc.alert.fired_at, "kind": "alert", "id": inc.alert.alert_id,
                           "service": inc.alert.service, "text": f"{inc.alert.title} ({inc.alert.condition})"}]
    notes: list[str] = []

    def add(res, kind, text_of):
        if res.status not in ("ok", "empty"):
            notes.append(f"{kind}: {res.status} {res.error or ''}".strip())
            return
        for r in res.records:
            events.append({"time": r.event_time or r.available_time, "kind": kind, "id": r.evidence_id,
                           "service": r.service, "text": text_of(r)})

    add(b.list_deployments(), "deploy", lambda r: r.title or r.content[:160])
    add(b.search_commits(limit=15), "commit", lambda r: r.title or r.content[:160])
    add(b.search_logs(level="ERROR", limit=30), "error_log", lambda r: r.title)
    for d in [e for e in events if e["kind"] == "deploy"]:
        rec = next((x for x in b.list_deployments().records if x.evidence_id == d["id"]), None)
        build = (rec.attributes or {}).get("build_id") if rec else None
        if build:
            add(b.get_build(build), "build", lambda r: r.title or r.content[:160])
    events = [e for e in events if e["time"]]
    events.sort(key=lambda e: e["time"])
    return {"incident_id": incident_id, "window_start": inc.window_start, "investigation_cutoff": inc.investigation_cutoff,
            "events": events, "notes": notes}


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
    if os.environ.get("BLACKBOX_LOKI_URL"):
        from blackbox.connectors.loki import LokiLogs
        lk = LokiLogs()
        ok, why = lk.ready()
        sources.append({"source": "logs (Grafana Loki)", "mode": "live" if ok else "unavailable", "healthy": ok,
                        "detail": lk.detail, "error": None if ok else why})
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


# ------------------------------------------------------------------ live stack
def _open_live_incident(alert: str) -> dict:
    import re as _re
    m = _re.fullmatch(r"([\w.-]+):(\w+)([<>])([\d.]+):(\d+)", (alert or "").replace(" ", ""))
    if not m:
        raise HTTPException(400, "alert must look like checkout-svc:error_rate>0.1:15")
    from blackbox.evidence.live_incident import open_incident
    try:
        inc = open_incident(m[1], m[2], m[3], float(m[4]), int(m[5]))
    except LookupError as exc:
        raise HTTPException(409, str(exc))
    except OSError as exc:
        raise HTTPException(503, f"Prometheus not reachable: {type(exc).__name__}")
    return inc.model_dump(mode="json")


def _status_word(available: bool, detail: str) -> str:
    d = detail.lower()
    if available:
        return "live"
    if "not configured" in d:
        return "not configured"
    if "http" in d and ("answered" in d or "error" in d):
        return "error"
    return "unavailable"


@app.get("/api/live/sources")
def live_sources() -> dict:
    """Per-source status of the live stack (no snapshot is read)."""
    from blackbox.evidence.live_stack import load_live_stack
    from blackbox.schemas import Alert, Incident
    now = _now()
    probe = Incident(incident_id="LIVE-STATUS", alert=Alert(alert_id="-", title="status", service="checkout-svc",
                     signal="-", condition="-", fired_at=now), window_start=now, window_end=now,
                     investigation_cutoff=now, services=["checkout-svc", "payment-svc"], available_sources=[])
    b = load_live_stack(probe)
    rows = [{"source": s.source, "status": _status_word(s.available, s.detail), "detail": s.detail}
            for s in b.source_status()]
    jk = b.jenkins
    ok, why = jk.ready() if jk.configured else (False, "not configured (set JENKINS_URL, JENKINS_USER, JENKINS_API_TOKEN)")
    rows.append({"source": "jenkins", "status": "live" if ok else _status_word(False, why), "detail": jk.detail + ("" if ok else f" - {why}")})
    jr = b.jira
    ok, why = jr.ready() if jr.configured else (False, "not configured (set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN)")
    rows = [r for r in rows if r["source"] != "tickets"] + [{"source": "tickets (Jira)", "status": "live" if ok else
                                                             _status_word(False, why), "detail": jr.detail + ("" if ok else f" - {why}")}]
    return {"checked_at": now, "sources": rows}


@app.post("/api/live/incidents")
def live_incident(body: dict) -> dict:
    return _open_live_incident(str(body.get("alert", "")))


# ------------------------------------------------------------------ chat (Ask BlackBox)
CHAT_DIR = REPO / "runs" / "chat"
CHATS: dict[str, Any] = {}
CHAT_LOCK = threading.Lock()


class ChatStart(BaseModel):
    mode: str = "live_stack"            # live_stack | snapshot
    alert: Optional[str] = None
    incident_id: Optional[str] = None


class ChatMessage(BaseModel):
    text: str


def _chat_save(sess) -> None:
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    (CHAT_DIR / f"{sess.id}.json").write_text(json.dumps(sess.to_dict(), indent=1, default=str), encoding="utf-8")


@app.post("/api/chat/sessions")
def chat_start(req: ChatStart) -> dict:
    import yaml
    from blackbox.investigator.chat import ChatSession
    cfg = RunConfig(**(yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}))
    if req.mode == "live_stack":
        from blackbox.evidence.live_stack import load_live_stack
        from blackbox.schemas import Incident
        backend = load_live_stack(Incident(**_open_live_incident(req.alert or "")))
    elif req.mode == "snapshot":
        if not req.incident_id or not (SNAPSHOTS / req.incident_id / "incident.json").exists():
            raise HTTPException(404, f"unknown incident {req.incident_id}")
        backend = load_backend(req.incident_id, snapshot_root=SNAPSHOTS)
    else:
        raise HTTPException(400, "mode must be live_stack or snapshot")
    sess = ChatSession(backend, cfg, title=backend.incident().alert.title)
    with CHAT_LOCK:
        CHATS[sess.id] = sess
    _chat_save(sess)
    return sess.to_dict()


@app.get("/api/chat/sessions")
def chat_list() -> list[dict]:
    out = []
    for f in sorted(CHAT_DIR.glob("*.json"), reverse=True)[:30] if CHAT_DIR.exists() else []:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"session_id": d["session_id"], "title": d.get("title"), "mode": d.get("mode"),
                    "incident_id": d["incident"]["incident_id"], "turns": len(d.get("turns", [])),
                    "open": d["session_id"] in CHATS})
    return out


@app.get("/api/chat/sessions/{sid}")
def chat_get(sid: str) -> dict:
    if sid in CHATS:
        return CHATS[sid].to_dict()
    f = CHAT_DIR / f"{sid}.json"
    if not f.exists():
        raise HTTPException(404, "unknown chat session")
    return json.loads(f.read_text(encoding="utf-8"))


@app.post("/api/chat/sessions/{sid}/messages")
def chat_ask(sid: str, msg: ChatMessage) -> dict:
    sess = CHATS.get(sid)
    if sess is None:
        raise HTTPException(409, "this chat session is closed (the API restarted); start a new one")
    if not msg.text.strip():
        raise HTTPException(400, "empty message")
    deadline = time.time() + 900
    while not gpu_lock.try_acquire(f"BlackBox chat {sid}"):
        if time.time() > deadline:
            raise HTTPException(503, "the GPU is busy; try again later")
        time.sleep(3)
    try:
        turn = sess.ask(msg.text)
    finally:
        gpu_lock.release()
    _chat_save(sess)
    return {"turn": turn.__dict__, "session": sess.to_dict()}


# ------------------------------------------------------------------ jobs
class InvestigationRequest(BaseModel):
    incident_id: str = ""
    mode: str = "snapshot"              # snapshot | live (snapshot + GitHub + local logs) | live_stack (live sources only)
    alert: Optional[str] = None         # live_stack: service:metric>threshold:for_s
    settings: str = "standard"          # standard | improved (change expansion, synthesis, causal gate; development)
    followup_of: Optional[str] = None   # run id this investigation follows up (new job, new run id)
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


PUBLIC = ("job_id", "status", "created", "started", "ended", "run_id", "error", "request", "meta", "retry_of")


def _public(job: dict) -> dict:
    return {k: job.get(k) for k in PUBLIC}


def _save(job: dict) -> None:
    """Jobs survive an API restart: state is written to runs/ui_jobs/<id>/job.json on every change."""
    (Path(job["dir"]) / "job.json").write_text(json.dumps(_public(job), indent=2, default=str), encoding="utf-8")


def _set(job: dict, **fields: Any) -> None:
    with JOBS_LOCK:
        job.update(fields)
    _save(job)


@app.post("/api/investigations", status_code=202)
def submit(req: InvestigationRequest, retry_of: Optional[str] = None) -> dict:
    if req.settings not in SETTINGS:
        raise HTTPException(400, "settings must be standard or improved")
    if req.mode not in ("snapshot", "live", "live_stack"):
        raise HTTPException(400, "mode must be snapshot, live or live_stack")
    live_incident = None
    if req.mode == "live_stack":
        live_incident = _open_live_incident(req.alert or "")
        req.incident_id = live_incident["incident_id"]
        inc = live_incident
    else:
        snap = SNAPSHOTS / req.incident_id / "incident.json"
        if not snap.exists():
            raise HTTPException(404, f"unknown incident {req.incident_id}")
        inc = json.loads(snap.read_text(encoding="utf-8"))
    if req.followup_of:
        _run_dir(req.followup_of)   # 404 if the parent run does not exist
    lo, hi = _parse(inc["window_start"]), _parse(inc["investigation_cutoff"])
    for name in ("window_start", "window_end"):
        v = getattr(req, name)
        if v and not (lo <= _parse(v) <= hi):
            raise HTTPException(400, f"{name} must lie inside the incident window {inc['window_start']} .. {inc['investigation_cutoff']}")
    job_id = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
    d = JOBS_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    job = {"job_id": job_id, "status": "queued", "created": _now(), "started": None, "ended": None,
           "run_id": None, "error": None, "request": req.model_dump(), "events": [], "dir": str(d),
           "meta": None, "retry_of": retry_of, "cancel": False}
    (d / "request.json").write_text(json.dumps(req.model_dump(), indent=2), encoding="utf-8")
    if live_incident is not None:
        (d / "incident.json").write_text(json.dumps(live_incident, indent=2), encoding="utf-8")
    with JOBS_LOCK:
        JOBS[job_id] = job
        position = sum(1 for j in JOBS.values() if j["status"] in ("queued", "waiting_gpu"))
    _save(job)
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


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    """Queued jobs are cancelled at once; a running job stops before its next round (a safe boundary)."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, f"no job {job_id}")
    if job["status"] in ("queued", "waiting_gpu"):
        _set(job, status="cancelled", ended=_now(), error="cancelled before it started")
        _event(job, "cancelled", when="before start")
    elif job["status"] == "running":
        _set(job, cancel=True)
        _event(job, "cancel_requested", note="will stop before the next round")
    else:
        raise HTTPException(409, f"job is {job['status']}; nothing to cancel")
    return _public(job)


@app.post("/api/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: str) -> dict:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, f"no job {job_id}")
    if job["status"] not in ("failed", "cancelled"):
        raise HTTPException(409, f"only failed or cancelled jobs can be retried (job is {job['status']})")
    return submit(InvestigationRequest(**job["request"]), retry_of=job_id)


SNAPSHOT_FILES = {"logs.jsonl", "spans.jsonl", "metrics.jsonl", "deployments.jsonl", "builds.jsonl",
                  "commits.jsonl", "configs.jsonl", "tickets.json", "incident.json"}


@app.get("/api/runs/{run_id}/evidence/{evidence_id:path}")
def evidence_source(run_id: str, evidence_id: str) -> dict:
    """A cited record and where it came from. Only records this run actually saw can be opened."""
    out = json.loads((_run_dir(run_id) / "output.json").read_text(encoding="utf-8"))
    rec = next((e for e in out.get("evidence_seen", []) if e.get("evidence_id") == evidence_id), None)
    if rec is None:
        raise HTTPException(404, f"{evidence_id} was not retrieved in run {run_id}")
    ref = rec.get("content_ref") or ""
    attrs = rec.get("attributes") or {}
    source: dict[str, Any] = {"kind": "unknown", "ref": ref}
    if ref.startswith("http"):
        source = {"kind": "github" if "github.com" in ref else "url", "url": ref}
    elif "#" in ref and ref.split("#")[0] in SNAPSHOT_FILES:
        fname, line = ref.split("#", 1)
        raw = None
        f = SNAPSHOTS / out.get("incident_id", "") / fname
        if f.exists() and line.isdigit() and fname.endswith(".jsonl"):
            for row in f.read_text(encoding="utf-8").splitlines():
                try:
                    obj = json.loads(row)
                except json.JSONDecodeError:
                    continue
                if obj.get("evidence_id") == evidence_id or str(obj.get("line")) == line:
                    raw = obj
                    break
        source = {"kind": "snapshot", "file": fname, "line": line, "raw": raw}
    elif attrs.get("path"):
        source = {"kind": "local_log_file", "path": attrs.get("path"), "line": attrs.get("line")}
    return {"record": rec, "source": source}


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
        if job["status"] == "cancelled":
            continue
        req = InvestigationRequest(**job["request"])
        announced = False
        while not gpu_lock.try_acquire(f"BlackBox UI job {job_id}"):
            if not announced:
                _set(job, status="waiting_gpu")
                _event(job, "waiting_gpu", holder=(gpu_lock.holder() or {}).get("owner"))
                announced = True
            time.sleep(5)
            if job["status"] == "cancelled":
                break
        if job["status"] == "cancelled":
            gpu_lock.release()
            continue
        try:
            _set(job, status="running", started=_now())
            _event(job, "started", mode=req.mode)
            if req.mode == "live_stack":
                from blackbox.evidence.live_stack import load_live_stack
                from blackbox.connectors.capture import capture_dir_for
                from blackbox.schemas import Incident
                inc_live = Incident(**json.loads((Path(job["dir"]) / "incident.json").read_text(encoding="utf-8")))
                base = load_live_stack(inc_live, capture_dir=capture_dir_for(f"ui-{job_id}"))
            elif req.mode == "live":
                from blackbox.evidence.live import load_live_backend
                from blackbox.connectors.capture import capture_dir_for
                # the incident's own captured application logs, read live from the files the services wrote;
                # never the demo's current telemetry, which belongs to whatever is running now
                tel = REPO / "demo" / ".runtime" / "captures" / req.incident_id / "telemetry"
                loki = os.environ.get("BLACKBOX_LOKI_URL")
                base = load_live_backend(req.incident_id, DEFAULT_REPO, telemetry_dir=tel if tel.exists() else None,
                                         capture_dir=capture_dir_for(f"ui-{job_id}"),
                                         snapshot_root=SNAPSHOTS, loki_url=loki or None,
                                         loki_incident=req.incident_id if loki else None)
            else:
                base = load_backend(req.incident_id, snapshot_root=SNAPSHOTS)
            cfg_file = SETTINGS.get(req.settings, CONFIG)
            cfg = RunConfig(**(yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}))
            import hashlib
            from blackbox.systems import code_revision as _rev
            man = SNAPSHOTS / req.incident_id / "manifest.json"
            _set(job, meta={"mode": req.mode, "settings": req.settings, "config_file": str(cfg_file.relative_to(REPO)),
                            "model": cfg.model_dump(include={"model", "temperature", "seed", "num_ctx", "num_predict",
                                                              "max_rounds", "max_tool_calls", "round_prompt",
                                                              "report_prompt", "coverage_hint"}),
                            "code_revision": _rev(), "snapshot": req.incident_id,
                            "snapshot_manifest_sha256": hashlib.sha256(man.read_bytes()).hexdigest() if man.exists() else None})
            out = run_system("blackbox", _WithUserContext(base, req), cfg,
                             on_event=lambda kind, payload: _event(job, kind, data=payload),
                             should_cancel=lambda: bool(job.get("cancel")))
            d = Path(job["dir"])
            (d / "output.json").write_text(out.model_dump_json(indent=2), encoding="utf-8")
            (d / "meta.json").write_text(json.dumps({"phase": "BlackBox UI job (not evaluation)", **(job["meta"] or {}),
                                                    "request": req.model_dump(), "status": out.status,
                                                    "retry_of": job.get("retry_of")}, indent=2), encoding="utf-8")
            final = "cancelled" if out.status == "cancelled" else "completed"
            _set(job, status=final, run_id=_run_id(d / "output.json"))
            _event(job, final, run_id=job["run_id"], status=out.status)
        except Exception as exc:
            _set(job, status="failed", error=f"{type(exc).__name__}: {str(exc)[:300]}")
            _event(job, "failed", error=job["error"])
        finally:
            gpu_lock.release()
            _set(job, ended=_now())


def _reload_jobs() -> None:
    """Restore jobs after an API restart. Queued jobs are re-queued; a job that was running when
    the API stopped is marked failed (its inference was interrupted) and can be retried."""
    for f in sorted(JOBS_DIR.glob("*/job.json")):
        try:
            job = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        job["dir"], job["cancel"] = str(f.parent), False
        ev = f.parent / "events.jsonl"
        job["events"] = [json.loads(x) for x in ev.read_text(encoding="utf-8").splitlines()] if ev.exists() else []
        JOBS[job["job_id"]] = job
        if job["status"] == "running":
            _set(job, status="failed", error="interrupted: the API restarted while this job was running", ended=_now())
            _event(job, "failed", error=job["error"])
        elif job["status"] in ("queued", "waiting_gpu"):
            _set(job, status="queued")
            WORK.put(job["job_id"])


_reload_jobs()
threading.Thread(target=_worker, daemon=True, name="gpu-job-worker").start()
