"""Single-call baselines.

B0: the alert plus the incident-window logs, one model call.
B1: the evidence records the initial BlackBox run retrieved, in retrieval order, without any
    of BlackBox's hypotheses or conclusions, one model call. If they do not fit, trailing
    records are dropped, their ids and token cost are recorded, and the run is marked
    unmatched so it cannot support a same-evidence claim.
"""
from __future__ import annotations

import time
from typing import Optional

from blackbox.evidence.interface import EvidenceBackend
from blackbox.investigator.common import (
    REPORT_SCHEMA, build_report, count_tokens, fit_records, load_prompt, render_incident, render_record,
)
from blackbox.model.adapter import ModelError, OllamaAdapter
from blackbox.schemas import EvidenceBudget, EvidenceRecord, RunConfig, SystemName, SystemOutput, Usage


def _dedupe(records: list[EvidenceRecord]) -> list[EvidenceRecord]:
    seen, out = set(), []
    for r in records:
        if r.evidence_id not in seen:
            seen.add(r.evidence_id)
            out.append(r)
    return out


def run_single_call(system: SystemName, backend: EvidenceBackend, config: RunConfig,
                    b1_evidence: Optional[list[EvidenceRecord]] = None,
                    adapter: Optional[OllamaAdapter] = None) -> SystemOutput:
    t0 = time.perf_counter()
    adapter = adapter or OllamaAdapter(config)
    incident = backend.incident()
    sys_text, version = load_prompt(config.baseline_prompt)

    if system == "b0":
        statuses = [s for s in backend.source_status() if s.source == "logs"]
        candidates = _dedupe(backend.export_logs(incident.window_start, incident.window_end))
        from_end = True      # keep the most recent lines before the cutoff, the window an engineer opens first
        label = "LOGS FROM THE INCIDENT WINDOW (oldest first)"
    elif system == "b1":
        if b1_evidence is None:
            raise ValueError("b1 needs the evidence_seen list of the initial BlackBox run")
        statuses = backend.source_status()
        candidates = _dedupe(b1_evidence)
        from_end = False     # drop trailing records in retrieval order
        label = "EVIDENCE RECORDS (in the order they were retrieved)"
    else:
        raise ValueError(f"not a single-call system: {system}")

    head = "INCIDENT\n" + render_incident(incident, statuses) + f"\n\n{label}\n"
    tail = "\n\nWrite the report. JSON only."
    reserved = config.reserve_output_tokens + max(config.reserve_instruction_tokens,
                                                  count_tokens(sys_text) + count_tokens(head) + count_tokens(tail) + 50)
    budget = config.num_ctx - reserved
    included, omitted, used, dropped = fit_records(candidates, budget, from_end=from_end)
    eb = EvidenceBudget(
        context_tokens=config.num_ctx, reserved_tokens=reserved, evidence_token_budget=budget,
        evidence_tokens_total=used + dropped, included_ids=[r.evidence_id for r in included],
        omitted_ids=[r.evidence_id for r in omitted], omitted_tokens=dropped,
        matched=(len(omitted) == 0) if system == "b1" else None,
    )
    body = "\n".join(render_record(r) for r in included) or "(no records)"
    if omitted:
        body += f"\n({len(omitted)} further records did not fit in the context window and are not shown)"
    user = head + body + tail

    usage = Usage()
    context = {"num_ctx": config.num_ctx, "num_predict": config.reserve_output_tokens,
               "prompt_tokens_est": count_tokens(sys_text) + count_tokens(user) + 20}
    error, report, status = None, None, "completed"
    try:
        reply = adapter.chat([{"role": "system", "content": sys_text}, {"role": "user", "content": user}],
                             schema=REPORT_SCHEMA, num_predict=config.reserve_output_tokens)
        usage.model_calls = 1
        usage.prompt_tokens, usage.completion_tokens = reply.prompt_tokens or 0, reply.completion_tokens or 0
        usage.model_seconds = reply.seconds
        context["prompt_tokens_actual"] = reply.prompt_tokens
        if reply.parsed is None:  # one retry, same as BlackBox
            reply = adapter.chat([{"role": "system", "content": sys_text},
                                  {"role": "user", "content": user + "\n\nYOUR PREVIOUS OUTPUT WAS REJECTED: "
                                   + (reply.parse_error or "invalid") + ". Reply with one JSON object."}],
                                 schema=REPORT_SCHEMA, num_predict=config.reserve_output_tokens)
            usage.model_calls += 1
            usage.prompt_tokens += reply.prompt_tokens or 0
            usage.completion_tokens += reply.completion_tokens or 0
            usage.model_seconds += reply.seconds
        if reply.parsed is None:
            status, error = "failed", f"unparseable report: {reply.parse_error}"
        else:
            chosen = reply.parsed.get("status")
            chosen = chosen if chosen in ("diagnosis", "insufficient_evidence", "no_fault_observed") else "insufficient_evidence"
            report = build_report(reply.parsed, incident.incident_id, system, chosen, {r.evidence_id for r in included})
    except ModelError as exc:
        status = "timeout" if "timeout" in str(exc) else "failed"
        error = str(exc)

    return SystemOutput(
        system=system, incident_id=incident.incident_id, ablation=backend.ablation, status=status, error=error,
        report=report, evidence_seen=included, usage=usage, wall_seconds=round(time.perf_counter() - t0, 2),
        evidence_budget=eb, context=context, config=config, prompt_versions={"single_call": version},
    )
