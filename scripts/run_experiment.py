"""Before/after pilot experiment: same code revision, same frozen snapshot, configs differ
only in coverage_hint. Runs interleaved (before, after, before, after); every outcome kept."""
import hashlib
import json
import sys
import time
from pathlib import Path

from blackbox.cli import load_config
from blackbox.evidence.backend import load_backend
from blackbox.systems import code_revision, run_system

OUT = Path("runs/exp_coverage_v1")
ROOT = OUT / "snapshot_root"
PLAN = [("before", 1), ("after", 1), ("before", 2), ("after", 2)]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    rev = code_revision()
    snap_manifest = sha(ROOT / "INC-0000" / "manifest.json")
    for name, rep in PLAN:
        run_dir = OUT / f"{name}_r{rep}"
        if (run_dir / "output.json").exists():
            print(f"skip {run_dir.name} (done)")
            continue
        run_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = Path(f"configs/exp_{name}.yaml")
        cfg = load_config(str(cfg_path))
        meta = {"run": run_dir.name, "phase": "development experiment (not evaluation)",
                "config_file": str(cfg_path), "config_sha256": sha(cfg_path), "code_revision": rev,
                "snapshot": "INC-0000 (frozen copy)", "snapshot_manifest_sha256": snap_manifest,
                "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "status": "running"}
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"[{run_dir.name}] start", flush=True)
        try:
            out = run_system("blackbox", load_backend("INC-0000", snapshot_root=ROOT), cfg)
            (run_dir / "output.json").write_text(out.model_dump_json(indent=2), encoding="utf-8")
            meta.update(status=out.status, wall_seconds=out.wall_seconds)
        except Exception as exc:  # a crash is a result too
            meta.update(status="exception", error=f"{type(exc).__name__}: {exc}")
        meta["ended"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"[{run_dir.name}] {meta['status']} {meta.get('wall_seconds')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
