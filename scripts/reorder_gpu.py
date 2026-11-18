"""One-off GPU reordering (2026-11-20), using the harness's own resume behaviour.

1. wait until the eval_v1 harness logs the end of a given run (the s1 trio's last run);
2. stop the harness at that boundary; a run that had just started is left "running" and the
   harness moves it to _interrupted/ and re-runs it on resume (nothing is lost or replaced);
3. wait for the BlackBox API job to take the GPU lock and finish;
4. relaunch the identical harness command, which resumes the batch with the same frozen config,
   code revision and run ids.
Every step is appended to runs/eval_v1/_reorder.log.
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
BATCH = ROOT / "runs" / "eval_v1"
LOG = BATCH / "_reorder.log"
UNTIL_RUN = "INC-3172_b0_none_r0"
JOB_ID = sys.argv[1]
HARNESS_ARGS = ["-u", "-m", "eval.harness", "run", "--phase", "eval", "--matrix", "eval/matrix.yaml",
                "--batch", "eval_v1", "--cases", "INC-3172,INC-5408,INC-2691,INC-7735"]


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def harness_pids():
    return [p for p in psutil.process_iter(["pid", "cmdline"])
            if p.info["cmdline"] and "eval.harness" in " ".join(p.info["cmdline"])
            and "--batch" in p.info["cmdline"] and "eval_v1" in p.info["cmdline"]]


def ended(run_id):
    for line in (BATCH / "harness_log.jsonl").read_text(encoding="utf-8").splitlines():
        ev = json.loads(line)
        if ev.get("event") == "end" and ev.get("run_id") == run_id:
            return ev
    return None


def job():
    with urllib.request.urlopen(f"http://127.0.0.1:8700/api/jobs/{JOB_ID}", timeout=10) as r:
        return json.loads(r.read())


log(f"waiting for end of {UNTIL_RUN}")
while not ended(UNTIL_RUN):
    time.sleep(1)
log(f"{UNTIL_RUN} ended: {ended(UNTIL_RUN)}")
for p in harness_pids():
    log(f"stopping harness pid {p.pid} at the run boundary")
    p.terminate()
psutil.wait_procs(harness_pids(), timeout=30)
log("harness stopped; GPU lock is now stale and free for the API job")

while job()["status"] not in ("completed", "failed"):
    time.sleep(10)
log(f"API job {JOB_ID} finished: {job()['status']} run_id={job().get('run_id')}")

log("resuming eval_v1 with the identical command")
with open(ROOT / "runs" / "eval_v1_harness_resumed.out", "w", encoding="utf-8") as out:
    rc = subprocess.call([sys.executable, *HARNESS_ARGS], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
log(f"resumed harness exited rc={rc}")
