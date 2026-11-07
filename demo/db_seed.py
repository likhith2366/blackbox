"""Seed the payment database (deterministic, cached) and reset the working copy.

The historical payments table is generated from a fixed seed so the database is identical
on every machine. It is large enough (1.5M rows) that the velocity query depends on the
customer/time index, as it would in a real store.
"""
from __future__ import annotations

import os
import random
import shutil
import sqlite3
import calendar
import time

from demo.paths import BASE_DB, DATA, TEMPLATE, WORK_DB

ROWS = 1_500_000
CUSTOMERS = 20_000
SEED = 20261119


def _create_base():
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = BASE_DB.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(tmp)
    conn.execute("CREATE TABLE schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT)")
    for name in ("001_schema.sql", "002_customer_index.sql"):
        conn.executescript((TEMPLATE / "db" / "migrations" / name).read_text(encoding="utf-8"))
        conn.execute("INSERT INTO schema_migrations VALUES (?, '2026-09-15 10:30:00')", (name,))
    rnd = random.Random(SEED)
    base = calendar.timegm((2026, 6, 1, 0, 0, 0, 0, 0, 0))
    span = 170 * 86400

    def rows():
        for i in range(ROWS):
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(base + rnd.random() * span))
            yield (f"pay_h{i:07d}", f"h-{i:07d}", rnd.randint(1, CUSTOMERS),
                   round(rnd.lognormvariate(3.6, 0.9), 2), "USD", "approved", ts)

    conn.executemany("INSERT INTO payments (payment_id, order_id, customer_id, amount, currency, status, "
                     "created_at) VALUES (?,?,?,?,?,?,?)", rows())
    conn.commit()
    conn.close()
    tmp.replace(BASE_DB)


def ensure_base():
    if not BASE_DB.exists():
        _create_base()


def reset_working_db():
    ensure_base()
    for suffix in ("", "-wal", "-shm"):
        p = str(WORK_DB) + suffix
        if os.path.exists(p):
            os.remove(p)
    shutil.copyfile(BASE_DB, WORK_DB)


if __name__ == "__main__":
    t = time.time()
    reset_working_db()
    print(f"working db ready at {WORK_DB} ({time.time() - t:.1f}s)")
