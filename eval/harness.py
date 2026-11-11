"""Run B0, B1 and BlackBox over frozen snapshots and store complete run records.

    python -m eval.harness plan --phase pilot|eval --matrix eval/matrix.yaml
    python -m eval.harness run  --phase pilot|eval --matrix eval/matrix.yaml --batch <name>
    python -m eval.harness run  --phase eval --matrix eval/matrix.yaml --batch <name> --stub   # dev only

Rules enforced here:
- Strictly sequential: one run (hence one model request stream) at a time, and a batch
  lock file stops a second harness process on the same batch.
- Real (non-stub) runs require "PIPELINE READY" in coordination/agent1_log.md.
- Phase separation: a batch is pilot or eval for life (batch.json); the pilot phase may
  only select pilot incidents (scenario 0); eval refuses any incident whose manifest
  entry is not frozen, is superseded, or has no answer key.
- Resume: a run whose meta.json has a terminal status is skipped; a run left in
  "running" state by an interruption is moved to _interrupted/ and re-run.
- Failures are results: exceptions, failed/timeout outputs and skipped B1 runs all get
  a run directory with meta.json; nothing is dropped.
- --stub writes only to runs/_dev_stub/<batch>/ with "stub": true in every record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from blackbox.schemas import EvidenceRecord, RunConfig, SystemOutput

from eval.common import (DEFAULT_ANSWER_KEYS, DEFAULT_MANIFEST, PRIMARY_DIAGNOSABLE_SCENARIOS,
                         REPO_ROOT, RUNS_ROOT, STUB_ROOT, ManifestEntry, active_entries,
                         load_answer_key, load_manifest, pipeline_ready, read_json, write_json)

TERMINAL = {"completed", "failed", "timeout", "exception", "skipped_dependency",
            "refused_live", "invalid_live_data"}
SNAPSHOT_ROOT = REPO_ROOT / "scenarios" / "snapshots"


# --------------------------------------------------------------------------- live-data guard
def live_backend_problem(backend) -> Optional[str]:
    """Why a backend is not an acceptable frozen-snapshot backend for the eval phase (None = ok).

    Evaluation uses frozen snapshots only (CONTRACT 10c): the backend must be the snapshot
    backend (blackbox.evidence.backend.SnapshotBackend), must not declare a live mode, and
    must read from scenarios/snapshots/.
    """
    cls = type(backend)
    if not (cls.__module__ == "blackbox.evidence.backend" and cls.__name__ == "SnapshotBackend"):
        return f"backend class {cls.__module__}.{cls.__name__} is not the snapshot backend"
    if getattr(backend, "live", False) or str(getattr(backend, "mode", "snapshot")).lower() == "live":
        return "backend declares live mode"
    for attr in ("snap", "snapshot_dir", "path", "root", "_snap"):
        v = getattr(backend, attr, None)
        if isinstance(v, (str, Path)):
            try:
                if not Path(v).resolve().is_relative_to(SNAPSHOT_ROOT.resolve()):
                    return f"backend reads {v}, outside scenarios/snapshots/"
            except OSError:
                pass
    return None


def file_sha256(path: Path) -> Optional[str]:
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def run_provenance(incident_id: str, keys_dir: Path, snapshot_root: Optional[Path]) -> dict:
    """Which answer key and snapshot a run was made against (sha256s), so a later rebuild
    of either is detected at scoring time instead of silently re-scoring old outputs."""
    root = Path(snapshot_root) if snapshot_root else SNAPSHOT_ROOT
    return {"answer_key_sha256": file_sha256(Path(keys_dir) / f"{incident_id}.json"),
            "snapshot_manifest_sha256": file_sha256(root / incident_id / "manifest.json"),
            "snapshot_incident_sha256": file_sha256(root / incident_id / "incident.json")}


def live_evidence_ids(out: SystemOutput) -> list[str]:
    """Evidence ids in a run output that came from a live connector."""
    recs = list(out.evidence_seen)
    for s in out.steps:
        for r in s.tool_results:
            recs.extend(r.records)
    bad = [r.evidence_id for r in recs
           if r.attributes.get("live") is True or r.evidence_id.startswith("gh:")]
    return list(dict.fromkeys(bad))
SYSTEM_ORDER = {"blackbox": 0, "b1": 1, "b0": 2}


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def run_id_for(incident_id: str, system: str, ablation: str, repeat: int) -> str:
    return f"{incident_id}_{system}_{ablation}_r{repeat}"


# --------------------------------------------------------------------------- planning
class PlanError(Exception):
    pass


def load_matrix(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _select(sel: dict, entries: list[ManifestEntry], keys_dir: Path) -> list[ManifestEntry]:
    out = entries
    if "scenario_numbers" in sel:
        nums = set(int(n) for n in sel["scenario_numbers"])
        out = [e for e in out if e.scenario_number in nums]
    if sel.get("diagnosable_primary"):
        keep = []
        for e in out:
            k = load_answer_key(e.incident_id, keys_dir)
            if k is not None:
                if k.is_primary and k.diagnosable:
                    keep.append(e)
            elif e.scenario_number in PRIMARY_DIAGNOSABLE_SCENARIOS:
                keep.append(e)
        out = keep
    return out


def build_plan(matrix: dict, phase: str, manifest: dict[str, ManifestEntry],
               keys_dir: Path) -> list[dict[str, Any]]:
    if phase not in matrix.get("phases", {}):
        raise PlanError(f"phase {phase!r} not defined in matrix")
    spec = matrix["phases"][phase]
    entries = active_entries(manifest)
    if phase == "eval":
        nums = [e.scenario_number for e in entries if e.scenario_number >= 1]
        dup = sorted({n for n in nums if nums.count(n) > 1})
        if dup:
            raise PlanError(f"several active (non-superseded) incidents for scenario(s) {dup}; "
                            "mark old ones superseded in the manifest")
    plan: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cell in spec["cells"]:
        sel = _select(cell.get("select", {}), entries, keys_dir)
        for e in sel:
            for system in sorted(cell["systems"], key=lambda s: SYSTEM_ORDER.get(s, 9)):
                for abl in cell.get("ablations", ["none"]):
                    for rep in cell.get("repeats", [0]):
                        rid = run_id_for(e.incident_id, system, abl, int(rep))
                        if rid in seen:
                            continue
                        seen.add(rid)
                        plan.append({"run_id": rid, "incident_id": e.incident_id,
                                     "system": system, "ablation": abl, "repeat_index": int(rep),
                                     "cell": cell["name"], "scenario_number": e.scenario_number,
                                     "scenario_type": e.scenario_type, "phase": phase})
    # B1 dependency: the initial BlackBox run must come earlier in the plan
    pos = {p["run_id"]: i for i, p in enumerate(plan)}
    for i, p in enumerate(plan):
        if p["system"] == "b1":
            dep = run_id_for(p["incident_id"], "blackbox", "none", 0)
            p["b1_source_run"] = dep
            if dep not in pos or pos[dep] > i:
                raise PlanError(f"{p['run_id']} needs {dep} planned before it")
    return plan


def classify_incidents(plan: list[dict], phase: str, manifest: dict[str, ManifestEntry],
                       keys_dir: Path, require_frozen: bool) -> tuple[list[str], dict[str, str]]:
    """(blocking problems, {incident_id: skip reason}).

    Blocking: phase separation violations (refuse the whole batch). Skips: an eval incident
    that is not frozen, superseded or has no answer key is NOT run; its planned runs stay in
    plan.json with skip_reason and are scored as not_run (never dropped).
    """
    blocking, skips = [], {}
    for iid in dict.fromkeys(p["incident_id"] for p in plan):
        e = manifest[iid]
        if phase == "pilot" and e.scenario_type != "pilot" and e.scenario_number != 0:
            blocking.append(f"{iid}: pilot phase may only use pilot incidents "
                            f"(this is scenario {e.scenario_number}); do not tune on eval cases")
        if phase == "eval":
            if e.scenario_number == 0 or e.scenario_type == "pilot":
                blocking.append(f"{iid}: pilot incident selected in eval phase")
            reasons = []
            if require_frozen and not e.frozen:
                reasons.append("manifest entry not frozen")
            if e.superseded:
                reasons.append("superseded")
            if load_answer_key(iid, keys_dir) is None:
                reasons.append("no answer key")
            if reasons:
                skips[iid] = "; ".join(reasons)
    return blocking, skips


def validate_phase(plan: list[dict], phase: str, manifest: dict[str, ManifestEntry],
                   keys_dir: Path, require_frozen: bool) -> list[str]:
    """All problems as text (blocking + per-incident skips), for `plan` output and tests."""
    blocking, skips = classify_incidents(plan, phase, manifest, keys_dir, require_frozen)
    return blocking + [f"{iid}: {r}" for iid, r in skips.items()]


# --------------------------------------------------------------------------- GPU lock
GPU_LOCK = RUNS_ROOT / "_gpu.lock"


class GpuLock:
    """Cross-process GPU lock, CONTRACT s12: runs/_gpu.lock = {pid, owner, started}, created
    with exclusive create, removed on release. A lock held by an ANCESTOR process (e.g.
    scripts/with_gpu_lock.py wrapping this harness) counts as held: no deadlock, no release.
    A lock whose pid no longer exists is stale and removed."""

    def __init__(self, owner: str, path: Path = GPU_LOCK, wait_s: float = 15.0,
                 log: Callable[[str], None] = print):
        self.owner, self.path, self.wait_s, self.log = owner, Path(path), wait_s, log
        self.mine = False
        self.via = None

    def _holder(self) -> Optional[dict]:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except ValueError:
            return {"pid": None, "owner": "unreadable lock file"}
        pid = d.get("pid")
        if pid:
            try:
                import psutil
                if not psutil.pid_exists(pid):
                    self.path.unlink(missing_ok=True)
                    return None
            except ImportError:
                pass
        return d

    @staticmethod
    def _ancestors() -> set[int]:
        try:
            import psutil
            return {p.pid for p in psutil.Process().parents()}
        except Exception:
            return set()

    def __enter__(self):
        while True:
            h = self._holder()
            if h and h.get("pid") in self._ancestors():
                self.via = f"held by parent process {h.get('pid')} ({h.get('owner')})"
                return self
            if h is None:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with open(self.path, "x", encoding="utf-8") as fh:
                        json.dump({"pid": os.getpid(), "owner": self.owner, "started": utcnow()}, fh)
                    self.mine, self.via = True, "acquired by eval.harness"
                    return self
                except FileExistsError:
                    pass
            self.log(f"waiting for GPU lock held by: {(h or {}).get('owner')}")
            time.sleep(self.wait_s)

    def __exit__(self, *exc):
        if self.mine:
            h = self._holder()
            if h and h.get("pid") == os.getpid():
                self.path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- metadata
def git_info() -> dict[str, Any]:
    info: dict[str, Any] = {"git_head": None, "git_dirty": None}
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                              text=True, timeout=20)
        if head.returncode != 0:
            info["git_error"] = head.stderr.strip()[:300]
            return info
        info["git_head"] = head.stdout.strip()
        st = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True,
                            text=True, timeout=20)
        info["git_dirty"] = bool(st.stdout.strip()) if st.returncode == 0 else None
    except Exception as e:  # git missing
        info["git_error"] = str(e)[:300]
    return info


def model_metadata(cfg: RunConfig) -> dict[str, Any]:
    """Metadata only (/api/show, /api/tags): never a generation request."""
    import requests
    md: dict[str, Any] = {"model": cfg.model, "model_digest": None}
    try:
        r = requests.post(f"{cfg.ollama_url}/api/show", json={"model": cfg.model}, timeout=15)
        r.raise_for_status()
        j = r.json()
        md["model_details"] = j.get("details")
        md["model_modified_at"] = j.get("modified_at")
        md["model_digest"] = j.get("digest")
    except Exception as e:
        md["model_show_error"] = str(e)[:300]
    if not md["model_digest"]:
        try:
            r = requests.get(f"{cfg.ollama_url}/api/tags", timeout=15)
            r.raise_for_status()
            for m in r.json().get("models", []):
                if m.get("name") == cfg.model or m.get("model") == cfg.model:
                    md["model_digest"] = m.get("digest")
        except Exception as e:
            md["model_tags_error"] = str(e)[:300]
    return md


def make_config(matrix: dict, phase: str, code_revision: str) -> tuple[RunConfig, dict]:
    """RunConfig = defaults <- run_config_file (phase-level overrides top-level) <- run_config.

    Returns the config and its provenance (file path + sha256) for batch.json/meta.json.
    A configured file that does not exist is an error (the eval phase must not silently
    fall back to defaults before configs/frozen_v1.yaml is created).
    """
    spec = matrix.get("phases", {}).get(phase, {})
    f = spec.get("run_config_file", matrix.get("run_config_file"))
    base: dict[str, Any] = {}
    src: dict[str, Any] = {"run_config_file": None, "run_config_sha256": None}
    if f:
        p = Path(f)
        if not p.is_absolute():
            p = REPO_ROOT / p
        if not p.exists():
            raise PlanError(f"run_config_file for phase {phase!r} not found: {p}")
        raw = p.read_bytes()
        txt = raw.decode("utf-8")
        base = (json.loads(txt) if p.suffix == ".json" else yaml.safe_load(txt)) or {}
        src = {"run_config_file": p.relative_to(REPO_ROOT).as_posix() if p.is_relative_to(REPO_ROOT) else str(p),
               "run_config_sha256": hashlib.sha256(raw).hexdigest()}
    base.update(matrix.get("run_config") or {})
    base.update(spec.get("run_config") or {})
    cfg = RunConfig(**base)
    if not cfg.code_revision:
        cfg.code_revision = code_revision
    return cfg, src


# --------------------------------------------------------------------------- execution
class BatchLock:
    def __init__(self, path: Path, break_lock: bool):
        self.path, self.break_lock, self.fd = path, break_lock, None

    def __enter__(self):
        if self.break_lock and self.path.exists():
            self.path.unlink()
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise SystemExit(f"batch locked by another harness ({self.path}); "
                             "use --break-lock only if no harness is running")
        os.write(self.fd, f"{os.getpid()} {utcnow()}".encode())
        return self

    def __exit__(self, *exc):
        os.close(self.fd)
        self.path.unlink(missing_ok=True)


def _hlog(batch_dir: Path, **kw):
    with open(batch_dir / "harness_log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": utcnow(), **kw}, default=str) + "\n")


def run_batch(*, plan: list[dict], batch_dir: Path, phase: str, stub: bool, cfg: RunConfig,
              system_fn: Callable, backend_fn: Callable, base_meta: dict,
              repeat_seed_strategy: str = "same", max_runs: Optional[int] = None,
              provenance_fn: Optional[Callable[[str], dict]] = None,
              log: Callable[[str], None] = print) -> dict[str, int]:
    counts = {"skipped_done": 0, "executed": 0, "skipped_with_reason": 0}
    executed = 0
    for p in plan:
        rid = p["run_id"]
        if p.get("skip_reason"):      # stays planned (scored as not_run); no run dir is created
            counts["skipped_with_reason"] += 1
            _hlog(batch_dir, event="skip", run_id=rid, reason=p["skip_reason"])
            continue
        rdir = batch_dir / rid
        mpath = rdir / "meta.json"
        if mpath.exists():
            st = read_json(mpath).get("status")
            if st in TERMINAL:
                counts["skipped_done"] += 1
                continue
        if rdir.exists():  # interrupted earlier: keep it, move aside
            dest = batch_dir / "_interrupted" / f"{rid}_{int(time.time())}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(rdir), str(dest))
            _hlog(batch_dir, event="moved_interrupted", run_id=rid, to=str(dest))
        if max_runs is not None and executed >= max_runs:
            break
        rdir.mkdir(parents=True)
        run_cfg = cfg.model_copy(deep=True)
        if repeat_seed_strategy == "offset":
            run_cfg.seed = cfg.seed + p["repeat_index"]
        inp = {**p, "stub": stub, "config": run_cfg.model_dump()}
        write_json(rdir / "input.json", inp)
        meta = {**base_meta, "run_id": rid, "stub": stub, "phase": phase, "status": "running",
                "seed": run_cfg.seed, "prompt_version": run_cfg.prompt_version,
                "decoding": {k: getattr(run_cfg, k) for k in
                             ("temperature", "top_p", "seed", "num_ctx", "num_predict", "think")},
                "run_config": run_cfg.model_dump(),
                **(provenance_fn(p["incident_id"]) if provenance_fn else {}),
                "start_time": utcnow(), "end_time": None, "wall_seconds": None,
                "exception": None}
        write_json(mpath, meta)
        _hlog(batch_dir, event="start", run_id=rid)
        log(f"[{executed + 1}] {rid} ...")
        t0 = time.perf_counter()
        out: Optional[SystemOutput] = None
        try:
            b1_ev: Optional[list[EvidenceRecord]] = None
            if p["system"] == "b1":
                src = batch_dir / p["b1_source_run"] / "output.json"
                meta["b1_source_run"] = p["b1_source_run"]
                if not src.exists():
                    meta["status"] = "skipped_dependency"
                    meta["exception"] = (f"initial BlackBox run {p['b1_source_run']} has no "
                                         "output.json; B1 cannot be given its evidence")
                    raise _Skip()
                src_out = SystemOutput.model_validate_json(src.read_text(encoding="utf-8"))
                b1_ev = src_out.evidence_seen
                meta["b1_evidence_count"] = len(b1_ev)
                # QA-N2: a B1 run built from a failed/timed-out BlackBox run is recorded but
                # excluded from same-evidence comparisons (see eval/report.py b1_section)
                meta["b1_source_status"] = src_out.status
            backend = backend_fn(p["incident_id"], p["ablation"])
            if phase == "eval" and not stub:
                problem = live_backend_problem(backend)
                if problem:
                    meta["status"] = "refused_live"
                    meta["exception"] = f"eval phase refuses non-snapshot backend: {problem}"
                    raise _Skip()
            out = system_fn(p["system"], backend, run_cfg, b1_evidence=b1_ev)
            d = out.model_dump()
            if stub:
                d["stub"] = True
            write_json(rdir / "output.json", d)
            meta["status"] = out.status
            if phase == "eval" and not stub:
                live_ids = live_evidence_ids(out)
                if live_ids:   # kept on disk as a result, but never scored as a valid run
                    meta["status"] = "invalid_live_data"
                    meta["exception"] = f"live evidence in an eval run: {live_ids[:5]}"
            meta["system_error"] = out.error
            meta["prompt_versions"] = out.prompt_versions
            if out.prompt_versions and not meta["prompt_version"]:
                meta["prompt_version"] = json.dumps(out.prompt_versions, sort_keys=True)
        except _Skip:
            pass
        except BaseException as e:  # KeyboardInterrupt also leaves a record, then re-raises
            meta["status"] = "exception" if not isinstance(e, KeyboardInterrupt) else "running"
            meta["exception"] = f"{type(e).__name__}: {e}"
            (rdir / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
            if isinstance(e, KeyboardInterrupt):
                write_json(mpath, meta)
                raise
        wall = time.perf_counter() - t0
        meta["end_time"] = utcnow()
        meta["wall_seconds"] = round(wall, 3)
        meta["exceeded_run_timeout"] = wall > run_cfg.run_timeout_s
        write_json(mpath, meta)
        _hlog(batch_dir, event="end", run_id=rid, status=meta["status"], wall=meta["wall_seconds"])
        log(f"    -> {meta['status']} ({meta['wall_seconds']} s)"
            + (f" {meta['exception']}" if meta["exception"] else ""))
        executed += 1
    counts["executed"] = executed
    return counts


class _Skip(Exception):
    pass


# --------------------------------------------------------------------------- CLI
def _real_fns(snapshot_root: Optional[Path]):
    from blackbox.evidence.backend import load_backend  # Agent 2
    from blackbox.systems import run_system              # Agent 1

    def backend_fn(iid, abl):
        return load_backend(iid, ablation=abl, snapshot_root=snapshot_root)
    return run_system, backend_fn


def _stub_fns():
    from eval.stub import StubBackend, run_stub_system
    return run_stub_system, (lambda iid, abl: StubBackend(iid, abl))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.harness")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "run"):
        sp = sub.add_parser(name)
        sp.add_argument("--phase", choices=["pilot", "eval"], required=True)
        sp.add_argument("--matrix", default="eval/matrix.yaml")
        sp.add_argument("--manifest", default=None)
        sp.add_argument("--answer-keys", default=None)
        sp.add_argument("--snapshot-root", default=None)
        sp.add_argument("--stub", action="store_true",
                        help="development only: stub system + synthetic fixtures, writes to runs/_dev_stub/")
        if name == "run":
            sp.add_argument("--batch", required=True)
            sp.add_argument("--max-runs", type=int, default=None)
            sp.add_argument("--break-lock", action="store_true")
            sp.add_argument("--no-gpu-lock", action="store_true",
                            help="do not take runs/_gpu.lock (only if another wrapper serialises GPU use)")
        sp.add_argument("--cases", default=None,
                        help="comma-separated incident ids to run now; other planned runs stay planned (not_run)")
    a = ap.parse_args(argv)

    matrix_path = Path(a.matrix)
    if not matrix_path.is_absolute():
        matrix_path = REPO_ROOT / matrix_path
    matrix = load_matrix(matrix_path)

    if a.stub:
        from eval.stub import create_stub_fixtures
        manifest_path, keys_dir = create_stub_fixtures(STUB_ROOT / "_fixtures")
    else:
        manifest_path = Path(a.manifest) if a.manifest else DEFAULT_MANIFEST
        keys_dir = Path(a.answer_keys) if a.answer_keys else DEFAULT_ANSWER_KEYS
    if not manifest_path.exists():
        print(f"manifest not found: {manifest_path} (Agent 2 has not built scenarios yet)",
              file=sys.stderr)
        return 2
    manifest = load_manifest(manifest_path)
    try:
        plan = build_plan(matrix, a.phase, manifest, keys_dir)
    except PlanError as e:
        print(f"plan error: {e}", file=sys.stderr)
        return 2
    spec = matrix["phases"][a.phase]
    blocking, skips = classify_incidents(plan, a.phase, manifest, keys_dir,
                                         bool(spec.get("require_frozen")))
    cases = [c.strip() for c in (a.cases or "").split(",") if c.strip()]
    unknown = [c for c in cases if c not in {p["incident_id"] for p in plan}]
    if unknown:
        blocking.append(f"--cases not in the {a.phase} plan: {unknown}")
    for p in plan:     # every planned run stays in plan.json; skipped ones carry the reason
        reason = skips.get(p["incident_id"])
        if cases and p["incident_id"] not in cases:
            reason = "not selected (--cases)" + (f"; {reason}" if reason else "")
        if reason:
            p["skip_reason"] = reason
    warnings_ = []
    exp = spec.get("expected_total")
    if exp is not None and len(plan) != exp:
        warnings_.append(f"plan has {len(plan)} runs, matrix expects {exp} "
                         "(scenarios missing from the manifest are not planned yet)")
    runnable = [p for p in plan if not p.get("skip_reason")]

    if a.cmd == "plan":
        for p in plan:
            print(f"{p['cell']:10s} {p['run_id']}"
                  + (f"   SKIP: {p['skip_reason']}" if p.get("skip_reason") else ""))
        print(f"total: {len(plan)}  unique run ids: {len({p['run_id'] for p in plan})}  "
              f"runnable now: {len(runnable)}")
        try:
            _, src = make_config(matrix, a.phase, "plan")
            print(f"run config: {src['run_config_file'] or 'RunConfig defaults'} "
                  f"(sha256 {src['run_config_sha256']})")
        except PlanError as e:
            blocking.append(str(e))
        for w in warnings_:
            print(f"WARNING: {w}")
        for iid, r in skips.items():
            print(f"SKIP {iid}: {r}")
        for pr in blocking:
            print(f"PROBLEM: {pr}")
        return 0 if not blocking else 1

    if blocking:
        print("refusing to run:", *blocking, sep="\n  ", file=sys.stderr)
        return 2
    for w in warnings_:
        print(f"WARNING: {w}")
    for iid, r in skips.items():
        print(f"SKIP {iid}: {r} (its runs stay planned and are scored as not_run)")
    if not runnable:
        print("nothing runnable: every planned run is skipped (see SKIP lines)", file=sys.stderr)
        return 2
    if not a.stub and not pipeline_ready():
        print("refusing: coordination/agent1_log.md has no 'PIPELINE READY' line", file=sys.stderr)
        return 2
    if a.stub and a.batch.startswith(("..", "/", "\\")):
        return 2
    if a.phase == "eval" and not a.stub:
        if a.snapshot_root and not Path(a.snapshot_root).resolve().is_relative_to(SNAPSHOT_ROOT.resolve()):
            print("refusing: eval phase uses frozen snapshots under scenarios/snapshots/ only",
                  file=sys.stderr)
            return 2
        _, bfn = _real_fns(Path(a.snapshot_root) if a.snapshot_root else None)
        for iid in dict.fromkeys(p["incident_id"] for p in runnable):
            prob = live_backend_problem(bfn(iid, "none"))
            if prob:
                print(f"refusing: {iid}: {prob} (live data never enters the eval matrix)",
                      file=sys.stderr)
                return 2

    root = STUB_ROOT if a.stub else RUNS_ROOT
    batch_dir = root / a.batch
    if not a.stub and batch_dir.resolve().is_relative_to(STUB_ROOT.resolve()):
        print("refusing: real runs never go under runs/_dev_stub/", file=sys.stderr)
        return 2
    gi = git_info()
    try:
        cfg, cfg_src = make_config(matrix, a.phase, (gi.get("git_head") or "nogit")
                                   + ("-dirty" if gi.get("git_dirty") else ""))
    except PlanError as e:
        print(f"refusing: {e}", file=sys.stderr)
        return 2
    batch_dir.mkdir(parents=True, exist_ok=True)
    matrix_sha = hashlib.sha256(matrix_path.read_bytes()).hexdigest()
    bpath = batch_dir / "batch.json"
    if bpath.exists():
        old = read_json(bpath)
        if old.get("run_config_sha256") != cfg_src["run_config_sha256"]:
            print("refusing: run_config_file changed since this batch started; use a new batch name",
                  file=sys.stderr)
            return 2
        if old.get("phase") != a.phase:
            print(f"refusing: batch {a.batch} is a {old.get('phase')} batch", file=sys.stderr)
            return 2
        if old.get("stub") != a.stub:
            print("refusing: cannot mix stub and real runs in one batch", file=sys.stderr)
            return 2
        if old.get("matrix_sha256") != matrix_sha:
            print("refusing: matrix changed since this batch started; use a new batch name",
                  file=sys.stderr)
            return 2

    md = ({"model": cfg.model, "model_digest": "STUB-no-model"} if a.stub else model_metadata(cfg))
    base_meta = {**gi, **md, **cfg_src, "python": sys.version.split()[0],
                 "platform": platform.platform(), "batch": a.batch, "matrix_sha256": matrix_sha}
    batch_meta = {"batch": a.batch, "phase": a.phase, "stub": a.stub,
                  "created_at": read_json(bpath)["created_at"] if bpath.exists() else utcnow(),
                  "last_started_at": utcnow(), "matrix_path": str(matrix_path),
                  "matrix_sha256": matrix_sha, "manifest_path": str(manifest_path),
                  "answer_keys_dir": str(keys_dir), "snapshot_root": a.snapshot_root,
                  "run_config": cfg.model_dump(), **cfg_src, **gi, **md,
                  "expected_total": exp, "planned_total": len(plan), "plan_warnings": warnings_,
                  "skipped_incidents": skips, "cases": cases or None}
    write_json(bpath, batch_meta)
    write_json(batch_dir / "plan.json", {"phase": a.phase, "runs": plan})

    system_fn, backend_fn = (_stub_fns() if a.stub
                             else _real_fns(Path(a.snapshot_root) if a.snapshot_root else None))
    from contextlib import nullcontext
    gpu = (nullcontext() if (a.stub or a.no_gpu_lock)
           else GpuLock(f"eval.harness {a.phase} {a.batch}"))
    with BatchLock(batch_dir / "_lock", a.break_lock), gpu:
        if isinstance(gpu, GpuLock):
            print(f"GPU lock: {gpu.via}")
            batch_meta["gpu_lock"] = gpu.via
        counts = run_batch(plan=plan, batch_dir=batch_dir, phase=a.phase, stub=a.stub, cfg=cfg,
                           system_fn=system_fn, backend_fn=backend_fn, base_meta=base_meta,
                           repeat_seed_strategy=matrix.get("repeat_seed_strategy", "same"),
                           max_runs=a.max_runs,
                           provenance_fn=lambda iid: run_provenance(
                               iid, keys_dir, Path(a.snapshot_root) if a.snapshot_root else None))
    if not a.stub:
        end_md = model_metadata(cfg)
        batch_meta["model_digest_end"] = end_md.get("model_digest")
        batch_meta["model_changed_during_batch"] = end_md.get("model_digest") != md.get("model_digest")
    batch_meta["last_finished_at"] = utcnow()
    write_json(bpath, batch_meta)
    print(f"batch {batch_dir}: executed {counts['executed']}, already done {counts['skipped_done']}, "
          f"skipped with reason {counts['skipped_with_reason']}, planned {len(plan)}"
          + (f" (matrix expects {exp})" if exp is not None and exp != len(plan) else ""))
    if a.stub:
        print("STUB batch: development output only, not a result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
