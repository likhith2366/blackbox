"""Seeded load generator for checkout-svc.

    python -m demo.loadgen --duration 30 --rps 5 --seed 7

The order stream (arrival gaps, customers, amounts, notes) is a pure function of the
seed; only the services' responses vary between runs.
"""
from __future__ import annotations

import argparse
import json
import random
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from demo.paths import PORTS
from demo.timeutil import iso

ITEMS = ["sku-1001", "sku-1002", "sku-2040", "sku-3310", "sku-4420", "sku-5005", "sku-7781"]
NOTES = ["leave at front desk", "gift wrap please", "ring the bell twice", "deliver after 6pm",
         "no plastic bags", "call on arrival", "please include receipt"]


def make_order(rnd: random.Random, n: int, seed: int) -> dict:
    r = rnd.random()
    if r < 0.04:
        amount = round(rnd.uniform(1.0, 4.99), 2)
    elif r < 0.14:
        amount = round(rnd.uniform(301.0, 900.0), 2)
    else:
        amount = round(rnd.uniform(10.0, 299.0), 2)
    order = {"order_id": f"ord-{seed:02d}{n:05d}", "customer_id": rnd.randint(1, 20000),
             "item": rnd.choice(ITEMS), "qty": rnd.randint(1, 3), "amount": amount, "currency": "USD"}
    if rnd.random() < 0.12:
        order["note"] = rnd.choice(NOTES)
    return order


class LoadGenerator:
    """schedule: list of (start_offset_s, rps); special: {order_index: extra fields}."""

    def __init__(self, schedule, seed: int, out_path: Path | None = None,
                 special: dict[int, dict] | None = None, workers: int = 64):
        self.schedule = sorted(schedule)
        self.seed = seed
        self.special = special or {}
        self.out_path = out_path
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.stats = {"sent": 0, "ok": 0, "failed": 0}
        self.thread = None

    def _rps_at(self, t: float) -> float:
        rps = 0.0
        for start, r in self.schedule:
            if t >= start:
                rps = r
        return rps

    def _send(self, order: dict):
        url = f"http://127.0.0.1:{PORTS['checkout-svc']}/orders"
        t0 = time.time()
        status = 0
        try:
            req = urllib.request.Request(url, data=json.dumps(order).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        except Exception:
            status = -1
        rec = {"ts": iso(t0), "order_id": order["order_id"], "status": status,
               "latency_ms": round((time.time() - t0) * 1000, 1)}
        with self._lock:
            self.stats["ok" if 200 <= status < 300 else "failed"] += 1
            if self.out_path:
                with open(self.out_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec) + "\n")

    def _run(self, duration: float):
        rnd = random.Random(self.seed)
        start = time.time()
        next_t = 0.0
        n = 0
        while not self._stop.is_set():
            rps = self._rps_at(next_t)
            gap = rnd.expovariate(rps) if rps > 0 else 0.5
            next_t += gap
            if next_t >= duration:
                break
            order = make_order(rnd, n, self.seed)
            order.update(self.special.get(n, {}))
            n += 1
            delay = start + next_t - time.time()
            if delay > 0 and self._stop.wait(delay):
                break
            if rps <= 0:
                continue
            with self._lock:
                self.stats["sent"] += 1
            self.pool.submit(self._send, order)

    def start(self, duration: float):
        self.thread = threading.Thread(target=self._run, args=(duration,), daemon=True)
        self.thread.start()

    def stop(self, wait: bool = True):
        self._stop.set()
        if self.thread:
            self.thread.join()
        self.pool.shutdown(wait=wait)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=30)
    ap.add_argument("--rps", type=float, default=5)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    lg = LoadGenerator([(0, args.rps)], args.seed)
    lg.start(args.duration)
    lg.thread.join()
    lg.stop()
    print(json.dumps(lg.stats))


if __name__ == "__main__":
    main()
