"""BlackBox as a chat assistant: questions about one incident, answered from evidence retrieved with tools,
and every answer checked against the evidence it cites.

A ChatSession holds one incident (a live-stack incident or a snapshot incident), the conversation, and the records
retrieved so far. Each user turn runs at most MAX_ROUNDS model calls; a call either requests read-only tools
(the same ToolRunner the investigator uses) or answers. The answer is then verified:
  - every cited id must be a record retrieved in this conversation (unknown ids are listed);
  - concrete tokens in the answer (ids, versions, numbers with units, hashes, durations) must appear in at least
    one cited record; tokens that do not are listed as unsupported;
  - an answer that makes claims but cites nothing is marked ungrounded.
The verdict ("verified", "partly verified", "not verified", "no evidence") is shown with the answer.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from blackbox.investigator.common import load_prompt, render_incident, render_record
from blackbox.investigator.tools import ToolRunner, tool_catalog
from blackbox.model.adapter import OllamaAdapter
from blackbox.schemas import EvidenceRecord, RunConfig, ToolRequest

MAX_ROUNDS = 4
MAX_CALLS = 3
NOTEBOOK_CHARS = 14000
HISTORY_TURNS = 6
SCHEMA = {
    "type": "object",
    "properties": {
        "tool_calls": {"type": "array", "maxItems": MAX_CALLS, "items": {
            "type": "object", "properties": {"tool": {"type": "string"}, "args": {"type": "object"}},
            "required": ["tool", "args"]}},
        "answer": {"type": ["string", "null"]},
        "citations": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["tool_calls", "answer"],
}
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "citations": {"type": "array", "items": {"type": "string"}},
                   "confidence": {"type": "string", "enum": ["high", "medium", "low"]}},
    "required": ["answer", "citations"],
}
# concrete things an answer can assert that must be traceable to cited evidence
_TOKEN = re.compile(r"\b(?:[a-z]+:[A-Za-z0-9_.#@/-]+|d-\d+|b-\d+|v?\d+\.\d+\.\d+|[0-9a-f]{7,40}|\d+(?:\.\d+)?\s?(?:ms|s|%|rps))\b")


@dataclass
class Turn:
    role: str                      # user | assistant
    text: str
    at: float = field(default_factory=time.time)
    citations: list[str] = field(default_factory=list)
    confidence: Optional[str] = None
    verification: Optional[dict] = None
    tools: list[dict] = field(default_factory=list)
    seconds: float = 0.0


# "no X" claims are only checkable if a tool for X was called in the conversation
ABSENCE_CHECKS = [
    ("deployments", "deployments?|deploys?|releases?", {"list_deployments", "get_build"}),
    ("commits", "commits?|code changes?|changes to the code", {"search_commits", "get_commit"}),
    ("traces", "traces?|spans?", {"search_traces", "get_trace"}),
    ("metrics", "metrics?", {"get_metrics"}),
    ("logs", "logs?|log lines?", {"search_logs"}),
]


def verify(answer: str, citations: list[str], evidence: dict[str, EvidenceRecord],
           queried: set[str] | None = None) -> dict:
    queried = queried or set()
    cited = [c for c in dict.fromkeys(citations or [])]
    unknown = [c for c in cited if c not in evidence]
    known = [evidence[c] for c in cited if c in evidence]
    def norm(x: str) -> str:
        return re.sub(r"[\s\"'`]", "", x.lower())
    hay = norm(" ".join(r.evidence_id + " " + r.title + " " + r.content for r in known))
    tokens = sorted({t for t in _TOKEN.findall(answer or "")})
    unsupported = [t for t in tokens if norm(t) not in hay]
    no_info = bool(re.search(r"(?i)\b(not enough|insufficient|cannot|can't|could not|couldn't|no evidence|unknown|not found)\b",
                             answer or ""))
    unqueried = []
    for source, words, tools in ABSENCE_CHECKS:
        pattern = r"(?i)\bno (recent )?(%s)\b|\b(%s)\b[^.]*\b(not visible|none|not found|no sign)" % (words, words)
        if re.search(pattern, answer or "") and not (queried & tools):
            unqueried.append(source)
    if not known and not tokens and no_info:
        verdict = "no evidence"          # an honest 'I could not find it'
    elif not known:
        verdict = "not verified"
    elif unknown or unsupported or unqueried:
        verdict = "partly verified"
    else:
        verdict = "verified"
    return {"verdict": verdict, "cited": len(cited), "cited_known": len(known), "unknown_ids": unknown,
            "checked_tokens": tokens, "unsupported_tokens": unsupported, "unqueried_absence_claims": unqueried}


class ChatSession:
    def __init__(self, backend, config: RunConfig, adapter: Optional[OllamaAdapter] = None, title: str = "",
                 follow_changes: bool = True):
        self.id = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
        self.backend = backend
        self.cfg = config
        self.adapter = adapter or OllamaAdapter(config)
        self.runner = ToolRunner(backend, config.tool_timeout_s)
        self.evidence: dict[str, EvidenceRecord] = {}
        self.turns: list[Turn] = []
        self.title = title
        text, self.prompt_version = load_prompt("chat_v1")
        self.system = text.format(max_calls=MAX_CALLS, tools=tool_catalog(tools=self.runner.tools))
        self._calls: dict[str, int] = {}
        self.follow_changes = follow_changes
        self._followed: set[str] = set()

    def _follow_changes(self, records, rnd: int, tools_used: list, observations: list) -> None:
        """Like the investigator's change expansion: a deployment inside the incident window is followed to its
        build and commit diff (read-only calls, marked 'auto' in the tool list)."""
        inc = self.backend.incident()
        deps = [r for r in records if r.source == "deployments" and r.service in inc.services
                and inc.window_start <= (r.event_time or "") <= inc.investigation_cutoff and r.evidence_id not in self._followed]
        for d in sorted(deps, key=lambda r: r.event_time or "", reverse=True)[:2]:
            self._followed.add(d.evidence_id)
            a = d.attributes or {}
            for tool, args in (("get_build", {"build_id": a.get("build_id")}),
                               ("get_commit", {"sha": a.get("commit_full") or a.get("commit")})):
                if not list(args.values())[0]:
                    continue
                res = self.runner.run(ToolRequest(call_id=f"auto{len(self._followed)}", round=rnd, tool=tool, args=args))
                for r in res.records:
                    self.evidence[r.evidence_id] = r if len(r.content or "") >= len(self.evidence.get(r.evidence_id, r).content or "")                         else self.evidence[r.evidence_id]
                tools_used.append({"tool": tool, "args": args, "status": res.status, "records": len(res.records),
                                   "error": res.error, "auto": True, "ids": [r.evidence_id for r in res.records],
                                   "backends": sorted({str(r.attributes.get("backend")) for r in res.records
                                                       if r.attributes.get("backend")})})
                observations.append(f"(auto, follows {d.evidence_id}) {tool} -> {res.status}, {len(res.records)} records")

    def close(self):
        self.runner.close()

    # ------------------------------------------------------------------ prompt
    def _notebook(self) -> str:
        out, used = [], 0
        for r in reversed(list(self.evidence.values())):
            block = render_record(r, full=True)
            if used + len(block) > NOTEBOOK_CHARS:
                out.append(f"... {len(self.evidence) - len(out)} older records not shown")
                break
            out.append(block)
            used += len(block)
        return "\n\n".join(reversed(out)) or "(nothing retrieved yet)"

    def _user_prompt(self, question: str, observations: list[str]) -> str:
        hist = []
        for t in self.turns[-HISTORY_TURNS * 2:]:
            hist.append(f"{t.role.upper()}: {t.text}" + (f"  [cited: {', '.join(t.citations)}]" if t.citations else ""))
        return (f"INCIDENT\n{render_incident(self.backend.incident(), self.backend.source_status())}\n\n"
                f"EVIDENCE RETRIEVED SO FAR\n{self._notebook()}\n\n"
                f"CONVERSATION\n" + ("\n".join(hist) or "(new conversation)") +
                f"\n\nQUESTION\n{question}\n" +
                ("\nTOOL RESULTS THIS TURN\n" + "\n".join(observations) if observations else "") +
                "\n\nReply with the JSON object.")

    # ------------------------------------------------------------------ one turn
    def ask(self, question: str) -> Turn:
        t0 = time.time()
        question = question.strip()[:2000]
        tools_used: list[dict] = []
        observations: list[str] = []
        answer, cites, conf = None, [], None
        for rnd in range(1, MAX_ROUNDS + 1):
            reply = self.adapter.chat([{"role": "system", "content": self.system},
                                       {"role": "user", "content": self._user_prompt(question, observations)}],
                                      schema=SCHEMA, num_predict=self.cfg.num_predict)
            p = reply.parsed or {}
            calls = [c for c in (p.get("tool_calls") or []) if isinstance(c, dict)][:MAX_CALLS]
            if p.get("answer") and (not calls or rnd == MAX_ROUNDS):
                answer, cites, conf = str(p["answer"]), [str(c) for c in p.get("citations") or []], p.get("confidence")
                break
            if not calls:
                observations.append("(no tool call and no answer; answer now from the evidence above)")
                continue
            for n, c in enumerate(calls, start=1):
                key = json.dumps([c.get("tool"), c.get("args")], sort_keys=True)
                req = ToolRequest(call_id=f"q{len(self.turns)}r{rnd}c{n}", round=rnd, tool=str(c.get("tool")),
                                  args=c.get("args") if isinstance(c.get("args"), dict) else {})
                if key in self._calls:
                    observations.append(f"{req.tool}: identical to an earlier call; its records are already above")
                    continue
                self._calls[key] = 1
                res = self.runner.run(req)
                for r in res.records:
                    self.evidence.setdefault(r.evidence_id, r)
                backends = sorted({str(r.attributes.get("backend")) for r in res.records if r.attributes.get("backend")})
                tools_used.append({"tool": req.tool, "args": req.args, "status": res.status, "records": len(res.records),
                                   "error": res.error, "backends": backends,
                                   "ids": [r.evidence_id for r in res.records][:20]})
                observations.append(f"{req.tool}({json.dumps(req.args)[:160]}) -> {res.status}, {len(res.records)} records"
                                    + (f": {res.error}" if res.error else ""))
                if self.follow_changes:
                    self._follow_changes(res.records, rnd, tools_used, observations)
        if answer is None:      # tool budget spent: one answer-only call over what was retrieved
            reply = self.adapter.chat(
                [{"role": "system", "content": self.system},
                 {"role": "user", "content": self._user_prompt(question, observations + [
                     "No more tool calls are possible. Answer now from the evidence above, citing ids, or say what is missing."])}],
                schema=ANSWER_SCHEMA, num_predict=self.cfg.num_predict)
            p = reply.parsed or {}
            if p.get("answer"):
                answer, cites, conf = str(p["answer"]), [str(c) for c in p.get("citations") or []], p.get("confidence")
        if answer is None:
            answer = "I could not reach an answer within the tool budget for this question."
        user = Turn("user", question)
        bot = Turn("assistant", answer, citations=cites, confidence=conf, tools=tools_used,
                   verification=verify(answer, cites, self.evidence, self._queried_tools()),
                   seconds=round(time.time() - t0, 1))
        self.turns += [user, bot]
        return bot

    def _queried_tools(self) -> set[str]:
        return {x["tool"] for t in self.turns for x in t.tools if x.get("status") in ("ok", "empty")} |                {json.loads(k)[0] for k in self._calls}

    def to_dict(self) -> dict[str, Any]:
        inc = self.backend.incident()
        return {"session_id": self.id, "title": self.title, "incident": inc.model_dump(mode="json"),
                "mode": getattr(self.backend, "mode", "snapshot"), "prompt_version": self.prompt_version,
                "turns": [t.__dict__ for t in self.turns],
                "evidence": {k: v.model_dump(mode="json") for k, v in self.evidence.items()}}
