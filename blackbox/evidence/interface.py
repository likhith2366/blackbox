"""The boundary between the investigator (Agent 1) and the evidence layer (Agent 2).

Agent 1 owns this file. Agent 2 implements it in blackbox/evidence/backend.py and
exposes `load_backend`. The investigator only ever reaches evidence through these
methods, so every rule below is enforced in the backend, not in prompts:

- Investigation cutoff: never return a record whose available_time is after
  incident.investigation_cutoff (e.g. a ticket comment written after the incident).
- Ablation: an ablated source disappears from every method, index and summary,
  including indirect mentions (see CONTRACT.md "Ablations").
- Read-only: no method changes the snapshot or the demo application.
- Answer keys and the fault controller (evaluator_private/, demo/controller*) are
  never read by this layer.
- Connector failure is simulated here (scenario 8): a method returns
  BackendResult(status="error", error=...) rather than raising.
- Time arguments are ISO-8601 UTC strings; None means "the incident window".
- `limit` caps returned records; set `truncated=True` and `total_matches` when more exist.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from blackbox.schemas import Ablation, BackendResult, EvidenceRecord, Incident, SourceStatus


@runtime_checkable
class EvidenceBackend(Protocol):
    incident_id: str
    ablation: Ablation

    def incident(self) -> Incident: ...

    def source_status(self) -> list[SourceStatus]: ...

    # telemetry
    def search_logs(self, service: Optional[str] = None, start: Optional[str] = None,
                    end: Optional[str] = None, query: Optional[str] = None,
                    level: Optional[str] = None, limit: int = 20) -> BackendResult: ...

    def search_traces(self, service: Optional[str] = None, start: Optional[str] = None,
                      end: Optional[str] = None, status: Optional[str] = None,
                      min_duration_ms: Optional[float] = None, limit: int = 10) -> BackendResult: ...

    def get_trace(self, trace_id: str) -> BackendResult: ...

    def get_metrics(self, service: str, metric: Optional[str] = None,
                    start: Optional[str] = None, end: Optional[str] = None) -> BackendResult: ...

    # release provenance
    def list_deployments(self, service: Optional[str] = None, start: Optional[str] = None,
                         end: Optional[str] = None) -> BackendResult: ...

    def get_build(self, build_id: str) -> BackendResult: ...

    # code and configuration
    def search_commits(self, service: Optional[str] = None, path: Optional[str] = None,
                       query: Optional[str] = None, since: Optional[str] = None,
                       until: Optional[str] = None, limit: int = 10) -> BackendResult: ...

    def get_commit(self, sha: str) -> BackendResult: ...

    def get_config(self, service: str, version: Optional[str] = None) -> BackendResult: ...

    # issue context
    def search_tickets(self, query: Optional[str] = None, service: Optional[str] = None,
                       limit: int = 5) -> BackendResult: ...

    def get_ticket(self, key: str) -> BackendResult: ...

    # not a model tool: the full log window for baseline B0, oldest first
    def export_logs(self, start: Optional[str] = None,
                    end: Optional[str] = None) -> list[EvidenceRecord]: ...


# Implemented by Agent 2 in blackbox/evidence/backend.py:
#   def load_backend(incident_id: str, ablation: Ablation = "none",
#                    snapshot_root: Path | None = None) -> EvidenceBackend
#   def list_incidents(snapshot_root: Path | None = None) -> list[str]
