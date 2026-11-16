"""Cross-process GPU lock (CONTRACT section 12): runs/_gpu.lock = {pid, owner, started}.

Created with exclusive create, removed by its owner. A lock whose pid is no longer alive is
stale and may be removed. Every inference runner acquires it, so a dashboard job never
competes with an experiment or an evaluation batch.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

import psutil

LOCK = Path(os.environ.get("BLACKBOX_ROOT") or Path(__file__).resolve().parents[2]) / "runs" / "_gpu.lock"


def holder() -> Optional[dict]:
    """The current lock holder, or None. Removes a stale lock left by a dead process."""
    try:
        data = json.loads(LOCK.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return {"pid": None, "owner": "unreadable lock file", "started": None}
    pid = data.get("pid")
    if pid and not psutil.pid_exists(pid):
        try:
            LOCK.unlink()
        except FileNotFoundError:
            pass
        return None
    return data


def try_acquire(owner: str) -> bool:
    if holder() is not None:
        return False
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(LOCK, "x", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "owner": owner,
                       "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, fh)
        return True
    except FileExistsError:
        return False


def release() -> None:
    data = holder()
    if data and data.get("pid") == os.getpid():
        try:
            LOCK.unlink()
        except FileNotFoundError:
            pass
