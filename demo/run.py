"""Run the demo locally (minimal profile: local processes, telemetry to files).

    python -m demo.run --healthy [--duration 30]   # baseline deploy + seeded load, print health
    python -m demo.run --status
    python -m demo.run --stop
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter

from demo import controller, deploy
from demo.loadgen import LoadGenerator
from demo.paths import RUNTIME, SERVICES, load_state, save_state


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--healthy", action="store_true")
    g.add_argument("--status", action="store_true")
    g.add_argument("--stop", action="store_true")
    ap.add_argument("--duration", type=float, default=30)
    ap.add_argument("--rps", type=float, default=5)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    if args.stop:
        for s in reversed(SERVICES):
            print(s, "stopped" if deploy.stop(s, reason="operator request") else "was not running")
        return
    if args.status:
        print(json.dumps({s: deploy.health(s) for s in SERVICES}, indent=2))
        return
    live = RUNTIME / "telemetry" / f"live-{int(time.time())}"
    controller.baseline(str(live))
    out = live / "loadgen.jsonl"
    lg = LoadGenerator([(0, args.rps)], args.seed, out_path=out)
    lg.start(args.duration)
    lg.thread.join()
    lg.stop()
    codes = Counter(json.loads(l)["status"] for l in out.read_text().splitlines())
    print(json.dumps({"health": {s: deploy.health(s) for s in SERVICES},
                      "load": lg.stats, "status_codes": dict(codes),
                      "telemetry_dir": str(live)}, indent=2))
    print("services left running; stop with: python -m demo.run --stop")


if __name__ == "__main__":
    main()
