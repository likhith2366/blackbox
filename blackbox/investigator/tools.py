"""Read-only diagnostic tools. The allowlist and argument validation are enforced here,
not in the prompt: a request for any other tool is refused and recorded."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from blackbox.evidence.interface import EvidenceBackend
from blackbox.schemas import BackendResult, ToolRequest, ToolResult


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchLogs(_Args):
    service: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    query: Optional[str] = None
    level: Optional[str] = None
    limit: int = Field(default=15, ge=1, le=30)


class SearchTraces(_Args):
    service: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    status: Optional[str] = None
    min_duration_ms: Optional[float] = Field(default=None, ge=0)
    limit: int = Field(default=8, ge=1, le=15)


class GetTrace(_Args):
    trace_id: str = Field(min_length=1)


class GetMetrics(_Args):
    service: str = Field(min_length=1)
    metric: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None


class ListDeployments(_Args):
    service: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None


class GetBuild(_Args):
    build_id: str = Field(min_length=1)


class SearchCommits(_Args):
    service: Optional[str] = None
    path: Optional[str] = None
    query: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None
    limit: int = Field(default=8, ge=1, le=15)


class GetCommit(_Args):
    sha: str = Field(min_length=4)


class GetConfig(_Args):
    service: str = Field(min_length=1)
    version: Optional[str] = None


class SearchTickets(_Args):
    query: Optional[str] = None
    service: Optional[str] = None
    limit: int = Field(default=5, ge=1, le=10)


class GetTicket(_Args):
    key: str = Field(min_length=1)


# name -> (argument model, backend method name, one-line description for the prompt)
TOOLS: dict[str, tuple[type[_Args], str, str]] = {
    "search_logs": (SearchLogs, "search_logs",
                    "Log lines filtered by service, time range, text query and level (ERROR/WARN/INFO)."),
    "search_traces": (SearchTraces, "search_traces",
                      "Trace summaries filtered by service, time range, status (error/ok) and minimum duration."),
    "get_trace": (GetTrace, "get_trace", "All spans of one trace, across services."),
    "get_metrics": (GetMetrics, "get_metrics", "Metric series for a service (latency, error rate, pool usage...)."),
    "list_deployments": (ListDeployments, "list_deployments",
                         "Deployments in a time range with version, build and artifact."),
    "get_build": (GetBuild, "get_build", "Build manifest: commit, artifact digest, time."),
    "search_commits": (SearchCommits, "search_commits", "Commits filtered by service, file path, text and date."),
    "get_commit": (GetCommit, "get_commit", "One commit: message, author, changed files and diff."),
    "get_config": (GetConfig, "get_config", "Configuration of a service, optionally at a deployed version."),
    "search_tickets": (SearchTickets, "search_tickets", "Jira tickets matching text or a service."),
    "get_ticket": (GetTicket, "get_ticket", "One Jira ticket with its comments."),
}


def tool_catalog(available_sources: Optional[set[str]] = None) -> str:
    lines = []
    for name, (model, _, desc) in TOOLS.items():
        fields = []
        for fname, f in model.model_fields.items():
            req = f.is_required()
            fields.append(f"{fname}{'' if req else '?'}")
        lines.append(f"- {name}({', '.join(fields)}): {desc}")
    return "\n".join(lines)


class ToolRunner:
    """Executes validated tool requests against an EvidenceBackend with a per-call timeout."""

    def __init__(self, backend: EvidenceBackend, timeout_s: float):
        self.backend = backend
        self.timeout_s = timeout_s
        self._pool = ThreadPoolExecutor(max_workers=4)  # a hung call must not block the next one

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def is_allowed(tool: str) -> bool:
        return tool in TOOLS

    def run(self, req: ToolRequest) -> ToolResult:
        start = time.perf_counter()

        def done(status: str, **kw: Any) -> ToolResult:
            return ToolResult(call_id=req.call_id, tool=req.tool, status=status,
                              elapsed_ms=(time.perf_counter() - start) * 1000, **kw)

        if req.tool not in TOOLS:
            return done("denied", error=f"tool '{req.tool}' is not permitted; allowed: {', '.join(TOOLS)}")
        model, method_name, _ = TOOLS[req.tool]
        try:
            args = model.model_validate(req.args or {})
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'args'}: {e['msg']}" for e in exc.errors())
            return done("invalid_args", error=problems[:400])

        method: Optional[Callable[..., BackendResult]] = getattr(self.backend, method_name, None)
        if method is None:  # connector not implemented by this backend: an explicit failure, not a crash
            return done("error", error=f"source for '{req.tool}' is not available in this environment")
        future = self._pool.submit(method, **args.model_dump(exclude_none=True))
        try:
            result: BackendResult = future.result(timeout=self.timeout_s)
        except FutureTimeout:
            return done("timeout", error=f"tool exceeded {self.timeout_s:.0f}s")
        except Exception as exc:  # backend bug or connector exception -> explicit observation
            return done("error", error=f"{type(exc).__name__}: {str(exc)[:300]}")
        try:  # a backend that returns something else is a broken connector, not a crash (QA-F03)
            if not isinstance(result, BackendResult):
                raise TypeError(f"expected BackendResult, got {type(result).__name__}")
            status = result.status
            if status == "ok" and not result.records:
                status = "empty"
            return done(status, records=result.records, total_matches=result.total_matches,
                        truncated=result.truncated, error=result.error)
        except Exception as exc:
            return done("error", error=f"backend returned a malformed result: {type(exc).__name__}")
