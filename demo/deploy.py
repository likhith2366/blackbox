"""Deploy a built artifact as a local process and record the deployment.

    python -m demo.deploy <service> <build_id>
    python -m demo.deploy --stop <service>

A deployment extracts the artifact into its own directory, writes BUILD_INFO.json
(release metadata from the build manifest), gracefully stops the running instance,
starts the new one and appends a record to deployments.jsonl. Environment overrides
passed by an operator are applied to the process but, like in many real setups, are
not part of the deployment record.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.request

from demo.buildtool import get_build
from demo.paths import (ARTIFACTS, DEPLOY_DIR, DEPLOYMENTS, METRIC_INTERVAL_S, MODULES, PORTS, PROC,
                        RUNTIME, WORK_DB, load_state, next_counter, save_state)
from demo.timeutil import iso_now


def _http(method: str, url: str, headers: dict | None = None, timeout: float = 2.0):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def health(service: str) -> dict | None:
    try:
        _, body = _http("GET", f"http://127.0.0.1:{PORTS[service]}/health", timeout=1.0)
        return json.loads(body)
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop(service: str, reason: str = "redeploy", timeout: float = 8.0) -> bool:
    """Graceful stop through the admin endpoint; force-kill as a fallback."""
    st = load_state()
    pid = st.get("pids", {}).get(service)
    was_running = health(service) is not None
    if was_running:
        try:
            _http("POST", f"http://127.0.0.1:{PORTS[service]}/admin/shutdown",
                  headers={"X-Shutdown-Reason": reason})
        except Exception:
            pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        if (pid is None or not _pid_alive(pid)) and health(service) is None:
            break
        time.sleep(0.1)
    if pid and _pid_alive(pid):
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, 9)
    st = load_state()
    st.get("pids", {}).pop(service, None)
    save_state(st)
    return was_running


def _extract(service: str, manifest: dict) -> str:
    n = next_counter("deploy_dir", 1)
    dest = DEPLOY_DIR / service / f"{manifest['build_id']}-{n}"
    dest.mkdir(parents=True, exist_ok=True)
    data = (ARTIFACTS / manifest["artifact"]).read_bytes()
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(dest)
    info = {k: manifest[k] for k in ("build_id", "service", "version", "commit", "artifact_digest")}
    (dest / "BUILD_INFO.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return str(dest)


def start(service: str, workdir: str, env_overrides: dict | None = None) -> int:
    st = load_state()
    telemetry_dir = st.get("telemetry_dir") or str(RUNTIME / "telemetry" / "live")
    os.makedirs(telemetry_dir, exist_ok=True)
    PROC.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"TELEMETRY_DIR": telemetry_dir, "PYTHONPATH": workdir,
                "PAYMENT_DB_PATH": str(WORK_DB), "METRIC_INTERVAL_S": str(METRIC_INTERVAL_S),
                "PYTHONDONTWRITEBYTECODE": "1"})
    env.pop("PAYMENT_DB_POOL_SIZE", None)
    env.update(env_overrides or {})
    out = open(PROC / f"{service}.out", "ab")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    p = subprocess.Popen([sys.executable, "-m", MODULES[service]], cwd=workdir, env=env,
                         stdout=out, stderr=subprocess.STDOUT, creationflags=flags)
    deadline = time.time() + 20
    while time.time() < deadline:
        if health(service):
            break
        if p.poll() is not None:
            raise RuntimeError(f"{service} exited during startup; see {PROC / (service + '.out')}")
        time.sleep(0.1)
    else:
        raise RuntimeError(f"{service} did not become healthy")
    st = load_state()
    st.setdefault("pids", {})[service] = p.pid
    save_state(st)
    return p.pid


def deploy(service: str, build_id: str, actor: str = "deploy-bot", trigger: str = "pipeline",
           env_overrides: dict | None = None) -> dict:
    manifest = get_build(build_id)
    assert manifest["service"] == service, "build is for a different service"
    workdir = _extract(service, manifest)
    stop(service, reason="redeploy")
    t = iso_now()
    start(service, workdir, env_overrides)
    rec = {
        "deploy_id": f"d-{next_counter('deploy', 501)}", "service": service,
        "version": manifest["version"], "build_id": build_id,
        "artifact_digest": manifest["artifact_digest"], "commit": manifest["commit"],
        "environment": "demo-local", "started_at": t, "finished_at": iso_now(),
        "actor": actor, "trigger": trigger, "status": "succeeded",
    }
    DEPLOYMENTS.parent.mkdir(parents=True, exist_ok=True)
    with open(DEPLOYMENTS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    st = load_state()
    st.setdefault("deployed", {})[service] = rec
    save_state(st)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("service")
    ap.add_argument("build_id", nargs="?")
    ap.add_argument("--stop", action="store_true")
    args = ap.parse_args()
    if args.stop:
        print(stop(args.service, reason="operator request"))
    else:
        print(json.dumps(deploy(args.service, args.build_id, actor="operator", trigger="manual"), indent=2))


if __name__ == "__main__":
    main()
