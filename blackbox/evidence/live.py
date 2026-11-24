"""Live mode: the snapshot backend for incident context, with selected sources served live.

- commits / configs: GitHub REST (blackbox/connectors/github_api.py), read-only.
  Commit listings are capped at the investigation cutoff; config files are read at a commit
  SHA (the deployed commit from the deployment record, or the version the caller passes),
  never at a branch head. An unknown deployed version is reported, not guessed.
- logs: the application's actual JSONL output, read from the local files (blackbox/connectors/local_logs.py)
  or from a Grafana Loki server the logs were shipped to (blackbox/connectors/loki.py).
- everything else (incident, traces, metrics, deployments, builds, tickets): the snapshot.

Live responses can be recorded verbatim (blackbox/connectors/capture.py) so a live run is
replayable. Live mode is for demonstrations only: evaluation and ablations use frozen
snapshots, and the eval harness refuses live backends.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from blackbox.evidence.backend import load_backend
from blackbox.schemas import BackendResult, EvidenceRecord, SourceStatus

CONFIG_PATHS = {"checkout-svc": "config/checkout-svc.yaml", "payment-svc": "config/payment-svc.yaml"}
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")


def _strip_prefix(value: str) -> str:
    v = (value or "").strip()
    for p in ("gh:commit:", "commit:"):
        if v.startswith(p):
            v = v[len(p):]
    return v.split("@")[-1]


class LiveBackend:
    mode = "live"

    def __init__(self, base, github=None, local_logs=None):
        if base.ablation != "none":
            raise ValueError("live mode is for demonstrations; ablations use frozen snapshots only")
        self.base = base
        self.github = github
        self.logs = local_logs
        self.incident_id = base.incident_id
        self.ablation = base.ablation
        self._cutoff = base.incident().investigation_cutoff

    # ------------------------------------------------------------ context
    def incident(self):
        return self.base.incident()

    def source_status(self) -> list[SourceStatus]:
        out = []
        for s in self.base.source_status():
            if self.github and s.source in ("commits", "configs"):
                s = SourceStatus(source=s.source, available=True, healthy=True, fixture=False,
                                 detail=f"live GitHub API ({self.github.repo}), capped at the investigation cutoff")
            elif self.logs and s.source == "logs":
                s = SourceStatus(source="logs", available=True, healthy=True, fixture=False,
                                 detail=getattr(self.logs, "detail", "live read of the application's JSONL log files"))
            out.append(s)
        return out

    def deployed_commit(self, service: str) -> Optional[str]:
        """Commit of the latest deployment of `service` before the cutoff, from the deployment record."""
        res = self.base.list_deployments(service=service)
        for rec in reversed(res.records or []):
            sha = rec.attributes.get("commit") if isinstance(rec.attributes, dict) else None
            if not sha:
                m = re.search(r"commit[\"'=: ]+([0-9a-f]{7,40})", rec.content or "")
                sha = m.group(1) if m else None
            if sha:
                return sha
        return None

    # ------------------------------------------------------------ logs (live local files)
    def search_logs(self, service=None, start=None, end=None, query=None, level=None, limit=20) -> BackendResult:
        if self.logs is None:
            return self.base.search_logs(service=service, start=start, end=end, query=query, level=level, limit=limit)
        return self.logs.search(service=service, start=start, end=end or self._cutoff, query=query,
                                level=level, limit=limit)

    def export_logs(self, start=None, end=None) -> list[EvidenceRecord]:
        if self.logs is None:
            return self.base.export_logs(start, end)
        return self.logs.export(start=start, end=end or self._cutoff)

    # ------------------------------------------------------------ commits / configs (live GitHub)
    def search_commits(self, service=None, path=None, query=None, since=None, until=None, limit=10) -> BackendResult:
        if self.github is None:
            return self.base.search_commits(service=service, path=path, query=query, since=since,
                                            until=until, limit=limit)
        until = min(until, self._cutoff) if until else self._cutoff   # never read past the cutoff
        # history of what was deployed, not of the default branch: walk back from the alerting
        # service's deployed commit when the deployment record names one
        head = self.deployed_commit(service or self.base.incident().alert.service)
        res = self.github.list_commits(since=since, until=until, path=path, sha=head,
                                       limit=max(limit, 20) if query else limit)
        if res.status != "ok" and head:      # deployed commit not published: say so, then fall back
            fallback = self.github.list_commits(since=since, until=until, path=path,
                                                limit=max(limit, 20) if query else limit)
            note = f"deployed commit {head} is not on GitHub ({res.error}); listing the default branch instead"
            res = BackendResult(status=fallback.status, records=fallback.records, total_matches=fallback.total_matches,
                                truncated=fallback.truncated, error=note)
        if query and res.records:
            words = [w for w in re.split(r"\W+", query.lower()) if len(w) > 2]
            hits = [r for r in res.records if any(re.search(rf"\b{re.escape(w)}", r.content.lower()) for w in words)]
            res = BackendResult(status="ok" if hits else "empty", records=hits[:limit],
                                total_matches=len(hits), truncated=len(hits) > limit)
        return res

    def get_commit(self, sha: str) -> BackendResult:
        if self.github is None:
            return self.base.get_commit(sha)
        return self.github.get_commit(_strip_prefix(sha))

    def get_config(self, service: str, version=None) -> BackendResult:
        if self.github is None:
            return self.base.get_config(service, version)
        path = CONFIG_PATHS.get(service)
        if path is None:
            return BackendResult(status="unavailable", error=f"no known config file for service '{service}'")
        ref = _strip_prefix(version) if version and _SHA.fullmatch(_strip_prefix(version)) else None
        if ref is None:
            ref = self.deployed_commit(service)
        if ref is None:
            return BackendResult(status="unavailable",
                                 error=f"deployed version of {service} is unknown; pass a commit sha to read its config")
        return self.github.get_file(path, ref)

    # ------------------------------------------------------------ CI (live GitHub Actions, read-only)
    def _before_cutoff(self, res: BackendResult) -> BackendResult:
        keep = [r for r in res.records if not r.available_time or r.available_time <= self._cutoff]
        if res.status != "ok" or len(keep) == len(res.records):
            return res
        return BackendResult(status="ok" if keep else "empty", records=keep, total_matches=len(keep),
                             truncated=res.truncated, error="CI records after the investigation cutoff were removed")

    def list_workflow_runs(self, limit=5, status=None) -> BackendResult:
        if self.github is None:
            return BackendResult(status="unavailable", error="GitHub Actions is not configured")
        return self._before_cutoff(self.github.list_workflow_runs(limit=limit, status=status))

    def get_workflow_run(self, run_id) -> BackendResult:
        if self.github is None:
            return BackendResult(status="unavailable", error="GitHub Actions is not configured")
        return self._before_cutoff(self.github.get_run(run_id))

    def get_job_log(self, job_id, failed_only=True) -> BackendResult:
        if self.github is None:
            return BackendResult(status="unavailable", error="GitHub Actions is not configured")
        return self._before_cutoff(self.github.get_job_log(job_id, failed_only=failed_only))

    def list_run_artifacts(self, run_id) -> BackendResult:
        if self.github is None:
            return BackendResult(status="unavailable", error="GitHub Actions is not configured")
        return self._before_cutoff(self.github.list_run_artifacts(run_id))

    # ------------------------------------------------------------ everything else: snapshot
    def __getattr__(self, name):
        if name in ("search_traces", "get_trace", "get_metrics", "list_deployments", "get_build",
                    "search_tickets", "get_ticket"):
            return getattr(self.base, name)
        raise AttributeError(name)


def load_live_backend(incident_id: str, repo: str, telemetry_dir: Optional[Path] = None,
                      capture_dir: Optional[Path] = None, replay: bool = False,
                      snapshot_root: Optional[Path] = None, loki_url: Optional[str] = None,
                      loki_incident: Optional[str] = None) -> LiveBackend:
    from blackbox.connectors.github_api import GitHubConnector
    session = None
    if capture_dir is not None:
        from blackbox.connectors import capture
        session = (capture.ReplaySession(capture_dir) if replay
                   else capture.RecordingSession(capture_dir, repo=repo))
    github = GitHubConnector(repo, session=session) if session is not None else GitHubConnector(repo)
    logs = None
    if loki_url is not None:
        from blackbox.connectors.loki import LokiLogs
        logs = LokiLogs(loki_url, incident=loki_incident)
    elif telemetry_dir is not None:
        from blackbox.connectors.local_logs import LocalLogs
        logs = LocalLogs(telemetry_dir)
    return LiveBackend(load_backend(incident_id, snapshot_root=snapshot_root), github=github, local_logs=logs)
