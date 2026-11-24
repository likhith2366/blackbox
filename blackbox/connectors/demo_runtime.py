"""Read-only connector over the demo platform's own deployment and build records (what is actually running).

The demo's deploy tool appends one JSON line per deployment to demo/.runtime/deployments.jsonl and writes one
manifest per build to demo/.runtime/builds/<build_id>.json. These are the deployment system's records, the
live equivalent of a CD system's API; this module only reads them. What is running is taken from these
records, never inferred from the latest commit on GitHub.

Directory: constructor argument, env BLACKBOX_DEMO_RUNTIME, default <repo>/demo/.runtime.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from blackbox.schemas import BackendResult, EvidenceRecord

REPO = Path(__file__).resolve().parents[2]


def _ts(s) -> Optional[float]:
    if not s:
        return None
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


class DemoRuntime:
    backend = "demo-deploy-records"

    def __init__(self, runtime_dir: Optional[Path] = None):
        self.dir = Path(runtime_dir or os.environ.get("BLACKBOX_DEMO_RUNTIME") or REPO / "demo" / ".runtime")
        self.deploy_file = self.dir / "deployments.jsonl"
        self.builds_dir = self.dir / "builds"
        self.detail = f"live deployment records ({self.deploy_file})"

    def ready(self) -> tuple[bool, str]:
        if not self.deploy_file.is_file():
            return False, f"no deployment record file at {self.deploy_file}"
        return True, "ok"

    def _deployments(self, cutoff: Optional[str]) -> list[dict]:
        cut = _ts(cutoff)
        out = []
        for line in self.deploy_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if cut is not None and (_ts(d.get("finished_at")) or 0) > cut:
                continue          # finished after the cutoff: not visible yet
            out.append(d)
        out.sort(key=lambda d: (d["started_at"], d["deploy_id"]))
        return out

    def _record(self, d: dict) -> EvidenceRecord:
        content = (f"{d['started_at']} deployment {d['deploy_id']} of {d['service']} version {d['version']} to "
                   f"{d.get('environment', '')} build={d['build_id']} commit={d['commit'][:7]} "
                   f"artifact_digest={d.get('artifact_digest', '')} by {d.get('actor', '')} ({d.get('trigger', '')}) "
                   f"status={d.get('status', '')}")
        return EvidenceRecord(
            evidence_id=f"deploy:{d['deploy_id']}", source="deployments", service=d["service"],
            event_time=d["started_at"], available_time=d.get("finished_at"), version=d["version"],
            title=f"deployment of {d['service']} {d['version']}", content=content,
            content_ref=f"{self.deploy_file}#{d['deploy_id']}", fixture=False,
            attributes={"deploy_id": d["deploy_id"], "build_id": d["build_id"], "commit": d["commit"][:7],
                        "commit_full": d["commit"], "artifact_digest": d.get("artifact_digest"),
                        "backend": self.backend, "mode": "live", "query": "read deployments.jsonl",
                        "source_url": str(self.deploy_file)})

    def list_deployments(self, service=None, start=None, end=None, cutoff=None) -> BackendResult:
        ok, why = self.ready()
        if not ok:
            return BackendResult(status="unavailable", error=why)
        s, e = _ts(start), _ts(end)
        rows = [d for d in self._deployments(cutoff) if (not service or d["service"] == service)
                and (s is None or _ts(d["started_at"]) >= s) and (e is None or _ts(d["started_at"]) <= e)]
        if not rows:
            return BackendResult(status="empty", total_matches=0)
        return BackendResult(status="ok", records=[self._record(d) for d in rows[-20:]], total_matches=len(rows),
                             truncated=len(rows) > 20)

    def deployed(self, service: str, cutoff: Optional[str]) -> Optional[dict]:
        """The latest succeeded deployment of `service` that finished before the cutoff."""
        if not self.ready()[0]:
            return None
        rows = [d for d in self._deployments(cutoff) if d["service"] == service and d.get("status") == "succeeded"]
        return rows[-1] if rows else None

    def get_build(self, build_id: str, cutoff=None) -> BackendResult:
        bid = str(build_id).split(":", 1)[-1].strip()
        p = self.builds_dir / f"{bid}.json"
        if not p.is_file():
            return BackendResult(status="empty", total_matches=0, error=f"build {bid} not found")
        b = json.loads(p.read_text(encoding="utf-8"))
        if cutoff and (_ts(b.get("finished_at")) or 0) > _ts(cutoff):
            return BackendResult(status="empty", total_matches=0, error=f"build {bid} finished after the cutoff")
        text = (f"build {b['build_id']} service={b['service']} version={b['version']} commit={b['commit']} "
                f"artifact={b['artifact']} artifact_digest={b['artifact_digest']} status={b['status']} "
                f"pipeline={b['pipeline']} started={b['started_at']} finished={b['finished_at']} "
                f"source_paths={','.join(b.get('source_paths', []))}")
        return BackendResult(status="ok", total_matches=1, records=[EvidenceRecord(
            evidence_id=f"build:{b['build_id']}", source="builds", service=b["service"], event_time=b["started_at"],
            available_time=b["finished_at"], version=b["version"], title=f"build {b['build_id']} of {b['service']} {b['version']}",
            content=text, content_ref=str(p), fixture=False,
            attributes={"commit": b["commit"][:7], "artifact_digest": b["artifact_digest"], "backend": self.backend,
                        "mode": "live", "query": f"read builds/{bid}.json", "source_url": str(p)})])
