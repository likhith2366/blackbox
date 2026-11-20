"""Prompt loading, evidence rendering, token budgeting and report validation shared by all systems."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable, Optional

from blackbox.model.adapter import count_tokens
from blackbox.schemas import (
    INSUFFICIENT_HEADLINE, Claim, EvidenceRecord, FinalReport, Incident, RankedCause,
    ReportStatus, SourceStatus, SystemName,
)

PROMPT_DIR = Path(__file__).parent / "prompts"
RUNTIME_SOURCES = {"logs", "traces", "metrics", "configs", "deployments"}

_CONF = {"type": "string", "enum": ["low", "medium", "high"]}
_IDS = {"type": "array", "items": {"type": "string"}, "maxItems": 6}

ROUND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "hypotheses": {
            "type": "array", "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "component": {"type": "string"},
                    "mechanism": {"type": "string"},
                    "status": {"type": "string", "enum": ["active", "supported", "rejected"]},
                    "confidence": _CONF,
                    "supporting": _IDS,
                    "contradicting": _IDS,
                    "note": {"type": "string"},
                },
                "required": ["id", "component", "mechanism", "status", "confidence", "supporting", "contradicting"],
            },
        },
        # tool name is a free string on purpose: the allowlist is enforced in code, so an
        # attempted unauthorized call is observable instead of being hidden by the grammar
        "tool_calls": {
            "type": "array", "maxItems": 3,
            "items": {"type": "object",
                      "properties": {"tool": {"type": "string"}, "args": {"type": "object"}},
                      "required": ["tool", "args"]},
        },
        "decision": {"type": "string", "enum": ["continue", "diagnose", "insufficient_evidence", "no_fault"]},
        "rationale": {"type": "string"},
    },
    "required": ["hypotheses", "tool_calls", "decision", "rationale"],
}

REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["diagnosis", "insufficient_evidence", "no_fault_observed"]},
        "ranked_causes": {
            "type": "array", "maxItems": 3,
            "items": {"type": "object",
                      "properties": {"component": {"type": "string"}, "mechanism": {"type": "string"},
                                     "confidence": _CONF, "evidence_ids": _IDS},
                      "required": ["component", "mechanism", "confidence", "evidence_ids"]},
        },
        "claims": {
            "type": "array", "maxItems": 6,
            "items": {"type": "object",
                      "properties": {"text": {"type": "string"},
                                     "kind": {"type": "string", "enum": ["factual", "causal", "tentative"]},
                                     "evidence_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 4}},
                      "required": ["text", "kind", "evidence_ids"]},
        },
        "unresolved_questions": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "next_check": {"type": "string"},
        "suggested_fix": {"type": "string"},
    },
    "required": ["status", "ranked_causes", "claims", "unresolved_questions", "next_check"],
}


def round_schema(evidence_retrieved: bool, calls_left: int, per_round: int) -> dict[str, Any]:
    """Response grammar that follows the investigation state.

    Measured on qwen3:4b: with the full schema the model declared insufficient_evidence in
    round 1 without calling a tool, twice in a row. Deciding is only legal once evidence
    exists, and tool calls are impossible once the budget is spent.
    """
    import copy
    schema = copy.deepcopy(ROUND_SCHEMA)
    props = schema["properties"]
    props["tool_calls"]["maxItems"] = max(0, min(per_round, calls_left))
    if not evidence_retrieved and calls_left > 0:
        props["tool_calls"]["minItems"] = 1
        props["decision"]["enum"] = ["continue"]
    elif calls_left <= 0:
        props["tool_calls"]["maxItems"] = 0
        props["decision"]["enum"] = ["diagnose", "insufficient_evidence", "no_fault"]
    return schema


def clean_text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"none", "null", "n/a", "na", "-"} else text


def fit_text(text: str, max_tokens: int, marker: str = " ...[cut to fit the context window]") -> tuple[str, bool]:
    """Shorten text to at most max_tokens (tokenizer count). Returns (text, was_cut)."""
    if max_tokens <= 0:
        return marker.strip(), True
    if count_tokens(text) <= max_tokens:
        return text, False
    lo, hi = 0, len(text)
    while lo < hi:                       # longest prefix that fits, by binary search on characters
        mid = (lo + hi + 1) // 2
        if count_tokens(text[:mid] + marker) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + marker, True


def load_prompt(name: str) -> tuple[str, str]:
    """Return (text, version) where version pins the exact file contents."""
    text = (PROMPT_DIR / f"{name}.txt").read_text(encoding="utf-8")
    return text, f"{name}@{hashlib.sha256(text.encode()).hexdigest()[:10]}"


def render_incident(incident: Incident, statuses: Optional[list[SourceStatus]] = None) -> str:
    a = incident.alert
    lines = [
        f"Incident {incident.incident_id} ({incident.environment})",
        f"Alert: {a.title} | service={a.service} | signal={a.signal} | condition: {a.condition}"
        + (f" | observed={a.observed_value}" if a.observed_value is not None else "")
        + f" | fired_at={a.fired_at}",
        f"Window: {incident.window_start} to {incident.window_end}; evidence after {incident.investigation_cutoff} is not available.",
        f"Services: {', '.join(incident.services)}",
    ]
    if incident.notes:
        lines.append(f"Notes: {incident.notes}")
    if statuses:
        parts = []
        for s in statuses:
            state = "available" if s.available and s.healthy else ("unhealthy" if s.available else "unavailable")
            parts.append(f"{s.source}={state}" + (f" ({s.detail})" if s.detail and state != "available" else ""))
        lines.append("Sources: " + "; ".join(parts))
    return "\n".join(lines)


def render_record(rec: EvidenceRecord, full: bool = True, max_chars: int = 600) -> str:
    head = f"[{rec.evidence_id}] {rec.source}"
    if rec.service:
        head += f" {rec.service}"
    if rec.event_time:
        head += f" {rec.event_time}"
    if rec.version:
        head += f" v={rec.version}"
    if rec.title:
        head += f" | {rec.title}"
    if not full or not rec.content:
        return head
    body = rec.content if len(rec.content) <= max_chars else rec.content[:max_chars] + " ...[cut]"
    return f"{head}\n{body}" if body.strip() and body.strip() != rec.title.strip() else head


def fit_records(records: list[EvidenceRecord], budget_tokens: int, from_end: bool = False,
                max_chars: int = 600) -> tuple[list[EvidenceRecord], list[EvidenceRecord], int, int]:
    """Greedily keep records (in order, or newest-first when from_end) until the token budget is spent.

    Returns (included in original order, omitted, included_tokens, omitted_tokens).
    """
    order = list(reversed(records)) if from_end else list(records)
    included, omitted, used, dropped = [], [], 0, 0
    for rec in order:
        cost = count_tokens(render_record(rec, max_chars=max_chars)) + 1
        if not omitted and used + cost <= budget_tokens:
            included.append(rec)
            used += cost
        else:
            omitted.append(rec)
            dropped += cost
    if from_end:
        included.reverse()
        omitted.reverse()
    return included, omitted, used, dropped


def resolve_citation(cited: str, seen: set[str]) -> tuple[Optional[str], str]:
    """Map one citation to a retrieved evidence id, or refuse. Returns (resolved, method).

    Measured on qwen3:4b: it copies the id together with the rest of the evidence line
    ("metric:m1: payment-svc pool_active=20/20") or drops the source prefix ("m1").
    exact     the citation is a retrieved id (brackets stripped)
    embedded  exactly one distinct retrieved id appears verbatim inside the citation
              (ids that are only substrings of a longer matching id are not distinct)
    suffix    the citation equals the part after "source:" of exactly one retrieved id
    ambiguous more than one distinct id matches: refused, never guessed
    invalid   nothing matches
    """
    c = cited.strip().strip("[]").strip()
    if c in seen:
        return c, "exact"
    # the id must stand alone: "log:1" inside "log:12" or "log:9f2c41" inside "log:9f2c41ff" is
    # a different (possibly fabricated) id, not a copy of the real one (found by QA QA)
    spans = [(m.start(), m.end(), i) for i in seen
             for m in re.finditer(r"(?<![A-Za-z0-9_])" + re.escape(i) + r"(?![A-Za-z0-9_])", c)]
    # a match lying inside a longer match is part of that id ("jira:DEMO-12" in "jira:DEMO-12#c3");
    # two separate matches ("log:a1, log:a12") name two different records (QA-F01b)
    kept = [a for a in spans if not any(b != a and b[0] <= a[0] and a[1] <= b[1] and b[1] - b[0] > a[1] - a[0]
                                        for b in spans)]
    distinct = sorted({a[2] for a in kept})
    if len(distinct) == 1:
        return distinct[0], "embedded"
    if len(distinct) > 1:
        return None, "ambiguous"
    by_suffix = [i for i in seen if ":" in i and i.split(":", 1)[1] == c]
    if len(by_suffix) == 1:
        return by_suffix[0], "suffix"
    return None, ("ambiguous" if by_suffix else "invalid")


def resolve_id(cited: str, seen: set[str]) -> Optional[str]:
    return resolve_citation(cited, seen)[0]


def check_ids(ids: Iterable[str], seen: set[str], normalized: Optional[list[str]] = None,
              audit: Optional[list] = None, where: str = "") -> tuple[list[str], list[str]]:
    """Validate citations. Every citation, valid or not, is appended to `audit` when given."""
    from blackbox.schemas import CitationCheck
    valid, invalid = [], []
    for i in ids or []:
        i = str(i).strip()
        if not i:
            continue
        hit, method = resolve_citation(i, seen)
        if audit is not None:
            audit.append(CitationCheck(where=where, raw=i, resolved=hit, method=method))
        if hit is None:
            invalid.append(i)
            continue
        if method != "exact" and normalized is not None:
            normalized.append(f"{i} -> {hit}")
        valid.append(hit)
    return list(dict.fromkeys(valid)), list(dict.fromkeys(invalid))


def headline_for(status: ReportStatus, causes: list[RankedCause]) -> str:
    if status == "insufficient_evidence":
        return INSUFFICIENT_HEADLINE
    if status == "no_fault_observed":
        return "No fault observed in the available evidence."
    if status == "diagnosis" and causes:
        return f"Most likely cause: {causes[0].component} - {causes[0].mechanism}"
    if status == "failed":
        return "Investigation failed before a report could be produced."
    return INSUFFICIENT_HEADLINE


def build_report(parsed: Optional[dict[str, Any]], incident_id: str, system: SystemName,
                 status: ReportStatus, seen: set[str], extra_invalid: Iterable[str] = ()) -> FinalReport:
    """Validate a model-written report: drop citations to evidence never retrieved, apply status rules."""
    parsed = parsed or {}
    invalid: list[str] = list(extra_invalid)
    normalized: list[str] = []
    audit: list = []
    causes: list[RankedCause] = []
    if status == "diagnosis":
        for i, c in enumerate((parsed.get("ranked_causes") or [])[:3], start=1):
            valid, bad = check_ids(c.get("evidence_ids", []), seen, normalized, audit, f"cause{i}")
            invalid += bad
            causes.append(RankedCause(rank=i, component=str(c.get("component", "")).strip(),
                                      mechanism=str(c.get("mechanism", "")).strip(),
                                      confidence=c.get("confidence", "low") if c.get("confidence") in ("low", "medium", "high") else "low",
                                      evidence_ids=valid))
        causes = [c for c in causes if c.component or c.mechanism]
        if not causes:  # a diagnosis with no cause is not a diagnosis
            status = "insufficient_evidence"
    claims: list[Claim] = []
    for i, cl in enumerate((parsed.get("claims") or [])[:6], start=1):
        valid, bad = check_ids(cl.get("evidence_ids", []), seen, normalized, audit, f"claim c{i}")
        invalid += bad
        kind = cl.get("kind") if cl.get("kind") in ("factual", "causal", "tentative") else "tentative"
        text = str(cl.get("text", "")).strip()
        if text:
            claims.append(Claim(claim_id=f"c{i}", text=text, kind=kind, evidence_ids=valid))
    fix = clean_text(parsed.get("suggested_fix")) or None
    return FinalReport(
        incident_id=incident_id, system=system, status=status,
        headline=headline_for(status, causes), ranked_causes=causes, claims=claims,
        unresolved_questions=[clean_text(q) for q in (parsed.get("unresolved_questions") or [])[:4] if clean_text(q)],
        next_check=clean_text(parsed.get("next_check")),
        suggested_fix=(f"(untested) {fix}" if fix and not fix.lower().startswith("(untested)") else fix),
        invalid_citations=list(dict.fromkeys(invalid)),
        normalized_citations=list(dict.fromkeys(normalized)),
        citation_audit=audit,
    )
