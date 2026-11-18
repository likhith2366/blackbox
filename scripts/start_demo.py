"""Start the BlackBox demo stack with one command:  python scripts/start_demo.py

Starts (each only if its port is not already listening), logging to runs/_services/<name>.log:
  BlackBox API        http://127.0.0.1:8700   (served from the blackbox-live worktree if present)
  BlackBox UI         http://127.0.0.1:5173
  Demo control API    http://127.0.0.1:8710   (local only; owns fault inject/reset)
  Demo shop UI        http://127.0.0.1:5174
The demo services themselves (checkout 18101, payment 18102) are started through the control API /
demo tooling, not here. No secrets are read or stored: GitHub access comes from GITHUB_TOKEN or the
`gh` CLI at request time. Ollama must already be running (http://127.0.0.1:11434).
Stop everything with:  python scripts/start_demo.py --stop
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT.parent / "blackbox-live"
LOGS = ROOT / "runs" / "_services"
NPM = "npm.cmd" if os.name == "nt" else "npm"

SERVICES = [
    ("blackbox-api", 8700, [sys.executable, "-m", "blackbox.api"], LIVE if (LIVE / "blackbox" / "api").exists() else ROOT,
     {"BLACKBOX_ROOT": str(ROOT)}),
    ("blackbox-ui", 5173, [NPM, "run", "dev"], ROOT / "ui" / "blackbox-web", {}),
    ("demo-control-api", 8710, [sys.executable, "-m", "demo.control_api"], ROOT, {}),
    ("demo-shop-ui", 5174, [NPM, "run", "dev"], ROOT / "demo" / "web", {}),
]


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def stop() -> None:
    ports = {p for _, p, *_ in SERVICES}
    for c in psutil.net_connections("tcp"):
        if c.status == "LISTEN" and c.laddr.port in ports and c.pid:
            psutil.Process(c.pid).terminate()
            print(f"stopped pid {c.pid} on :{c.laddr.port}")


def main() -> int:
    if "--stop" in sys.argv:
        stop()
        return 0
    LOGS.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=3)
        print("ollama          : running")
    except Exception:
        print("ollama          : NOT reachable on :11434 (start Ollama; investigations will fail until it is up)")
    for name, port, cmd, cwd, env in SERVICES:
        if listening(port):
            print(f"{name:16s}: already running on :{port}")
            continue
        if not cwd.exists():
            print(f"{name:16s}: missing directory {cwd}, skipped")
            continue
        log = open(LOGS / f"{name}.log", "a", encoding="utf-8")
        subprocess.Popen(cmd, cwd=cwd, env={**os.environ, **env}, stdout=log, stderr=subprocess.STDOUT,
                         creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        print(f"{name:16s}: starting on :{port} (log runs/_services/{name}.log)")
    deadline = time.time() + 60
    pending = [s for s in SERVICES if not listening(s[1])]
    while pending and time.time() < deadline:
        time.sleep(2)
        pending = [s for s in pending if not listening(s[1])]
    for name, port, *_ in SERVICES:
        print(f"  {'UP  ' if listening(port) else 'DOWN'} {name:16s} http://127.0.0.1:{port}")
    return 0 if not pending else 1


if __name__ == "__main__":
    sys.exit(main())
