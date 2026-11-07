"""Local build pipeline: package one service at one commit into an immutable artifact.

    python -m demo.buildtool build checkout-svc <commit>

Artifact = `git archive` tarball of the service's source paths at that commit (git sets
file mtimes to the commit time, so the tarball is reproducible). The build manifest
records build_id, service, version, commit, artifact path and sha256 digest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time

from demo.paths import ARTIFACTS, BUILDS, SOURCE_PATHS, VERSION_FILE, WORKSPACE, next_counter
from demo.workspace import git, show
from demo.timeutil import iso_now


def find_cached(service: str, commit: str) -> dict | None:
    if not BUILDS.exists():
        return None
    for p in sorted(BUILDS.glob("*.json")):
        m = json.loads(p.read_text(encoding="utf-8"))
        if m["service"] == service and m["commit"] == commit and m["status"] == "success":
            if (ARTIFACTS / m["artifact"]).exists():
                return m
    return None


def build(service: str, commit: str, triggered_by: str = "ci", reuse: bool = False) -> dict:
    commit = git("rev-parse", commit).strip()
    if reuse:
        cached = find_cached(service, commit)
        if cached:
            return cached
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    BUILDS.mkdir(parents=True, exist_ok=True)
    build_id = f"b-{next_counter('build', 1201)}"
    started = iso_now()
    version = show(commit, VERSION_FILE[service]).strip()
    tar = subprocess.run(["git", "archive", "--format=tar", commit, *SOURCE_PATHS[service]],
                         cwd=str(WORKSPACE), capture_output=True, check=True).stdout
    digest = "sha256:" + hashlib.sha256(tar).hexdigest()
    name = f"{service}-{version}-{build_id}.tar"
    (ARTIFACTS / name).write_bytes(tar)
    time.sleep(0.05)
    manifest = {
        "build_id": build_id, "service": service, "version": version, "commit": commit,
        "artifact": name, "artifact_digest": digest, "artifact_bytes": len(tar),
        "source_paths": SOURCE_PATHS[service], "started_at": started,
        "finished_at": iso_now(), "status": "success", "pipeline": "local-build",
        "triggered_by": triggered_by,
    }
    (BUILDS / f"{build_id}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def get_build(build_id: str) -> dict:
    return json.loads((BUILDS / f"{build_id}.json").read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("service")
    b.add_argument("commit", nargs="?", default="HEAD")
    args = ap.parse_args()
    print(json.dumps(build(args.service, args.commit), indent=2))


if __name__ == "__main__":
    main()
