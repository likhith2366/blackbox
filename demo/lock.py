"""Exclusive demo lock: one actor (a scenario capture or a UI control action) at a time.

demo/.runtime/demo.lock = {"pid", "owner", "started"} created with O_EXCL. A lock whose pid
is no longer alive is stale and is removed by the next acquirer.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager

from demo.paths import RUNTIME

LOCK = RUNTIME / "demo.lock"


class DemoLocked(RuntimeError):
    pass


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import subprocess
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def holder() -> dict | None:
    try:
        info = json.loads(LOCK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not _alive(int(info.get("pid", -1))):
        try:
            LOCK.unlink()
        except OSError:
            pass
        return None
    return info


def ui_traffic_active() -> bool:
    """UI traffic generation (demo/control_api.py) marks itself in ui_traffic.json while it runs."""
    try:
        m = json.loads((RUNTIME / "ui_traffic.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return time.time() < float(m.get("until", 0)) and _alive(int(m.get("pid", -1)))


def acquire(owner: str) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if owner.startswith("scenario capture") and ui_traffic_active():
        raise DemoLocked("demo is busy: UI traffic generation is running")
    for _ in range(2):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            h = holder()
            if h is not None:
                raise DemoLocked(f"demo is locked by {h.get('owner')} (pid {h.get('pid')}) since {h.get('started')}")
            continue  # stale lock was removed; retry once
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "owner": owner,
                       "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, fh)
        return
    raise DemoLocked("could not acquire demo lock")


def release() -> None:
    try:
        info = json.loads(LOCK.read_text(encoding="utf-8"))
        if int(info.get("pid", -1)) == os.getpid():
            LOCK.unlink()
    except (OSError, ValueError):
        pass


@contextmanager
def demo_lock(owner: str):
    acquire(owner)
    try:
        yield
    finally:
        release()
