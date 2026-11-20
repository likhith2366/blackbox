"""Development experiment: coordinator v1 (frozen settings) vs v2 (change check, refuse repeats, multi-call
example) on the development incidents only (pilot INC-0000 and UI demo incidents INC-9001, INC-9002).
Never run on the frozen evaluation cases. Each run takes the shared GPU lock.

    python scripts/run_dev_v2.py            (from the blackbox-live worktree)
"""
import json
import subprocess
import sys
from pathlib import Path

LIVE = Path(__file__).resolve().parents[1]
MAIN = LIVE.parent / "blackbox"
OUT = MAIN / "runs" / "dev_v2_experiment"
INCIDENTS = ["INC-0000", "INC-9001", "INC-9002"]
ARMS = {"v1": LIVE / "configs" / "frozen_v1.yaml", "v2": LIVE / "configs" / "dev_v2.yaml"}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for inc in INCIDENTS:
        for arm, cfg in ARMS.items():
            d = OUT / f"{inc}_{arm}"
            if (d / "output.json").exists():
                print(f"skip {d.name} (done)")
                continue
            cmd = [sys.executable, str(MAIN / "scripts" / "with_gpu_lock.py"), f"dev_v2 {inc} {arm}", "--",
                   sys.executable, "-m", "blackbox.cli", "investigate", "--incident", inc, "--system", "blackbox",
                   "--config", str(cfg), "--snapshot-root", str(MAIN / "scenarios" / "snapshots"), "--out", str(d)]
            print("run", d.name, flush=True)
            subprocess.run(cmd, cwd=LIVE, check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
