"""Controlled development experiment: dev_v2 vs dev_v2c (causal-evidence gate), 2 runs each, on the development
incidents only (INC-0000, INC-9001, INC-9002). Never on frozen evaluation cases.

Runs are sequential and take the shared GPU lock. Arms are interleaved (v2, v2c, v2, v2c ...) so slow drift
in machine state does not fall on one arm. Before each run the script waits for GPU < 80 C, no thermal
throttling and >= 1.5 GB free RAM, and records the reading. Every output is kept, including failures.

    python scripts/run_gate_experiment.py
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runs" / "dev_v2c_experiment"
INCIDENTS = ["INC-0000", "INC-9001", "INC-9002"]
ARMS = {"v2": ROOT / "configs" / "dev_v2.yaml", "v2c": ROOT / "configs" / "dev_v2c.yaml"}
REPEATS = 2
sys.path.insert(0, str(ROOT / "scripts"))


def health():
    import psutil
    q = "temperature.gpu,clocks_throttle_reasons.sw_thermal_slowdown,clocks_throttle_reasons.hw_thermal_slowdown"
    try:
        o = subprocess.run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=15).stdout.strip().split(", ")
        temp, thr = int(o[0]), o[1].strip() == "Active" or o[2].strip() == "Active"
    except Exception:
        temp, thr = -1, False
    return {"gpu_temp_c": temp, "thermal_throttle": thr, "ram_free_gb": round(psutil.virtual_memory().available / 1e9, 2)}


def wait_healthy(log):
    h = health()
    while h["gpu_temp_c"] >= 80 or h["thermal_throttle"] or h["ram_free_gb"] < 1.5:
        log(f"waiting for machine health: {h}")
        time.sleep(30)
        h = health()
    return h


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    logf = OUT / "_runner.log"

    def log(msg):
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
        print(line, flush=True)
        with open(logf, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    log(f"start; code revision {rev}")
    for rep in range(1, REPEATS + 1):
        for inc in INCIDENTS:
            for arm, cfg in ARMS.items():
                d = OUT / f"{inc}_{arm}_r{rep}"
                if (d / "output.json").exists():
                    log(f"skip {d.name} (done)")
                    continue
                h = wait_healthy(log)
                log(f"run {d.name} health={h}")
                t0 = time.time()
                rc = subprocess.call([sys.executable, str(ROOT / "scripts" / "with_gpu_lock.py"), f"dev_v2c {d.name}", "--",
                                      sys.executable, "-m", "blackbox.cli", "investigate", "--incident", inc,
                                      "--system", "blackbox", "--config", str(cfg), "--out", str(d)], cwd=ROOT,
                                     stdout=subprocess.DEVNULL, stderr=open(OUT / f"{d.name}.stderr.txt", "w"))
                (d.mkdir(parents=True, exist_ok=True))
                (d / "runner.json").write_text(json.dumps({"rc": rc, "seconds": round(time.time() - t0, 1),
                                                           "health_at_start": h, "config": str(cfg.relative_to(ROOT)),
                                                           "code_revision": rev}, indent=2), encoding="utf-8")
                log(f"done {d.name} rc={rc} {time.time() - t0:.0f}s")
    log("finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
