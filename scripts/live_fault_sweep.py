"""Development sweep: inject each demo fault into the RUNNING demo and let BlackBox investigate it live.

    python scripts/live_fault_sweep.py [--faults retry-policy,index-migration,...] [--out runs/live_faults]

Per fault (sequential, one GPU job at a time):
  1. reset to the healthy baseline (telemetry -> demo/.runtime/telemetry/stack, read by Alloy and the bridge);
  2. wait for healthy traffic, inject the fault through demo.controller (real commit/build/deploy or operator action);
  3. wait for a live alert in Prometheus: checkout-svc error_rate > 0.1 for 15s, or latency_p95_ms > 1500 for 15s;
  4. wait for GPU < 80 C, take the GPU lock, run one BlackBox live-stack investigation (frozen_v1 settings);
  5. save output.json, the incident, source status, and the ground truth (truth.json, never shown to the model).
Traffic must be running (python -m demo.loadgen ...). Results are development-only and are not edited.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

ALERTS = [("checkout-svc", "error_rate", ">", 0.10, 15), ("checkout-svc", "latency_p95_ms", ">", 1500.0, 15)]
# --alerts extended also watches common latency SLOs of both services (lower thresholds, held for 30 s)
EXTENDED_ALERTS = ALERTS + [("payment-svc", "latency_p95_ms", ">", 1000.0, 30), ("checkout-svc", "latency_p95_ms", ">", 1000.0, 30),
                            ("payment-svc", "error_rate", ">", 0.10, 15)]
FAULTS = ["retry-policy", "index-migration", "endpoint-port", "payment-stop", "pool-override", "pool-fastpath"]


def log(msg):
    print(f"{datetime.now(timezone.utc):%H:%M:%S} {msg}", flush=True)


def gpu_temp() -> int:
    try:
        return int(subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                                  capture_output=True, text=True, timeout=15).stdout.strip())
    except Exception:
        return 0


def traffic_rps() -> float:
    """Mean checkout-svc requests_per_s over the last minute, from Prometheus (0 if unknown)."""
    import urllib.parse
    import urllib.request
    q = urllib.parse.urlencode({"query": 'avg_over_time(requests_per_s{job="checkout-svc"}[1m])'})
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:9090/api/v1/query?{q}", timeout=10) as r:
            res = json.loads(r.read())["data"]["result"]
        return float(res[0]["value"][1]) if res else 0.0
    except Exception:
        return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--faults", default=",".join(FAULTS))
    ap.add_argument("--out", default=str(ROOT / "runs" / "live_faults"))
    ap.add_argument("--settle", type=int, default=330, help="seconds of healthy traffic before injecting (longer than the 5-min incident lookback, so the previous fault is outside the window)")
    ap.add_argument("--alerts", choices=["standard", "extended"], default="standard")
    ap.add_argument("--config", default="configs/frozen_v1.yaml", help="investigator settings (dev_v2.yaml adds the change check)")
    a = ap.parse_args()
    import yaml
    from demo import controller
    from demo.lock import demo_lock
    from demo.paths import RUNTIME
    from blackbox.evidence.live_incident import open_incident
    from blackbox.evidence.live_stack import load_live_stack
    from blackbox.schemas import RunConfig
    from blackbox.systems import run_system
    from blackbox.api import gpu_lock
    cfg = RunConfig(**yaml.safe_load((ROOT / a.config).read_text(encoding="utf-8")))
    out_root = Path(a.out)
    for fault in [f.strip() for f in a.faults.split(",") if f.strip()]:
        d = out_root / fault
        if (d / "output.json").exists():
            log(f"{fault}: done already, skipping")
            continue
        d.mkdir(parents=True, exist_ok=True)
        log(f"{fault}: reset to baseline")
        with demo_lock(f"live sweep reset {fault}"):
            controller.baseline(str(RUNTIME / "telemetry" / "stack"))
        time.sleep(a.settle)
        for _ in range(12):                  # traffic must be flowing, or no alert can fire
            rps = traffic_rps()
            if rps >= 1.5:
                break
            log(f"{fault}: waiting for traffic (checkout requests_per_s={rps:.2f}); is demo.loadgen running?")
            time.sleep(15)
        else:
            log(f"{fault}: no healthy traffic; skipped (recorded as 'no traffic')")
            (d / "output.json").write_text(json.dumps({"status": "no_traffic", "fault": fault}), encoding="utf-8")
            continue
        log(f"{fault}: inject (traffic {rps:.1f} req/s)")
        with demo_lock(f"live sweep inject {fault}"):
            ev = controller.apply(fault)
        (d / "truth.json").write_text(json.dumps(ev, indent=2), encoding="utf-8")
        inc, deadline = None, time.time() + 240
        while inc is None and time.time() < deadline:
            time.sleep(15)
            for svc, metric, op, thr, for_s in (EXTENDED_ALERTS if a.alerts == "extended" else ALERTS):
                try:
                    inc = open_incident(svc, metric, op, thr, for_s, since=ev["at"])
                    break
                except LookupError:
                    continue
        if inc is None:
            log(f"{fault}: no alert fired within 4 min; recorded as 'no alert'")
            (d / "output.json").write_text(json.dumps({"status": "no_alert", "fault": fault}), encoding="utf-8")
            continue
        log(f"{fault}: alert {inc.alert.condition} fired {inc.alert.fired_at} ({inc.incident_id})")
        while gpu_temp() >= 80:
            time.sleep(20)
        while not gpu_lock.try_acquire(f"live sweep {fault}"):
            time.sleep(10)
        try:
            # the cutoff is the start of the investigation: re-open so the window ends now
            parts = inc.alert.condition.split()
            inc = open_incident(inc.alert.service, inc.alert.signal, parts[1], float(parts[2]), int(parts[4].rstrip("s")),
                                since=ev["at"])
            backend = load_live_stack(inc)
            (d / "incident.json").write_text(inc.model_dump_json(indent=2), encoding="utf-8")
            (d / "sources.json").write_text(json.dumps([s.model_dump() for s in backend.source_status()], indent=2),
                                            encoding="utf-8")
            t0 = time.time()
            out = run_system("blackbox", backend, cfg)
            (d / "output.json").write_text(out.model_dump_json(indent=2), encoding="utf-8")
            rep = out.report
            log(f"{fault}: {out.status} in {time.time() - t0:.0f}s -> {rep.status if rep else 'no report'}: "
                f"{rep.headline if rep else ''}")
        finally:
            gpu_lock.release()
    log("sweep finished; resetting to baseline")
    with demo_lock("live sweep final reset"):
        controller.baseline(str(RUNTIME / "telemetry" / "stack"))


if __name__ == "__main__":
    main()
