"""TEST FIXTURE (QA, QA). Test doubles only: not evidence, not a model, not a result.

- ScriptedAdapter: replays canned model outputs (dict, raw string, or an Exception to raise).
  It records the exact messages it was sent so tests can re-count prompt tokens.
- QABackend: a tiny in-memory EvidenceBackend with switches for the failure modes QA needs
  (slow connector, raising connector, malformed return values, connector that fails after N calls).
Every record it returns carries fixture=True and an obviously synthetic id.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from blackbox.model.adapter import ModelError, ModelReply, parse_json_object
from blackbox.schemas import Alert, BackendResult, EvidenceRecord, Incident, SourceStatus

T = "2026-11-19T10:00:00Z"


def rec(i: str, source: str = "logs", text: str = "QA FIXTURE pool wait 900ms active=20/20",
        service: str = "payment-svc") -> EvidenceRecord:
    return EvidenceRecord(evidence_id=i, source=source, service=service, event_time=T, available_time=T,
                          title=text, content=text, fixture=True)


class QABackend:
    """In-memory backend. `mode` selects a failure behaviour for search_logs."""

    incident_id = "INC-9001"
    ablation = "none"

    def __init__(self, mode: str = "ok", slow: float = 0.0, fail_after: Optional[int] = None,
                 notes: str = "QA fixture incident", n_logs: int = 2, log_text: str | None = None):
        self.mode, self.slow, self.fail_after = mode, slow, fail_after
        self.notes, self.n_logs, self.log_text = notes, n_logs, log_text
        self.calls: list[str] = []
        self._ok = 0

    def incident(self) -> Incident:
        return Incident(incident_id=self.incident_id,
                        alert=Alert(alert_id="QA-1", title="checkout latency high (QA fixture)",
                                    service="checkout-svc", signal="p95_latency_ms", condition="> 1500",
                                    fired_at=T),
                        window_start=T, window_end=T, investigation_cutoff=T,
                        services=["checkout-svc", "payment-svc"],
                        available_sources=["logs", "metrics", "deployments"], notes=self.notes)

    def source_status(self) -> list[SourceStatus]:
        return [SourceStatus(source=s, available=True, healthy=True, fixture=True)
                for s in ("logs", "metrics", "deployments")]

    def search_logs(self, **kw) -> Any:
        self.calls.append("search_logs")
        if self.slow:
            time.sleep(self.slow)
        if self.fail_after is not None and self._ok >= self.fail_after:
            return BackendResult(status="error", error="QA fixture: logs connector failed (HTTP 502)")
        if self.mode == "raise":
            raise ConnectionError("QA fixture: connector raised")
        if self.mode == "none":
            return None
        if self.mode == "dict":
            return {"status": "ok", "records": []}
        if self.mode == "bad_record":
            return BackendResult.model_construct(status="ok", records=[{"evidence_id": "log:x"}],
                                                 total_matches=1, truncated=False, error=None)
        self._ok += 1
        text = self.log_text or "QA FIXTURE pool wait 900ms active=20/20"
        return BackendResult(status="ok", records=[rec(f"log:qa{n}", text=text) for n in range(1, self.n_logs + 1)],
                             total_matches=self.n_logs)

    def get_metrics(self, **kw) -> BackendResult:
        self.calls.append("get_metrics")
        return BackendResult(status="ok", records=[rec("metric:qa1", "metrics", "QA FIXTURE pool_active=20/20")])

    def list_deployments(self, **kw) -> BackendResult:
        self.calls.append("list_deployments")
        return BackendResult(status="ok", records=[rec("deploy:qa1", "deployments", "QA FIXTURE deploy v2")])

    def export_logs(self, start=None, end=None) -> list[EvidenceRecord]:
        return [rec(f"log:e{n:03d}", text=f"QA FIXTURE line {n}") for n in range(10)]


class ScriptedAdapter:
    """Replays outputs in order. An Exception instance is raised instead of returned."""

    def __init__(self, outputs: list, sleep: float = 0.0):
        self.outputs = list(outputs)
        self.sleep = sleep
        self.messages: list[list[dict]] = []
        self.num_predicts: list[Optional[int]] = []

    def chat(self, messages, schema=None, num_predict=None) -> ModelReply:
        self.messages.append(messages)
        self.num_predicts.append(num_predict)
        if self.sleep:
            time.sleep(self.sleep)
        out = self.outputs.pop(0) if self.outputs else {
            "hypotheses": [], "tool_calls": [], "decision": "insufficient_evidence", "rationale": ""}
        if isinstance(out, BaseException):
            raise out
        text = out if isinstance(out, str) else json.dumps(out)
        parsed, err = parse_json_object(text)
        return ModelReply(text=text, parsed=parsed, parse_error=err, prompt_tokens=100,
                          completion_tokens=20, seconds=0.01)


def H(hid="h1", sup=(), status="active", conf="medium", con=()):
    return {"id": hid, "component": "payment-svc", "mechanism": "connection pool exhausted",
            "status": status, "confidence": conf, "supporting": list(sup), "contradicting": list(con)}


def ROUND(tools=(), decision="continue", hyps=None):
    return {"hypotheses": hyps if hyps is not None else [H()],
            "tool_calls": [{"tool": t, "args": a} for t, a in tools], "decision": decision, "rationale": ""}


REPORT_INSUFF = {"status": "insufficient_evidence", "ranked_causes": [], "claims": [],
                 "unresolved_questions": [], "next_check": ""}
REPORT_DIAG = {"status": "diagnosis", "ranked_causes": [{"component": "payment-svc", "mechanism": "pool exhausted",
               "confidence": "high", "evidence_ids": ["log:qa1"]}],
               "claims": [{"text": "pool is full", "kind": "factual", "evidence_ids": ["metric:qa1"]}],
               "unresolved_questions": [], "next_check": "check pool size"}

__all__ = ["ModelError", "QABackend", "ScriptedAdapter", "H", "ROUND", "REPORT_INSUFF", "REPORT_DIAG", "rec", "T"]
