"""Development options for the coordinator (both off by default and off in every frozen config).

change_expansion: when a round retrieves deployment records, the coordinator itself follows each deployment of an
  incident service inside the incident window: its build, its commit with the diff, and the metric list of that
  service. Each fetch is an ordinary read-only tool call, labelled `auto` in the trace and logged as an
  orchestration rule; it never adds anything the backend would not return to the model.

synthesis: when the round loop ends, the model writes its hypotheses again FROM SCRATCH over every retrieved record
  (the reasoning-probe format: component, mechanism, cause -> runtime effect -> symptom with ids per link), instead
  of reusing the round-by-round ledger. The synthesized top hypothesis replaces the ledger and must still pass the
  configured diagnosis gate; otherwise the outcome is insufficient evidence.
"""
from __future__ import annotations

from typing import Any

PROMPT = """You are an incident investigator. You are given one incident and the evidence records retrieved during the
investigation. Each record starts with its id in square brackets. Some records may be unrelated to the incident.

Propose up to 3 candidate root-cause hypotheses, written from scratch from the evidence (ignore any earlier guesses).
For each one give:
- component: the service, and the part of it if the evidence shows it.
- mechanism: concretely how the fault works.
- chain: three links, each with a short statement and the ids of the records that directly support that link:
  cause (the underlying fault or change), runtime_effect (what it does while the system runs), symptom (the alerted symptom).
- contradicting: ids of records that argue against the hypothesis.

Cite only ids that appear in the evidence. Do not cite a record for a link it does not show.
Then decide: "diagnosis" if the evidence supports one hypothesis through every link of its chain, otherwise
"insufficient_evidence". Put the id of the diagnosed hypothesis in top_hypothesis (null when you abstain).
Finish with a short rationale. Reply with JSON only."""

_LINK = {"type": "object", "properties": {"statement": {"type": "string"},
                                          "evidence_ids": {"type": "array", "items": {"type": "string"}}},
         "required": ["statement", "evidence_ids"]}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "hypotheses": {"type": "array", "maxItems": 3, "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "component": {"type": "string"}, "mechanism": {"type": "string"},
                           "chain": {"type": "object", "properties": {"cause": _LINK, "runtime_effect": _LINK,
                                                                      "symptom": _LINK},
                                     "required": ["cause", "runtime_effect", "symptom"]},
                           "contradicting": {"type": "array", "items": {"type": "string"}}},
            "required": ["id", "component", "mechanism", "chain", "contradicting"]}},
        "decision": {"type": "string", "enum": ["diagnosis", "insufficient_evidence"]},
        "top_hypothesis": {"type": ["string", "null"]},
        "rationale": {"type": "string"},
    },
    "required": ["hypotheses", "decision", "top_hypothesis", "rationale"],
}


OUTPUT_TOKENS = 1600


def recover(raw: str) -> dict | None:
    """An output cut after the hypotheses array: keep the complete hypotheses and the decision, if present.
    Nothing is invented: a missing decision means no recovery; a missing top_hypothesis stays null."""
    import json
    import re
    m = re.search(r'\]\s*,\s*"decision"\s*:\s*"(diagnosis|insufficient_evidence)"', raw or "")
    if not m:
        return None
    try:
        obj = json.loads(raw[: m.start() + 1] + "}")
    except ValueError:
        return None
    obj.update({"decision": m.group(1), "top_hypothesis": None, "rationale": "(cut at the output limit)"})
    return obj


def chain_ids(h: dict) -> list[str]:
    out: list[str] = []
    for link in (h.get("chain") or {}).values():
        for i in (link or {}).get("evidence_ids") or []:
            if i not in out:
                out.append(str(i))
    return out
