"""Iterative BlackBox investigation: hypothesis ledger, bounded tool use, explicit stopping."""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from blackbox.evidence.interface import EvidenceBackend
from blackbox.investigator.common import (
    REPORT_SCHEMA, RUNTIME_SOURCES, build_report, check_ids, count_tokens,
    fit_text, load_prompt, render_incident, render_record, round_schema,
)
from blackbox.investigator import causal_gate
from blackbox.investigator.tools import ToolRunner, tool_catalog
from blackbox.model.adapter import ModelError, ModelReply, OllamaAdapter
from blackbox.schemas import (
    EvidenceRecord, FinalReport, Hypothesis, InvestigationStep, ReportStatus, RunConfig,
    SystemOutput, ToolRequest, ToolResult, Usage,
)

_CONF_RANK = {"high": 3, "medium": 2, "low": 1}


# Closing instruction of the rationale_first variant (experiment; off in every frozen config).
RECONCILE = (
    "First write the rationale. In it, state: (a) which retrieved change (deployment or commit), if any, could "
    "explain the symptom, with its effective deployment time and the component it changed; (b) when the symptom "
    "started; (c) the evidence ids that link the change to the symptom. If the evidence does not link any change "
    "to the symptom, write \"no causal link established\" and say what is missing. "
    "Then write the hypotheses so they match the rationale: add, update or reject hypotheses accordingly, and cite "
    "in each one the exact retrieved evidence ids it relies on. Then request tool calls or decide. JSON only.")


class Coordinator:
    def __init__(self, backend: EvidenceBackend, config: RunConfig, adapter: Optional[OllamaAdapter] = None,
                 on_event=None, should_cancel=None):
        self.should_cancel = should_cancel  # optional: checked only between rounds (a safe boundary)
        self.on_event = on_event          # optional observer: called with (type, payload); never changes behaviour
        self.backend = backend
        self.cfg = config
        self.adapter = adapter or OllamaAdapter(config)
        self.runner = ToolRunner(backend, config.tool_timeout_s)
        self.usage = Usage()
        self.seen: dict[str, EvidenceRecord] = {}          # retrieval order is insertion order
        self.ledger: list[Hypothesis] = []
        self.steps: list[InvestigationStep] = []
        self.calls_used = 0
        self.violations = 0
        self.unauthorized: list[ToolRequest] = []
        self.feedback: list[str] = []
        self.nb_stats: dict[str, Any] = {}
        self.report_context: dict[str, Any] = {}
        self.source_status: list = []
        self.last_round_ids: set[str] = set()
        self.last_gate_roles: dict = {}
        sys_text, self.v_round = load_prompt(config.round_prompt)
        self._cache: dict[str, ToolResult] = {}   # identical requests are answered from here
        self.sys_round = sys_text.format(max_calls_per_round=config.max_calls_per_round)
        self.sys_report, self.v_report = load_prompt(config.report_prompt)

    # ------------------------------------------------------------------ helpers
    def _emit(self, kind: str, payload: Any) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(kind, payload)
        except Exception:      # an observer must never break an investigation
            pass

    def _append(self, step: InvestigationStep) -> None:
        self.steps.append(step)
        self._emit("round", step.model_dump(mode="json"))

    def _chat(self, system: str, user: str, schema: dict[str, Any], num_predict: Optional[int] = None) -> ModelReply:
        reply = self.adapter.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                                  schema=schema, num_predict=num_predict)
        self.usage.model_calls += 1
        self.usage.prompt_tokens += reply.prompt_tokens or 0
        self.usage.completion_tokens += reply.completion_tokens or 0
        self.usage.model_seconds += reply.seconds
        return reply

    def _chat_json(self, system: str, user: str, schema: dict[str, Any],
                   num_predict: Optional[int] = None) -> tuple[ModelReply, int]:
        """One retry on malformed output. Returns the last reply and how many attempts were made."""
        reply = self._chat(system, user, schema, num_predict)
        if reply.parsed is not None:
            return reply, 1
        retry_user = (user + "\n\nYOUR PREVIOUS OUTPUT WAS REJECTED: " + (reply.parse_error or "invalid")
                      + ". Reply with a single JSON object matching the schema, and keep it short.")
        return self._chat(system, retry_user, schema, num_predict), 2

    def _top(self, ledger: Optional[list[Hypothesis]] = None) -> Optional[Hypothesis]:
        live = [h for h in (ledger if ledger is not None else self.ledger) if h.status != "rejected"]
        if not live:
            return None
        return max(live, key=lambda h: (h.status == "supported", _CONF_RANK.get(h.confidence, 0), len(h.supporting)))

    def _supports_diagnosis(self, h: Optional[Hypothesis]) -> tuple[bool, str]:
        if self.cfg.diagnosis_gate == "causal":
            unavailable = [s.source for s in self.source_status if not s.available]
            ok, why, self.last_gate_roles = causal_gate.check(h, self.seen, unavailable)
            return ok, why
        if h is None:
            return False, "no live hypothesis"
        runtime = [i for i in h.supporting if self.seen.get(i) and self.seen[i].source in RUNTIME_SOURCES]
        if len(h.supporting) < 2:
            return False, f"{h.hypothesis_id} cites {len(h.supporting)} retrieved evidence id(s); at least 2 are required"
        if not runtime:
            return False, f"{h.hypothesis_id} has no supporting runtime evidence (logs, traces, metrics, configs, deployments)"
        return True, f"{h.hypothesis_id} supported by {len(h.supporting)} ids ({len(runtime)} runtime)"

    def _normalize_ledger(self, raw: list[dict[str, Any]], normalized: Optional[list[str]] = None,
                          audit: Optional[list] = None) -> tuple[list[Hypothesis], list[str]]:
        seen_ids = set(self.seen)
        out, invalid, used = [], [], set()
        raw = [h for h in (raw if isinstance(raw, list) else []) if isinstance(h, dict)]   # QA-F04
        for n, h in enumerate(raw[:4], start=1):
            hid = str(h.get("id") or "").strip() or f"h{n}"
            if hid in used:
                hid = f"{hid}_{n}"
            used.add(hid)
            sup, bad1 = check_ids(h.get("supporting", []), seen_ids, normalized, audit, f"{hid}.supporting")
            con, bad2 = check_ids(h.get("contradicting", []), seen_ids, normalized, audit, f"{hid}.contradicting")
            invalid += bad1 + bad2
            status = h.get("status") if h.get("status") in ("active", "supported", "rejected") else "active"
            conf = h.get("confidence") if h.get("confidence") in _CONF_RANK else "low"
            out.append(Hypothesis(hypothesis_id=hid, component=str(h.get("component", "")).strip()[:120],
                                  mechanism=str(h.get("mechanism", "")).strip()[:240], status=status,
                                  confidence=conf, supporting=sup, contradicting=con,
                                  note=str(h.get("note", "")).strip()[:240]))
        return out, list(dict.fromkeys(invalid))

    @staticmethod
    def _diff(before: list[Hypothesis], after: list[Hypothesis]) -> list[str]:
        prev = {h.hypothesis_id: h for h in before}
        changes = []
        for h in after:
            p = prev.pop(h.hypothesis_id, None)
            if p is None:
                changes.append(f"+ {h.hypothesis_id} [{h.status}/{h.confidence}] {h.component}: {h.mechanism}")
                continue
            if p.status != h.status:
                changes.append(f"~ {h.hypothesis_id} status {p.status} -> {h.status}")
            if p.confidence != h.confidence:
                changes.append(f"~ {h.hypothesis_id} confidence {p.confidence} -> {h.confidence}")
            if (p.component, p.mechanism) != (h.component, h.mechanism):
                changes.append(f"~ {h.hypothesis_id} restated as {h.component}: {h.mechanism}")
            new_sup = [i for i in h.supporting if i not in p.supporting]
            new_con = [i for i in h.contradicting if i not in p.contradicting]
            if new_sup:
                changes.append(f"~ {h.hypothesis_id} new support {new_sup}")
            if new_con:
                changes.append(f"! {h.hypothesis_id} contradicted by {new_con}")
        for hid, p in prev.items():
            changes.append(f"- {hid} dropped without being marked rejected ({p.component}: {p.mechanism})")
        return changes

    def _notebook(self, budget: int) -> str:
        """Retrieved evidence: latest round and ledger-cited records in full, the rest as one line.

        Degrades the oldest full records to one line, then drops the oldest uncited lines,
        until the text fits. The decisions are kept in self.nb_stats for the run record.
        """
        cited = {i for h in self.ledger for i in h.supporting + h.contradicting}
        recs = list(self.seen.values())
        full = {r.evidence_id for r in recs if r.evidence_id in self.last_round_ids or r.evidence_id in cited}

        def render() -> str:
            return "\n".join(render_record(r, full=r.evidence_id in full) for r in recs)

        text = render()
        for r in recs:
            if count_tokens(text) <= budget:
                break
            if r.evidence_id in full and r.evidence_id not in self.last_round_ids:
                full.discard(r.evidence_id)
                text = render()
        while recs and count_tokens(text) > budget:
            drop = next((r for r in recs if r.evidence_id not in cited and r.evidence_id not in self.last_round_ids), None)
            recs.remove(drop if drop is not None else recs[0])
            text = render()
        omitted = len(self.seen) - len(recs)
        shown_full = sum(1 for r in recs if r.evidence_id in full)
        self.nb_stats = {"notebook_budget": budget, "notebook_tokens": count_tokens(text),
                         "records_retrieved": len(self.seen), "records_full": shown_full,
                         "records_one_line": len(recs) - shown_full, "records_omitted": omitted}
        return text + (f"\n({omitted} older records not shown for space; their ids remain citable)" if omitted else "")

    TOOL_SOURCE = {"search_logs": "logs", "search_traces": "traces", "get_trace": "traces",
                   "get_metrics": "metrics", "list_deployments": "deployments", "get_build": "builds",
                   "search_commits": "commits", "get_commit": "commits", "get_config": "configs",
                   "search_tickets": "tickets", "get_ticket": "tickets", "list_workflow_runs": "builds",
                   "get_workflow_run": "builds", "get_job_log": "builds", "list_run_artifacts": "builds"}

    def _unchecked_sources(self) -> list[str]:
        """Available sources with no executed call yet. Derived only from source_status and the
        call history: it names no record, service, time or cause."""
        queried = {self.TOOL_SOURCE.get(r.tool) for st in self.steps for r in st.tool_results
                   if r.status not in ("denied", "budget_exceeded", "invalid_args")}
        out = []
        for s in self.source_status:
            if s.available and s.source not in queried:
                tools = [t for t, src in self.TOOL_SOURCE.items() if src == s.source]
                out.append(f"{s.source} ({', '.join(tools)})")
        return out

    def _unchecked_change_tools(self, current=None) -> list[str]:
        """Change-history tools (deployments, commits) available in this run but not yet executed
        (including calls made in the current round)."""
        done = {r.tool for st in [*self.steps, *([current] if current else [])] for r in st.tool_results
                if r.status not in ("denied", "budget_exceeded", "invalid_args")}
        return [t for t in ("list_deployments", "search_commits") if t in self.runner.tools and t not in done]

    # ------------------------------------------------------------------ round prompt
    def _round_message(self, rnd: int, incident_text: str) -> str:
        left = self.cfg.max_tool_calls - self.calls_used
        ledger = json.dumps([h.model_dump(include={"hypothesis_id", "component", "mechanism", "status",
                                                    "confidence", "supporting", "contradicting"}) for h in self.ledger],
                            ensure_ascii=False) if self.ledger else "[] (none yet)"
        head = "\n\n".join([
            "INCIDENT\n" + incident_text,
            "TOOLS (read-only; arguments with ? are optional; times are ISO-8601 UTC)\n" + tool_catalog(tools=self.runner.tools)
            + (f'\nYou may request up to {self.cfg.max_calls_per_round} calls per round. Example tool_calls value: '
               '[{"tool": "list_deployments", "args": {}}, {"tool": "get_metrics", "args": {"service": "<a service above>"}}]'
               if self.cfg.multi_call_example else
               '\nExample tool_calls entry: {"tool": "search_logs", "args": {"service": "<a service above>", '
               '"level": "ERROR", "limit": 15}}'),
            f"BUDGET\nRound {rnd} of {self.cfg.max_rounds}. Tool calls used {self.calls_used} of "
            f"{self.cfg.max_tool_calls}; {left} left; at most {self.cfg.max_calls_per_round} this round."
            + ("\nNo tool calls remain: decide now." if left <= 0 else ""),
            "HYPOTHESES SO FAR\n" + ledger,
        ] + (["SOURCES NOT YET QUERIED\n" + ("; ".join(self._unchecked_sources()) or "none")]
             if self.cfg.coverage_hint else []))
        notes = "\n".join(self.feedback) if self.feedback else "none"
        tail = ("\n\nCOORDINATOR NOTES FROM LAST ROUND\n" + notes +
                ("\n\n" + RECONCILE if self.cfg.rationale_first else
                 "\n\nUpdate the hypotheses, then request tool calls or decide. JSON only."))
        limit = self.cfg.num_ctx - self.cfg.num_predict - 150
        fixed = count_tokens(self.sys_round) + count_tokens(head) + count_tokens(tail) + 60
        fixed_cut = False
        if fixed > limit - 200:   # an oversized fixed part is cut, never sent over the window (QA-F05)
            head, fixed_cut = fit_text(head, count_tokens(head) - (fixed - (limit - 200)))
            fixed = count_tokens(self.sys_round) + count_tokens(head) + count_tokens(tail) + 60
        budget = limit - fixed
        self.nb_stats = {}
        notebook = (self._notebook(budget) if budget > 0 else "(evidence omitted: no room left in the context window)") \
            if self.seen else "(nothing retrieved yet)"
        message = head + "\n\nEVIDENCE RETRIEVED SO FAR\n" + notebook + tail
        self.round_context = {"num_ctx": self.cfg.num_ctx, "num_predict": self.cfg.num_predict,
                              "fixed_tokens": fixed, "fixed_part_cut": fixed_cut, **self.nb_stats,
                              "prompt_tokens_est": count_tokens(self.sys_round) + count_tokens(message) + 20}
        return message

    # ------------------------------------------------------------------ main loop
    def run(self) -> SystemOutput:
        t0 = time.perf_counter()
        incident = self.backend.incident()
        self.source_status = self.backend.source_status()
        incident_text = render_incident(incident, self.source_status)
        stop, stop_reason, error = None, "", None
        no_progress = parse_failures = 0

        for rnd in range(1, self.cfg.max_rounds + 1):
            if self.should_cancel is not None and self.should_cancel():
                stop, stop_reason = "stop_cancelled", f"cancelled by the user before round {rnd}"
                if self.steps:
                    self.steps[-1].stop_decision, self.steps[-1].stop_reason = stop, stop_reason
                break
            if time.perf_counter() - t0 > self.cfg.run_timeout_s:
                stop, stop_reason = "stop_timeout", f"run exceeded {self.cfg.run_timeout_s:.0f}s"
                break
            step = InvestigationStep(round=rnd, hypotheses_before=[h.model_copy() for h in self.ledger])
            user = self._round_message(rnd, incident_text)
            self.feedback = []
            try:
                retrieved = bool(self.seen) or self.calls_used > 0
                left = self.cfg.max_tool_calls - self.calls_used
                schema = round_schema(retrieved, left, self.cfg.max_calls_per_round, self.cfg.rationale_first)
                if not retrieved and left > 0:
                    step.orchestration_rules.append("first-tool rule: no evidence yet, so >=1 tool call required and "
                                                    "decision limited to 'continue' (orchestration, not model behaviour)")
                if left <= 0:
                    step.orchestration_rules.append("budget spent: tool calls disabled, a decision is required")
                reply, attempts = self._chat_json(self.sys_round, user, schema)
            except ModelError as exc:
                step.parse_ok, step.parse_error = False, str(exc)
                step.stop_decision = "stop_timeout" if "timeout" in str(exc) else "stop_error"
                step.stop_reason = str(exc)
                self._append(step)
                stop, stop_reason, error = step.stop_decision, str(exc), str(exc)
                break
            step.raw_output = reply.text
            step.context = {**self.round_context, "prompt_tokens_actual": reply.prompt_tokens,
                            "unchecked_sources_shown": self._unchecked_sources() if self.cfg.coverage_hint else None,
                            "attempts": attempts,
                            "prompt_truncated_by_server": bool(reply.prompt_tokens and
                                                               reply.prompt_tokens + self.cfg.num_predict > self.cfg.num_ctx)}
            step.prompt_tokens, step.completion_tokens = reply.prompt_tokens, reply.completion_tokens
            step.model_seconds = reply.seconds
            if reply.parsed is None:
                parse_failures += 1
                step.parse_ok, step.parse_error = False, f"{reply.parse_error} (after {attempts} attempts)"
                step.hypotheses_after = step.hypotheses_before
                if parse_failures >= 2:
                    step.stop_decision, step.stop_reason = "stop_error", "two consecutive rounds of malformed output"
                    self._append(step)
                    stop, stop_reason = step.stop_decision, step.stop_reason
                    break
                self.feedback.append("Your last output could not be parsed; nothing was executed. Reply with valid JSON.")
                self._append(step)
                continue
            parse_failures = 0
            parsed = reply.parsed
            step.model_decision = str(parsed.get("decision", ""))

            new_ledger, invalid = self._normalize_ledger(parsed.get("hypotheses") or [], step.normalized_citations,
                                                         step.citation_audit)
            step.invalid_citations = invalid
            if invalid:
                self.feedback.append(f"These cited ids were never retrieved and were removed: {invalid[:8]}")

            # ---- tool calls (allowlist, budget and per-round cap enforced here)
            self.last_round_ids = set()
            requested = parsed.get("tool_calls") or []
            if not isinstance(requested, list):                  # QA-F04
                self.feedback.append("tool_calls must be a list of {tool, args} objects; nothing was executed.")
                requested = []
            for n, tc in enumerate(requested, start=1):
                if not isinstance(tc, dict):
                    tc = {"tool": str(tc), "args": None, "_malformed": True}
                tool = str(tc.get("tool", "")).strip()
                raw_args = tc.get("args", {})
                args = raw_args if isinstance(raw_args, dict) else {}
                req = ToolRequest(call_id=f"r{rnd}c{n}", round=rnd, tool=tool, args=args)
                step.tool_requests.append(req)
                if tc.get("_malformed") or not isinstance(raw_args, dict):
                    # never execute with arguments the model did not give (QA-F04)
                    res = ToolResult(call_id=req.call_id, tool=tool, status="invalid_args",
                                     error=f"args must be a JSON object, got {type(raw_args).__name__}")
                    if n <= self.cfg.max_calls_per_round and self.calls_used < self.cfg.max_tool_calls:
                        self.calls_used += 1
                    step.tool_results.append(res)
                    self.feedback.append(f"{req.call_id} {tool} -> invalid_args: {res.error}")
                    continue
                if not self.runner.is_allowed(tool):
                    self.unauthorized.append(req)
                    res = ToolResult(call_id=req.call_id, tool=tool, status="denied",
                                     error=f"'{tool}' is not an available tool; request refused")
                elif n > self.cfg.max_calls_per_round or self.calls_used >= self.cfg.max_tool_calls:
                    self.violations += 1
                    res = ToolResult(call_id=req.call_id, tool=tool, status="budget_exceeded",
                                     error="tool-call limit reached; call not executed")
                elif self.cfg.refuse_repeats and f"{tool}|{json.dumps(args, sort_keys=True)}" in self._cache:
                    prior = self._cache[f"{tool}|{json.dumps(args, sort_keys=True)}"]
                    res = ToolResult(call_id=req.call_id, tool=tool, status="invalid_args",
                                     error=f"identical to {prior.call_id}; refused without using budget. "
                                           "Use different arguments or another tool.")
                    step.orchestration_rules.append(f"repeat refused: {req.call_id} == {prior.call_id}")
                else:
                    key = f"{tool}|{json.dumps(args, sort_keys=True)}"
                    if key in self._cache:     # same tool, same arguments: serve the earlier answer
                        prior = self._cache[key]
                        res = prior.model_copy(update={"call_id": req.call_id, "elapsed_ms": 0.0})
                        step.duplicate_calls.append(f"{req.call_id}=={prior.call_id}")
                    else:
                        res = self.runner.run(req)
                        if res.status in ("ok", "empty"):
                            self._cache[key] = res
                    self.calls_used += 1
                    for rec in res.records:
                        if rec.evidence_id not in self.seen:
                            self.seen[rec.evidence_id] = rec
                        self.last_round_ids.add(rec.evidence_id)
                step.tool_results.append(res)
                if step.duplicate_calls and step.duplicate_calls[-1].startswith(req.call_id + "=="):
                    self.feedback.append(f"{req.call_id} {tool} repeated an earlier identical call "
                                         f"({step.duplicate_calls[-1].split('==')[1]}): no new evidence. Change the arguments or decide.")
                elif res.status not in ("ok",):
                    self.feedback.append(f"{req.call_id} {tool}{json.dumps(args)[:120]} -> {res.status}"
                                         + (f": {res.error}" if res.error else ""))
                else:
                    self.feedback.append(f"{req.call_id} {tool} -> {len(res.records)} records"
                                         + (f" of {res.total_matches} (truncated)" if res.truncated else ""))

            self.ledger = new_ledger
            step.hypotheses_after = [h.model_copy() for h in new_ledger]
            step.hypothesis_changes = self._diff(step.hypotheses_before, new_ledger)
            executed = sum(1 for r in step.tool_results if r.status not in ("denied", "budget_exceeded"))

            # ---- stopping
            decision = step.model_decision
            missing = self._unchecked_change_tools(step) if self.cfg.change_check else []
            if decision in ("diagnose", "insufficient_evidence", "no_fault") and missing and rnd < self.cfg.max_rounds:
                why = f"no call yet to {', '.join(missing)}"
                step.stop_reason = f"{decision} refused by change check: {why}"
                step.orchestration_rules.append(f"change check: {why}")
                self.feedback.append(f"Conclusion refused: before concluding, check what changed. Call "
                                     f"{' and '.join(missing)} (no arguments needed) and compare with the symptom times.")
                decision = "continue"
            if decision == "diagnose":
                ok, why = self._supports_diagnosis(self._top())
                if self.cfg.diagnosis_gate == "causal":
                    step.orchestration_rules.append(f"causal gate roles: {self.last_gate_roles}")
                if ok:
                    step.stop_decision, step.stop_reason = "stop_diagnosis", why
                else:
                    step.stop_reason = f"diagnosis refused: {why}"
                    step.orchestration_rules.append(f"diagnosis gate: {why}")
                    if self.cfg.diagnosis_gate == "causal":
                        self.feedback.append(f"Diagnosis refused: {why}. Gather or cite that evidence first.")
                    else:
                        self.feedback.append(f"Diagnosis refused: {why}. Gather or cite runtime evidence first.")
            elif decision == "insufficient_evidence":
                if self.calls_used > 0:
                    step.stop_decision, step.stop_reason = "stop_insufficient", "model judged evidence insufficient"
                else:
                    step.stop_reason = "insufficient_evidence refused before any evidence was retrieved"
                    self.feedback.append("You declared insufficient evidence before retrieving any. Investigate first.")
            elif decision == "no_fault":
                if any(r.source in RUNTIME_SOURCES for r in self.seen.values()):
                    step.stop_decision, step.stop_reason = "stop_no_fault", "model judged the system healthy"
                else:
                    step.stop_reason = "no_fault refused: no runtime evidence retrieved"
                    self.feedback.append("You declared no fault without checking runtime evidence.")
            if step.stop_decision == "continue":
                no_progress = no_progress + 1 if executed == 0 else 0
                if self.calls_used >= self.cfg.max_tool_calls and rnd < self.cfg.max_rounds and executed == 0 and decision == "continue":
                    step.stop_decision, step.stop_reason = "stop_budget", "tool budget exhausted and no decision made"
                elif no_progress >= 2:
                    step.stop_decision, step.stop_reason = "stop_rounds", "two rounds without executing a tool or deciding"
            self._append(step)
            if step.stop_decision != "continue":
                stop, stop_reason = step.stop_decision, step.stop_reason
                break

        if stop is None:
            stop, stop_reason = ("stop_budget" if self.calls_used >= self.cfg.max_tool_calls else "stop_rounds",
                                 f"limit reached after {len(self.steps)} rounds and {self.calls_used} tool calls")
            if self.steps:
                self.steps[-1].stop_decision, self.steps[-1].stop_reason = stop, stop_reason

        if stop == "stop_cancelled":   # no report call: a cancelled run is not a conclusion
            self.runner.close()
            return SystemOutput(
                system="blackbox", incident_id=incident.incident_id, ablation=self.backend.ablation,
                status="cancelled", error=stop_reason, report=None, steps=self.steps,
                evidence_seen=list(self.seen.values()), usage=self.usage,
                wall_seconds=round(time.perf_counter() - t0, 2), rounds_used=len(self.steps),
                tool_calls_used=self.calls_used, budget_violations=self.violations,
                unauthorized_attempts=self.unauthorized, unauthorized_executed=0, config=self.cfg,
                prompt_versions={"round": self.v_round, "report": self.v_report})
        if stop == "stop_error":
            # the model never decided; do not turn its unfinished ledger into a diagnosis (QA-F02)
            report, report_error = self._ledger_report(incident.incident_id, "failed"), None
        else:
            status = self._final_status(stop)
            report, report_error = self._write_report(incident_text, incident.incident_id, status, stop, stop_reason)
        self.runner.close()
        if report is not None:
            self._emit("report", report.model_dump(mode="json"))
        failed = report is None or stop == "stop_error" or (stop == "stop_timeout" and not self.steps)
        out_status = "timeout" if stop == "stop_timeout" and report is None else ("failed" if failed else "completed")
        return SystemOutput(
            system="blackbox", incident_id=incident.incident_id, ablation=self.backend.ablation,
            status=out_status, error=error or report_error, report=report, steps=self.steps,
            evidence_seen=list(self.seen.values()), usage=self.usage,
            wall_seconds=round(time.perf_counter() - t0, 2), rounds_used=len(self.steps),
            tool_calls_used=self.calls_used, budget_violations=self.violations,
            context={"report_call": self.report_context},
            unauthorized_attempts=self.unauthorized, unauthorized_executed=0, config=self.cfg,
            prompt_versions={"round": self.v_round, "report": self.v_report},
        )

    def _final_status(self, stop: str) -> ReportStatus:
        if stop == "stop_diagnosis":
            return "diagnosis"
        if stop == "stop_no_fault":
            return "no_fault_observed"
        if stop == "stop_insufficient":
            return "insufficient_evidence"
        ok, _ = self._supports_diagnosis(self._top())
        return "diagnosis" if ok else "insufficient_evidence"

    def _write_report(self, incident_text: str, incident_id: str, status: ReportStatus, stop: str,
                      stop_reason: str) -> tuple[Optional[FinalReport], Optional[str]]:
        ledger = json.dumps([h.model_dump(include={"hypothesis_id", "component", "mechanism", "status", "confidence",
                                                    "supporting", "contradicting", "note"}) for h in self.ledger],
                            ensure_ascii=False)
        head = (f"INCIDENT\n{incident_text}\n\nDECIDED STATUS: {status} (investigation stopped: {stop}; {stop_reason})"
                f"\n\nFINAL HYPOTHESES\n{ledger}")
        tail = "\n\nWrite the final report for the decided status. JSON only."
        limit = self.cfg.num_ctx - self.cfg.num_predict - 250
        if count_tokens(self.sys_report) + count_tokens(head) > limit - 200:     # QA-F05
            head, _ = fit_text(head, limit - 200 - count_tokens(self.sys_report))
        budget = limit - count_tokens(self.sys_report) - count_tokens(head)
        self.last_round_ids = set()
        notebook = (self._notebook(budget) if budget > 0 else "(evidence omitted: no room left in the context window)") \
            if self.seen else "(no evidence was retrieved)"
        user = head + "\n\nEVIDENCE\n" + notebook + tail
        self.report_context = {"num_ctx": self.cfg.num_ctx, **self.nb_stats,
                               "prompt_tokens_est": count_tokens(self.sys_report) + count_tokens(user) + 20}
        try:
            reply, _ = self._chat_json(self.sys_report, user, REPORT_SCHEMA)
            self.report_context["prompt_tokens_actual"] = reply.prompt_tokens
        except ModelError as exc:
            if status == "diagnosis" or self.ledger:
                return self._ledger_report(incident_id, status), f"report call failed ({exc}); report derived from ledger"
            return None, f"report call failed: {exc}"
        if reply.parsed is None:
            return self._ledger_report(incident_id, status), f"report output unparseable ({reply.parse_error}); report derived from ledger"
        report = build_report(reply.parsed, incident_id, "blackbox", status, set(self.seen))
        if status == "diagnosis" and report.status != "diagnosis":  # model returned no usable cause
            return self._ledger_report(incident_id, status), "report had no cause; ranked causes derived from ledger"
        return report, None

    def _ledger_report(self, incident_id: str, status: ReportStatus) -> FinalReport:
        live = sorted([h for h in self.ledger if h.status != "rejected"],
                      key=lambda h: (h.status == "supported", _CONF_RANK.get(h.confidence, 0)), reverse=True)
        parsed = {"ranked_causes": [{"component": h.component, "mechanism": h.mechanism, "confidence": h.confidence,
                                     "evidence_ids": h.supporting} for h in live[:3]] if status == "diagnosis" else [],
                  "claims": [{"text": f"{h.component}: {h.mechanism}", "kind": "tentative", "evidence_ids": h.supporting}
                             for h in live[:3]] if status != "diagnosis" else [],
                  "unresolved_questions": [], "next_check": ""}
        return build_report(parsed, incident_id, "blackbox", status, set(self.seen))
