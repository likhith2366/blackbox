"""Live-stack backend: every tool is answered by the source system it names, or it says why it cannot be.

    logs      -> Grafana Loki            (blackbox/connectors/loki.py)
    metrics   -> Prometheus              (blackbox/connectors/prometheus.py)
    traces    -> Grafana Tempo           (blackbox/connectors/tempo.py)
    deploys   -> the demo platform's live deployment/build records (blackbox/connectors/demo_runtime.py)
    commits / diffs / config files -> GitHub API, pinned to the deployed commit SHA (github_api.py)
    CI        -> GitHub Actions API (github_api.py); Jenkins API when configured (jenkins.py)
    tickets   -> Jira API when configured (jira.py)

There is no fallback: this module never imports the snapshot backend and never reads scenarios/snapshots/ or
local JSONL files. A source that is not configured or not reachable answers `unavailable` with the reason;
a source that answers with nothing answers `empty`. Every record carries backend, query, mode and source URL.
The incident itself is built from a live Prometheus alert evaluation (live_incident.py).
"""
from __future__ import annotations

import re
from typing import Optional

from blackbox.schemas import BackendResult, EvidenceRecord, Incident, SourceStatus

_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
CONFIG_PATHS = {"checkout-svc": "config/checkout-svc.yaml", "payment-svc": "config/payment-svc.yaml"}
PART_CHARS = 550           # open_record splits a long original into parts that fit the model's record preview
MAX_PARTS = 8


def _strip(v: str) -> str:
    v = (v or "").strip()
    for p in ("gh:commit:", "commit:"):
        if v.startswith(p):
            v = v[len(p):]
    return v.split("@")[-1]


class LiveStackBackend:
    mode = "live"
    ablation = "none"

    def __init__(self, incident: Incident, *, loki=None, prometheus=None, tempo=None, deploys=None, github=None,
                 jira=None, jenkins=None):
        self._incident = incident
        self.incident_id = incident.incident_id
        self.cutoff = incident.investigation_cutoff
        self.loki, self.prom, self.tempo, self.deploys = loki, prometheus, tempo, deploys
        self.github, self.jira, self.jenkins = github, jira, jenkins
        self._full: dict[str, EvidenceRecord] = {}     # records whose preview was shortened, for open_record

    # ------------------------------------------------------------------ helpers
    def incident(self) -> Incident:
        return self._incident

    def _window(self, start, end):
        s = start or self._incident.window_start
        e = min(end, self.cutoff) if end else self.cutoff        # never past the cutoff
        return s, e

    @staticmethod
    def _missing(name: str) -> BackendResult:
        return BackendResult(status="unavailable", error=f"{name} is not configured in live mode")

    def _keep(self, res: BackendResult, backend: str) -> BackendResult:
        for r in res.records:
            r.attributes.setdefault("backend", backend)
            r.attributes.setdefault("mode", "live")
            if r.truncated:
                self._full[r.evidence_id] = r
        return res

    def _before_cutoff(self, res: BackendResult) -> BackendResult:
        keep = [r for r in res.records if not r.available_time or r.available_time <= self.cutoff]
        if res.status != "ok" or len(keep) == len(res.records):
            return res
        return BackendResult(status="ok" if keep else "empty", records=keep, total_matches=len(keep),
                             truncated=res.truncated, error="records after the investigation cutoff were removed")

    # ------------------------------------------------------------------ status
    def source_status(self) -> list[SourceStatus]:
        def st(source, conn, name):
            if conn is None:
                return SourceStatus(source=source, available=False, healthy=False, detail=f"{name}: not configured")
            ok, why = conn.ready()
            return SourceStatus(source=source, available=ok, healthy=ok, fixture=False,
                                detail=f"{conn.detail}" + ("" if ok else f" - {why}"))
        out = [st("logs", self.loki, "Loki"), st("metrics", self.prom, "Prometheus"), st("traces", self.tempo, "Tempo"),
               st("deployments", self.deploys, "deployment records")]
        out.append(SourceStatus(source="builds", available=self.deploys is not None, healthy=self.deploys is not None,
                                detail="build manifests of the demo platform" +
                                       (f"; Jenkins: {self.jenkins.detail}" if self.jenkins else "; Jenkins: not configured")))
        gh = self.github is not None
        for s in ("commits", "configs"):
            out.append(SourceStatus(source=s, available=gh, healthy=gh,
                                    detail=f"live GitHub API ({self.github.repo}), pinned to the deployed SHA" if gh
                                    else "GitHub not configured"))
        jira_ok = bool(self.jira and self.jira.configured)
        out.append(SourceStatus(source="tickets", available=jira_ok, healthy=jira_ok,
                                detail=self.jira.detail if self.jira else "Jira not configured"))
        return out

    # ------------------------------------------------------------------ logs / metrics / traces
    def search_logs(self, service=None, start=None, end=None, query=None, level=None, limit=20) -> BackendResult:
        if self.loki is None:
            return self._missing("Loki")
        s, e = self._window(start, end)
        return self._keep(self.loki.search(service=service, start=s, end=e, query=query, level=level, limit=limit), "loki")

    def export_logs(self, start=None, end=None) -> list[EvidenceRecord]:
        if self.loki is None:
            return []
        s, e = self._window(start, end)
        return self.loki.export(start=s, end=e)

    def get_metrics(self, service: str, metric=None, start=None, end=None) -> BackendResult:
        if self.prom is None:
            return self._missing("Prometheus")
        s, e = self._window(start, end)
        return self._keep(self.prom.get_metrics(service, metric=metric, start=s, end=e), "prometheus")

    def search_traces(self, service=None, start=None, end=None, status=None, min_duration_ms=None, limit=10) -> BackendResult:
        if self.tempo is None:
            return self._missing("Tempo")
        s, e = self._window(start, end)
        return self._keep(self.tempo.search_traces(service=service, start=s, end=e, status=status,
                                                   min_duration_ms=min_duration_ms, limit=limit), "tempo")

    def get_trace(self, trace_id: str) -> BackendResult:
        if self.tempo is None:
            return self._missing("Tempo")
        return self._keep(self.tempo.get_trace(trace_id, cutoff=self.cutoff), "tempo")

    # ------------------------------------------------------------------ deployments / builds
    def list_deployments(self, service=None, start=None, end=None) -> BackendResult:
        if self.deploys is None:
            return self._missing("deployment records")
        return self.deploys.list_deployments(service=service, start=start, end=end, cutoff=self.cutoff)

    def get_build(self, build_id: str) -> BackendResult:
        if self.deploys is None:
            return self._missing("build records")
        return self.deploys.get_build(build_id, cutoff=self.cutoff)

    def deployed_commit(self, service: str) -> Optional[str]:
        d = self.deploys.deployed(service, self.cutoff) if self.deploys else None
        return d["commit"] if d else None

    # ------------------------------------------------------------------ code (GitHub, pinned)
    def search_commits(self, service=None, path=None, query=None, since=None, until=None, limit=10) -> BackendResult:
        if self.github is None:
            return self._missing("GitHub")
        until = min(until, self.cutoff) if until else self.cutoff
        svc = service or self._incident.alert.service
        head = self.deployed_commit(svc)
        if head is None:
            return BackendResult(status="unavailable", error=f"no deployment record for {svc}: deployed commit unknown")
        res = self.github.list_commits(since=since, until=until, path=path, sha=head,
                                       limit=max(limit, 20) if query else limit)
        if res.status not in ("ok", "empty"):
            return BackendResult(status=res.status, error=f"history of deployed commit {head[:7]} not readable on GitHub: {res.error}")
        if query and res.records:
            words = [w for w in re.split(r"\W+", query.lower()) if len(w) > 2]
            hits = [r for r in res.records if any(re.search(rf"\b{re.escape(w)}", r.content.lower()) for w in words)]
            res = BackendResult(status="ok" if hits else "empty", records=hits[:limit], total_matches=len(hits),
                                truncated=len(hits) > limit)
        return self._keep(res, "github")

    def get_commit(self, sha: str) -> BackendResult:
        if self.github is None:
            return self._missing("GitHub")
        return self._keep(self.github.get_commit(_strip(sha)), "github")

    def get_config(self, service: str, version=None) -> BackendResult:
        if self.github is None:
            return self._missing("GitHub")
        path = CONFIG_PATHS.get(service)
        if path is None:
            return BackendResult(status="unavailable", error=f"no known config file for service '{service}'")
        ref = _strip(version) if version and _SHA.fullmatch(_strip(version)) else self.deployed_commit(service)
        if ref is None:
            return BackendResult(status="unavailable", error=f"deployed version of {service} is unknown (no deployment record)")
        return self._keep(self.github.get_file(path, ref), "github")

    # ------------------------------------------------------------------ CI
    def list_workflow_runs(self, limit=5, status=None) -> BackendResult:
        if self.github is None:
            return self._missing("GitHub Actions")
        return self._before_cutoff(self._keep(self.github.list_workflow_runs(limit=limit, status=status), "github-actions"))

    def get_workflow_run(self, run_id) -> BackendResult:
        if self.github is None:
            return self._missing("GitHub Actions")
        return self._before_cutoff(self._keep(self.github.get_run(run_id), "github-actions"))

    def get_job_log(self, job_id, failed_only=True) -> BackendResult:
        if self.github is None:
            return self._missing("GitHub Actions")
        return self._before_cutoff(self._keep(self.github.get_job_log(job_id, failed_only=failed_only), "github-actions"))

    def list_run_artifacts(self, run_id) -> BackendResult:
        if self.github is None:
            return self._missing("GitHub Actions")
        return self._before_cutoff(self._keep(self.github.list_run_artifacts(run_id), "github-actions"))

    def list_jenkins_builds(self, job=None, limit=5) -> BackendResult:
        if self.jenkins is None or not self.jenkins.configured:
            return self._missing("Jenkins")
        return self._keep(self.jenkins.list_builds(job=job, limit=limit, cutoff=self.cutoff), "jenkins")

    def get_jenkins_console(self, job: str, number: int) -> BackendResult:
        if self.jenkins is None or not self.jenkins.configured:
            return self._missing("Jenkins")
        return self._keep(self.jenkins.get_console(job, number, cutoff=self.cutoff), "jenkins")

    # ------------------------------------------------------------------ tickets
    def search_tickets(self, query=None, service=None, limit=5) -> BackendResult:
        if self.jira is None or not self.jira.configured:
            return self._missing("Jira")
        return self._keep(self.jira.search_tickets(query=query, service=service, limit=limit, cutoff=self.cutoff), "jira")

    def get_ticket(self, key: str) -> BackendResult:
        if self.jira is None or not self.jira.configured:
            return self._missing("Jira")
        return self._keep(self.jira.get_ticket(key, cutoff=self.cutoff), "jira")

    # ------------------------------------------------------------------ full originals
    def open_record(self, evidence_id: str) -> BackendResult:
        """The full original of a record whose preview was shortened, split into parts the model can read."""
        rec = self._full.get(str(evidence_id).strip())
        if rec is None:
            return BackendResult(status="empty", total_matches=0,
                                 error=f"{evidence_id} was not shortened or was not retrieved in this investigation")
        full = self._original(rec)
        parts = [full[i:i + PART_CHARS] for i in range(0, len(full), PART_CHARS)][:MAX_PARTS]
        out = [rec.model_copy(update={"evidence_id": f"{rec.evidence_id}#part{n}", "content": p, "truncated": False,
                                      "title": f"{rec.title} (part {n} of {len(parts)})"})
               for n, p in enumerate(parts, start=1)]
        return BackendResult(status="ok", records=out, total_matches=len(out),
                             truncated=len(full) > PART_CHARS * MAX_PARTS)

    def _original(self, rec: EvidenceRecord) -> str:
        if rec.source == "commits" and self.github is not None:
            res = self.github.get_commit(rec.evidence_id.split(":")[-1])
            if res.records:
                return res.records[0].attributes.get("full_text") or res.records[0].content
        return rec.attributes.get("full_text") or rec.content


def load_live_stack(incident: Incident, repo: Optional[str] = None, capture_dir=None) -> LiveStackBackend:
    """Build the live-stack backend from the environment. Unconfigured sources stay None (reported, not faked)."""
    import os
    from blackbox.connectors.demo_runtime import DemoRuntime
    from blackbox.connectors.jenkins import JenkinsConnector
    from blackbox.connectors.jira import JiraConnector
    from blackbox.connectors.loki import LokiLogs
    from blackbox.connectors.prometheus import PrometheusMetrics
    from blackbox.connectors.tempo import TempoTraces
    github = None
    repo = repo or os.environ.get("BLACKBOX_GITHUB_REPO", "likhith2366/blackbox-demo-shop")
    if repo:
        from blackbox.connectors.github_api import GitHubConnector
        if capture_dir is not None:
            from blackbox.connectors import capture
            github = GitHubConnector(repo, session=capture.RecordingSession(capture_dir, repo=repo))
        else:
            github = GitHubConnector(repo)
    jira, jenkins = JiraConnector(), JenkinsConnector()
    return LiveStackBackend(incident, loki=LokiLogs(), prometheus=PrometheusMetrics(), tempo=TempoTraces(),
                            deploys=DemoRuntime(), github=github, jira=jira, jenkins=jenkins)
