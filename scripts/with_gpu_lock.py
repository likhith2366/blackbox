"""Run a command while holding the cross-process GPU lock (CONTRACT section 12).

python scripts/with_gpu_lock.py "<owner label>" -- <command ...>
Waits for the lock, runs the command, always releases. Uses the same lock file as the API.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

LOCK = Path(__file__).resolve().parents[1] / "runs" / "_gpu.lock"


def holder():
    try:
        data = json.loads(LOCK.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if data.get("pid") and not psutil.pid_exists(data["pid"]):
        LOCK.unlink(missing_ok=True)
        return None
    return data


def main() -> int:
    owner, rest = sys.argv[1], sys.argv[sys.argv.index("--") + 1:]
    while True:
        if holder() is None:
            try:
                with open(LOCK, "x", encoding="utf-8") as fh:
                    json.dump({"pid": os.getpid(), "owner": owner,
                               "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, fh)
                break
            except FileExistsError:
                pass
        print(f"waiting for GPU lock held by: {(holder() or {}).get('owner')}", flush=True)
        time.sleep(15)
    try:
        return subprocess.call(rest)
    finally:
        h = holder()
        if h and h.get("pid") == os.getpid():
            LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
