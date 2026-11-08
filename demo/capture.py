"""Run one real capture: healthy baseline -> timed changes -> fault period, all under load.

Used by `python -m scenarios.build`. Everything written under the capture directory is
produced by actually running the services; nothing is synthesised afterwards.

    <capture>/telemetry/{logs,spans,metrics}/<service>.jsonl   (written by the services)
    <capture>/telemetry/loadgen.jsonl                          (client-side results)
    <capture>/deployments.jsonl, builds/<id>.json               (from the build/deploy tooling)
    <capture>/repo_export.json                                  (company repo at capture end)
    <capture>/capture_meta.json                                 (PRIVATE timeline incl. change ids)
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field

from demo import controller, deploy, repo_export
from demo.loadgen import LoadGenerator
from demo.paths import BUILDS, CAPTURES, DEPLOYMENTS, RUNTIME, SERVICES, load_state, save_state
from demo.timeutil import iso, parse


@dataclass
class Step:
    at_s: float          # seconds after load start
    change: str          # id in demo.controller_faults.CHANGES


@dataclass
class CapturePlan:
    seed: int
    duration_s: float
    schedule: list = field(default_factory=lambda: [(0, 5.0)])
    steps: list = field(default_factory=list)
    special_orders: dict = field(default_factory=dict)   # order index -> extra fields


def write_provenance(cdir, t_start: float, t_end: float) -> None:
    """Copy the release provenance the tooling produced in [t_start, t_end] and export the repo."""
    deps = []
    if DEPLOYMENTS.exists():
        for line in DEPLOYMENTS.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if t_start - 1 <= parse(r["started_at"]) <= t_end:
                deps.append(r)
    (cdir / "deployments.jsonl").write_text("".join(json.dumps(d) + "\n" for d in deps), encoding="utf-8")
    (cdir / "builds").mkdir(exist_ok=True)
    for bid in sorted({d["build_id"] for d in deps}):
        shutil.copyfile(BUILDS / f"{bid}.json", cdir / "builds" / f"{bid}.json")
    (cdir / "repo_export.json").write_text(json.dumps(repo_export.export(), indent=1), encoding="utf-8")


def run_capture(plan: CapturePlan, capture_id: str, log=print) -> dict:
    from demo.lock import demo_lock
    with demo_lock(f"scenario capture {capture_id}"):
        return _run_capture(plan, capture_id, log)


def _run_capture(plan: CapturePlan, capture_id: str, log=print) -> dict:
    cdir = CAPTURES / capture_id
    if cdir.exists():
        shutil.rmtree(cdir)
    tdir = cdir / "telemetry"
    tdir.mkdir(parents=True)
    t_start = time.time()
    log(f"[{capture_id}] deploying healthy baseline")
    controller.baseline(str(tdir))
    lg = LoadGenerator(plan.schedule, plan.seed, out_path=tdir / "loadgen.jsonl",
                       special={int(k): v for k, v in plan.special_orders.items()})
    load_start = time.time()
    lg.start(plan.duration_s)
    events = []
    try:
        for st in sorted(plan.steps, key=lambda s: s.at_s):
            wait = load_start + st.at_s - time.time()
            if wait > 0:
                time.sleep(wait)
            log(f"[{capture_id}] t+{time.time() - load_start:.0f}s applying change")
            events.append(controller.apply(st.change))
        lg.thread.join()
    finally:
        lg.stop(wait=True)
        time.sleep(6)  # let one more metric interval flush
    t_end = time.time()
    for svc in reversed(SERVICES):
        deploy.stop(svc, reason="redeploy")
    # later manual runs (demo.run / controller) must not append into this capture
    st = load_state()
    st["telemetry_dir"] = str(RUNTIME / "telemetry" / "live")
    save_state(st)
    # release provenance produced by the tooling during this capture
    write_provenance(cdir, t_start, t_end)
    meta = {"capture_id": capture_id, "plan": asdict(plan), "t_start": iso(t_start),
            "load_start": iso(load_start), "t_end": iso(t_end), "events": events,
            "load_stats": lg.stats}
    (cdir / "capture_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log(f"[{capture_id}] done: {lg.stats}")
    return meta
