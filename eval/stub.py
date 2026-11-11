"""STUB system and fixtures for exercising the harness before real systems exist.

DEVELOPMENT ONLY. Nothing here is a model output or a result:
- the stub system returns canned SystemOutputs (no model, no evidence reasoning);
- the fixtures are synthetic incidents STUB-0000..STUB-0009 with synthetic answer keys;
- the harness writes stub runs only under runs/_dev_stub/ and marks every record
  "stub": true; score/report refuse to put stub records in any real table.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from blackbox.schemas import (Alert, AnswerKey, BackendResult, Claim, EvidenceBudget,
                              EvidenceRecord, FinalReport, Hypothesis, Incident,
                              InvestigationStep, RankedCause, RunConfig, SourceStatus,
                              SystemOutput, ToolRequest, ToolResult, Usage)

from eval.common import SCENARIO_TYPE_BY_NUMBER, write_json

STUB_PREFIX = "STUB-"
_EXPECTED = {0: "diagnosis", 1: "diagnosis", 2: "diagnosis", 3: "diagnosis", 4: "diagnosis",
             5: "insufficient_evidence", 6: "insufficient_evidence", 7: "no_fault_observed",
             8: "diagnosis", 9: "diagnosis"}


def stub_incident_ids() -> list[str]:
    return [f"{STUB_PREFIX}{n:04d}" for n in range(10)]


def create_stub_fixtures(root: Path) -> tuple[Path, Path]:
    """Write a synthetic manifest + answer keys under root. Returns (manifest, keys_dir)."""
    keys_dir = root / "answer_keys"
    manifest = {"_note": "STUB fixtures for harness development only. Not scenarios.",
                "incidents": []}
    for iid in stub_incident_ids():
        n = int(iid[-4:])
        diag = n in (0, 1, 2, 3, 4, 8, 9)
        # scenarios 1 and 3 use the stub's canned wording, so the matcher has hits and misses
        comp, mech = (("stub-service", "stub mechanism") if n in (0, 1, 3, 8)
                      else (("other-service", "connection pool exhaustion") if diag else (None, None)))
        key = AnswerKey(
            incident_id=iid, scenario_number=n, scenario_type=SCENARIO_TYPE_BY_NUMBER[n],
            is_primary=1 <= n <= 6, diagnosable=diag,
            variant_of=f"{STUB_PREFIX}0001" if n in (8, 9) else None,
            expected_status=_EXPECTED[n], component=comp, mechanism=mech,
            accepted_components=[], accepted_mechanisms=[],
            decisive_evidence_ids=["log:stub-1", "metric:stub-3"] if diag else [],
            remaining_evidence_sufficient=True if n == 8 else None,
            notes="STUB fixture", frozen_at="2026-01-01T00:00:00Z")
        write_json(keys_dir / f"{iid}.json", key.model_dump())
        manifest["incidents"].append({
            "incident_id": iid, "scenario_number": n, "scenario_type": SCENARIO_TYPE_BY_NUMBER[n],
            "variant_of": key.variant_of, "frozen": n != 0, "stub": True})
    mpath = root / "scenario_manifest.json"
    write_json(mpath, manifest)
    return mpath, keys_dir


class StubBackend:
    """Minimal stand-in for EvidenceBackend. Returns three canned records."""

    def __init__(self, incident_id: str, ablation: str = "none"):
        self.incident_id = incident_id
        self.ablation = ablation

    def incident(self) -> Incident:
        return Incident(incident_id=self.incident_id,
                        alert=Alert(alert_id="stub", title="stub alert", service="stub-service",
                                    signal="error_rate", condition="stub", fired_at="2026-01-01T00:00:00Z"),
                        window_start="2026-01-01T00:00:00Z", window_end="2026-01-01T00:10:00Z",
                        investigation_cutoff="2026-01-01T00:10:00Z", services=["stub-service"],
                        available_sources=["logs", "metrics"], notes="STUB")

    def source_status(self):
        return [SourceStatus(source="logs", available=True, healthy=True, detail="stub")]

    def records(self) -> list[EvidenceRecord]:
        return [EvidenceRecord(evidence_id=f"log:stub-{i}", source="logs", service="stub-service",
                               available_time="2026-01-01T00:05:00Z", title=f"stub log {i}",
                               content=f"STUB evidence text {i}") for i in (1, 2)] + [
            EvidenceRecord(evidence_id="metric:stub-3", source="metrics", service="stub-service",
                           available_time="2026-01-01T00:05:00Z", title="stub metric",
                           content="STUB metric text")]

    def search_logs(self, **kw) -> BackendResult:
        return BackendResult(status="ok", records=self.records()[:2])

    def export_logs(self, start=None, end=None):
        return self.records()[:2]


def run_stub_system(system: str, backend, config: RunConfig,
                    b1_evidence: Optional[list[EvidenceRecord]] = None) -> SystemOutput:
    iid = backend.incident_id
    n = int(iid[-4:]) if iid[-4:].isdigit() else 0
    if system == "b0" and n == 8:
        raise RuntimeError("STUB: simulated system exception (exercises failure recording)")
    recs = backend.records() if hasattr(backend, "records") else []
    status = "insufficient_evidence" if n in (2, 5) else "diagnosis"
    report = FinalReport(
        incident_id=iid, system=system, status=status,
        headline="No reliable root cause identified." if status == "insufficient_evidence"
        else "STUB headline",
        ranked_causes=[] if status != "diagnosis" else [
            RankedCause(rank=1, component="stub-service", mechanism="stub mechanism",
                        evidence_ids=["log:stub-1"]),
            RankedCause(rank=2, component="other-service", mechanism="connection pool exhaustion",
                        evidence_ids=["log:stub-9"])],
        claims=[Claim(claim_id="c1", text="STUB cited claim", kind="factual", evidence_ids=["log:stub-1"]),
                Claim(claim_id="c2", text="STUB uncited claim", kind="causal"),
                Claim(claim_id="c3", text="STUB invalid citation", kind="factual", evidence_ids=["log:stub-9"]),
                Claim(claim_id="c4", text="STUB tentative", kind="tentative")],
        invalid_citations=["log:stub-9"])
    out = SystemOutput(system=system, incident_id=iid, ablation=backend.ablation,
                       status="completed", report=report, config=config,
                       usage=Usage(model_calls=1, prompt_tokens=100, completion_tokens=20),
                       prompt_versions={"stub": "stub"})
    if system == "blackbox":
        h = Hypothesis(hypothesis_id="h1", component="stub-service", mechanism="stub mechanism",
                       supporting=["log:stub-1"], contradicting=["metric:stub-3"])
        r1 = [ToolRequest(call_id="t1", round=1, tool="search_logs", args={"service": "stub-service"}),
              ToolRequest(call_id="t2", round=1, tool="get_metrics", args={"service": "stub-service"})]
        s1 = InvestigationStep(round=1, model_decision="STUB", tool_requests=r1, tool_results=[
            ToolResult(call_id="t1", tool="search_logs", status="ok", records=recs[:2]),
            ToolResult(call_id="t2", tool="get_metrics", status="ok", records=recs[2:])],
            hypotheses_after=[h], hypothesis_changes=["STUB: added h1"])
        r2 = [ToolRequest(call_id="t3", round=2, tool="search_logs", args={"service": "stub-service"})]
        s2 = InvestigationStep(round=2, model_decision="STUB", tool_requests=r2, tool_results=[
            ToolResult(call_id="t3", tool="search_logs", status="ok", records=recs[:2])],
            hypotheses_before=[h], hypotheses_after=[h], stop_decision="stop_diagnosis",
            stop_reason="STUB")
        out.steps = [s1, s2]
        out.evidence_seen = recs
        out.rounds_used, out.tool_calls_used = 2, 3
    else:
        ev = list(b1_evidence or []) if system == "b1" else recs[:2]
        out.evidence_seen = ev
        ids = [r.evidence_id for r in ev]
        out.evidence_budget = EvidenceBudget(
            context_tokens=config.num_ctx, reserved_tokens=2400, evidence_token_budget=5792,
            evidence_tokens_total=100, included_ids=ids,
            matched=(True if system == "b1" else None))
    h = int(hashlib.sha1(f"{iid}{system}{backend.ablation}".encode()).hexdigest()[:4], 16)
    out.wall_seconds = 0.01 + (h % 10) / 1000
    return out
