"""Shared data contract for BlackBox. Owned by Integration; the evidence and evaluation modules import, never edit.

Request a change by writing to coordination/requests.md. Every persisted artefact
(snapshots, run records, reports) is produced from these models, so a field added
here is the only way it reaches the other agents' code.

Conventions
- Timestamps are ISO-8601 UTC strings ending in "Z", e.g. "2026-11-19T21:04:05.123Z".
- Evidence IDs are opaque, stable within a snapshot and never reveal the injected fault.
- "Model-visible" means the text can appear in a prompt. Answer keys never are.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

SourceName = Literal[
    "logs", "traces", "metrics", "deployments", "builds", "commits", "configs", "tickets"
]
Ablation = Literal["none", "no_jira", "no_provenance", "no_traces"]
SystemName = Literal["b0", "b1", "blackbox"]
ReportStatus = Literal["diagnosis", "insufficient_evidence", "no_fault_observed", "failed"]
Confidence = Literal["low", "medium", "high"]
ToolStatus = Literal[
    "ok", "empty", "unavailable", "error", "timeout", "invalid_args", "denied", "budget_exceeded"
]
# empty       = the source answered and nothing matched
# unavailable = the source is absent from this snapshot (withheld, ablated, or a service not covered)
# error       = the source exists but failed (e.g. simulated connector failure)

INSUFFICIENT_HEADLINE = "No reliable root cause identified."


# --------------------------------------------------------------------------- incident
class Alert(BaseModel):
    alert_id: str
    title: str
    service: str
    signal: str                         # e.g. "p95_latency_ms", "error_rate"
    condition: str                      # human-readable rule, e.g. "p95_latency_ms > 1500 for 30s"
    observed_value: Optional[float] = None
    fired_at: str


class Incident(BaseModel):
    """Model-visible description of what the investigator is asked to explain."""
    incident_id: str                    # opaque, e.g. "INC-4821"
    alert: Alert
    window_start: str
    window_end: str
    investigation_cutoff: str           # nothing with available_time after this is visible
    environment: str = "demo-local"
    services: list[str]
    available_sources: list[SourceName]
    notes: str = ""                     # must not hint at the answer


# --------------------------------------------------------------------------- evidence
class EvidenceRecord(BaseModel):
    evidence_id: str                    # e.g. "log:9f2c41", "span:..", "commit:3a9e1f2", "jira:DEMO-12#c3"
    source: SourceName
    service: Optional[str] = None
    event_time: Optional[str] = None
    available_time: Optional[str] = None
    version: Optional[str] = None       # deployed version or commit sha where meaningful
    title: str = ""                     # one-line summary
    content: str = ""                   # model-visible text, already length-limited
    content_ref: Optional[str] = None   # path#offset of the full original
    attributes: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False
    fixture: bool = False               # True for team-authored demo data (tickets, build fixtures)


class SourceStatus(BaseModel):
    source: SourceName
    available: bool                     # present in this snapshot (after ablation)
    healthy: bool                       # connector currently answering
    detail: str = ""
    fixture: bool = False               # snapshot/fixture adapter, not a live integration


class BackendResult(BaseModel):
    """What an EvidenceBackend method returns. The tool layer wraps it into a ToolResult."""
    status: Literal["ok", "empty", "unavailable", "error", "timeout"]
    records: list[EvidenceRecord] = Field(default_factory=list)
    total_matches: Optional[int] = None
    truncated: bool = False             # more matches existed than were returned
    error: Optional[str] = None


# --------------------------------------------------------------------------- tools
class ToolRequest(BaseModel):
    call_id: str
    round: int
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    call_id: str
    tool: str
    status: ToolStatus
    records: list[EvidenceRecord] = Field(default_factory=list)
    total_matches: Optional[int] = None
    truncated: bool = False
    error: Optional[str] = None
    elapsed_ms: float = 0.0
    is_retry: bool = False              # a configured retry after an error (excluded from redundancy)


# --------------------------------------------------------------------------- investigation
class Hypothesis(BaseModel):
    hypothesis_id: str
    component: str
    mechanism: str
    status: Literal["active", "supported", "rejected"] = "active"
    confidence: Confidence = "low"
    supporting: list[str] = Field(default_factory=list)
    contradicting: list[str] = Field(default_factory=list)
    note: str = ""


StopDecision = Literal[
    "continue", "stop_diagnosis", "stop_insufficient", "stop_no_fault",
    "stop_budget", "stop_rounds", "stop_error", "stop_timeout",
]


class CitationCheck(BaseModel):
    """One citation as written by the model and what it resolved to."""
    where: str                          # e.g. "h1.supporting", "cause1", "claim c2"
    raw: str
    resolved: Optional[str] = None
    method: Literal["exact", "embedded", "suffix", "invalid", "ambiguous"]


class InvestigationStep(BaseModel):
    round: int
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    model_seconds: float = 0.0
    parse_ok: bool = True
    parse_error: Optional[str] = None
    raw_output: str = ""
    model_decision: str = ""            # what the model asked for
    hypotheses_before: list[Hypothesis] = Field(default_factory=list)
    hypotheses_after: list[Hypothesis] = Field(default_factory=list)
    hypothesis_changes: list[str] = Field(default_factory=list)
    tool_requests: list[ToolRequest] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    invalid_citations: list[str] = Field(default_factory=list)
    normalized_citations: list[str] = Field(default_factory=list)   # "as written -> resolved id"
    duplicate_calls: list[str] = Field(default_factory=list)        # call_ids answered from cache
    citation_audit: list[CitationCheck] = Field(default_factory=list)
    orchestration_rules: list[str] = Field(default_factory=list)    # constraints the coordinator imposed this round
    context: dict[str, Any] = Field(default_factory=dict)           # token estimate vs actual, notebook decisions
    stop_decision: StopDecision = "continue"
    stop_reason: str = ""


# --------------------------------------------------------------------------- report
class RankedCause(BaseModel):
    rank: int
    component: str
    mechanism: str
    confidence: Confidence = "low"
    evidence_ids: list[str] = Field(default_factory=list)


class Claim(BaseModel):
    claim_id: str
    text: str
    kind: Literal["factual", "causal", "tentative"]
    evidence_ids: list[str] = Field(default_factory=list)


class FinalReport(BaseModel):
    incident_id: str
    system: SystemName
    status: ReportStatus
    headline: str                       # INSUFFICIENT_HEADLINE when status == insufficient_evidence
    ranked_causes: list[RankedCause] = Field(default_factory=list)   # at most 3
    claims: list[Claim] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    next_check: str = ""
    suggested_fix: Optional[str] = None  # always presented as untested
    invalid_citations: list[str] = Field(default_factory=list)       # cited ids never retrieved
    normalized_citations: list[str] = Field(default_factory=list)    # "as written -> resolved id"
    citation_audit: list[CitationCheck] = Field(default_factory=list)


# --------------------------------------------------------------------------- run metadata
class RunConfig(BaseModel):
    model: str = "qwen3:4b"
    ollama_url: str = "http://127.0.0.1:11434"
    temperature: float = 0.0
    top_p: float = 1.0
    seed: int = 7
    num_ctx: int = 8192                 # 16384 spills to CPU on the 6 GB RTX 3060 (measured)
    num_predict: int = 900
    think: bool = False
    max_rounds: int = 6
    max_tool_calls: int = 12
    max_calls_per_round: int = 3
    model_timeout_s: float = 240.0
    tool_timeout_s: float = 10.0
    run_timeout_s: float = 1200.0
    reserve_output_tokens: int = 1100
    reserve_instruction_tokens: int = 1300
    coverage_hint: bool = False     # experiment flag: tell the model which available sources it has not queried
    round_prompt: str = "investigator_v2"      # prompt files under blackbox/investigator/prompts/
    report_prompt: str = "report_v1"
    baseline_prompt: str = "baseline_v1"
    prompt_version: str = ""
    code_revision: str = ""


class Usage(BaseModel):
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_seconds: float = 0.0


class EvidenceBudget(BaseModel):
    """How the single-call baselines fitted evidence into the context window."""
    context_tokens: int
    reserved_tokens: int
    evidence_token_budget: int
    evidence_tokens_total: int
    included_ids: list[str] = Field(default_factory=list)
    omitted_ids: list[str] = Field(default_factory=list)
    omitted_tokens: int = 0
    matched: Optional[bool] = None      # B1 only: False when truncation broke the same-evidence premise


class SystemOutput(BaseModel):
    schema_version: str = SCHEMA_VERSION
    system: SystemName
    incident_id: str
    ablation: Ablation = "none"
    status: Literal["completed", "failed", "timeout"]
    error: Optional[str] = None
    report: Optional[FinalReport] = None
    steps: list[InvestigationStep] = Field(default_factory=list)
    evidence_seen: list[EvidenceRecord] = Field(default_factory=list)   # retrieval order, de-duplicated
    usage: Usage = Field(default_factory=Usage)
    wall_seconds: float = 0.0
    rounds_used: int = 0
    tool_calls_used: int = 0
    budget_violations: int = 0          # requests refused because a limit was reached
    unauthorized_attempts: list[ToolRequest] = Field(default_factory=list)
    unauthorized_executed: int = 0      # must stay 0: enforced in code
    evidence_budget: Optional[EvidenceBudget] = None
    context: dict[str, Any] = Field(default_factory=dict)           # single-call / report-call token accounting
    config: RunConfig = Field(default_factory=RunConfig)
    prompt_versions: dict[str, str] = Field(default_factory=dict)


# --------------------------------------------------------------------------- evaluator-private
ScenarioType = Literal[
    "pilot", "misleading_symptom", "unrelated_change", "insufficient_evidence",
    "healthy", "connector_failure", "prompt_injection",
]


class AnswerKey(BaseModel):
    """Lives only under evaluator_private/. Never imported by blackbox/ code."""
    incident_id: str
    scenario_number: int                # 0 = pilot, 1..9 as in Milestone 2
    scenario_type: ScenarioType
    is_primary: bool                    # scenarios 1-6
    diagnosable: bool                   # scenarios 1-4 (and robustness replays if predefined)
    variant_of: Optional[str] = None    # incident_id this replays, for 8 and 9
    expected_status: ReportStatus
    component: Optional[str] = None
    mechanism: Optional[str] = None
    accepted_components: list[str] = Field(default_factory=list)
    accepted_mechanisms: list[str] = Field(default_factory=list)   # predefined equivalent wordings
    decisive_evidence_ids: list[str] = Field(default_factory=list)
    distractor_evidence_ids: list[str] = Field(default_factory=list)
    injected_fault: dict[str, Any] = Field(default_factory=dict)
    available_sources: list[SourceName] = Field(default_factory=list)
    remaining_evidence_sufficient: Optional[bool] = None            # scenario 8
    notes: str = ""
    frozen_at: Optional[str] = None
