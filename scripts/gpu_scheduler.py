"""Share the single GPU between the frozen evaluation and interactive dashboard jobs.

Replaces the one-off reorder (scripts/reorder_gpu.py). No evaluation code or setting changes:
1. wait for the end of the given run in the running eval_v1 harness (the s1 trio's last run) and
   stop that harness at the run boundary; a run that had just started is left "running" and the
   harness itself moves it to _interrupted/ and re-runs it (its supported resume behaviour);
2. loop: if the BlackBox API has interactive jobs queued/waiting/running, wait for them (the API
   worker takes the GPU lock); otherwise run the identical harness command with --max-runs 1
   (the harness takes and releases the GPU lock for that single run);
3. stop when the harness reports it executed nothing (batch complete for the frozen cases).
Every decision is appended to runs/eval_v1/_scheduler.log.
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
BATCH = ROOT / "runs" / "eval_v1"
LOG = BATCH / "_scheduler.log"
STOP_AFTER = sys.argv[1] if len(sys.argv) > 1 else "INC-3172_b0_none_r0"
CASES = sys.argv[2] if len(sys.argv) > 2 else "INC-3172,INC-5408,INC-2691,INC-7735"
HARNESS = [sys.executable, "-u", "-m", "eval.harness", "run", "--phase", "eval", "--matrix", "eval/matrix.yaml",
           "--batch", "eval_v1", "--cases", CASES, "--max-runs", "1"]


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def harness_procs():
    return [p for p in psutil.process_iter(["pid", "cmdline"])
            if p.info["cmdline"] and "eval.harness" in " ".join(p.info["cmdline"]) and "eval_v1" in p.info["cmdline"]]


def ended(run_id):
    for line in (BATCH / "harness_log.jsonl").read_text(encoding="utf-8").splitlines():
        ev = json.loads(line)
        if ev.get("event") == "end" and ev.get("run_id") == run_id:
            return True
    return False


def machine_health():
    """GPU temperature/clock/throttle and free RAM, from nvidia-smi and psutil."""
    q = "temperature.gpu,clocks.sm,clocks.max.sm,clocks_throttle_reasons.sw_thermal_slowdown,clocks_throttle_reasons.hw_thermal_slowdown"
    try:
        out = subprocess.run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15).stdout.strip().split(", ")
        temp, sm, smax = int(out[0]), int(out[1]), int(out[2])
        # exact match: "Not Active" also contains "Active" (bug until 2026-11-20 04:08Z: always True)
        throttled = out[3].strip() == "Active" or out[4].strip() == "Active"
    except Exception:
        temp, sm, smax, throttled = -1, -1, -1, False
    free = psutil.virtual_memory().available / 1e9
    return {"gpu_temp_c": temp, "sm_mhz": sm, "sm_max_mhz": smax, "thermal_throttle": throttled, "ram_free_gb": round(free, 2)}


def wait_until_healthy():
    """Do not start a frozen-evaluation run on a thermally throttled GPU or under memory exhaustion.
    No time limit: the scheduler waits as long as throttling persists (a 5-minute "start anyway"
    limit was tried on 2026-11-19 and withdrawn). Conditions are logged, not used to explain results."""
    h = machine_health()
    while h["gpu_temp_c"] >= 80 or h["thermal_throttle"] or h["ram_free_gb"] < 1.5:
        log(f"waiting for machine health before next run: {h}")
        time.sleep(60)
        h = machine_health()
    return h


def interactive_jobs():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8700/api/jobs", timeout=10) as r:
            return [j for j in json.loads(r.read()) if j["status"] in ("queued", "waiting_gpu", "running")]
    except Exception:
        return []   # API down: nothing interactive to yield to


if STOP_AFTER != "none":
    log(f"waiting for end of {STOP_AFTER} in the running batch")
    while harness_procs() and not ended(STOP_AFTER):
        time.sleep(1)
    for p in harness_procs():
        log(f"stopping batch harness pid {p.pid} at the run boundary after {STOP_AFTER}")
        p.terminate()
    psutil.wait_procs(harness_procs(), timeout=30)

while True:
    jobs = interactive_jobs()
    if jobs:
        log(f"yielding GPU to {len(jobs)} interactive job(s): {[j['job_id'] for j in jobs]}")
        while interactive_jobs():
            time.sleep(10)
        log("interactive jobs finished")
        continue
    h = wait_until_healthy()
    if interactive_jobs():
        continue
    log(f"machine health at run start: {h}")
    out = subprocess.run(HARNESS, cwd=ROOT, capture_output=True, text=True)
    with open(ROOT / "runs" / "eval_v1_scheduled.out", "a", encoding="utf-8") as fh:
        fh.write(out.stdout + out.stderr)
    m = re.search(r"executed (\d+)", out.stdout)
    executed = int(m.group(1)) if m else -1
    log(f"harness --max-runs 1 rc={out.returncode} executed={executed}")
    if executed == 0 or (out.returncode != 0 and executed <= 0):
        log("no further runnable runs (or harness error); scheduler exits")
        break
