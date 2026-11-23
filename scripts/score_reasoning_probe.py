"""Automatic scoring of runs/reasoning_probe (read-only). The reviewer's manual scores are separate
(runs/reasoning_probe/_review.json); this script gives the provisional lexical verdicts only.

Per run:
- decision and top hypothesis (top_hypothesis id, else the first hypothesis if the model diagnosed);
- auto component / mechanism match of the top hypothesis (eval.matching, the same matcher as eval_v1);
- any-hypothesis match (is a correct hypothesis anywhere in the list?);
- rationale match: does the rationale text alone match an accepted mechanism (copy-to-ledger check);
- citations: ids cited per link that are not in the packet (invalid), and links left uncited.

Recovery of outputs cut at num_predict: the schema puts rationale last, so when the cut falls inside the
rationale string every structured field is complete. Such outputs are closed after the partial rationale and
parsed; they are marked "recovered". A cut anywhere earlier is left as a parse failure.

    python scripts/score_reasoning_probe.py [--md out.md]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.matching import accepted_components, accepted_mechanisms, component_matches, mechanism_matches  # noqa: E402
from blackbox.schemas import AnswerKey  # noqa: E402

RUNS = ROOT / "runs" / "reasoning_probe"
KEYS = ROOT / "evaluator_private" / "answer_keys"


def recover(o: dict):
    if o.get("parsed"):
        return o["parsed"], False
    raw = o.get("raw_output") or ""
    k = raw.rfind('"rationale": "')
    if k < 0:
        return None, False
    head, tail = raw[:k + len('"rationale": "')], raw[k + len('"rationale": "'):]
    tail = tail.replace("\\", "").replace('"', "'")
    try:
        return json.loads(head + tail + '"}'), True
    except ValueError:
        return None, False


def score(f: Path) -> dict:
    o = json.loads(f.read_text(encoding="utf-8"))
    d = f.parent
    ids = {r["evidence_id"] for r in json.loads((d / "packet.json").read_text(encoding="utf-8"))}
    key = AnswerKey.model_validate_json((KEYS / f"{o['incident']}.json").read_text(encoding="utf-8"))
    ac, am = accepted_components(key), accepted_mechanisms(key)
    p, recovered = recover(o)
    p = p or {}
    hs = p.get("hypotheses") or []
    top = next((h for h in hs if h.get("id") == p.get("top_hypothesis")), None)
    if top is None and p.get("decision") == "diagnosis" and hs:
        top = hs[0]

    def ok(h):
        return component_matches(h.get("component", ""), ac) and mechanism_matches(h.get("mechanism", ""), am)

    invalid, uncited = [], 0
    for h in hs:
        for link, v in (h.get("chain") or {}).items():
            e = v.get("evidence_ids") or []
            uncited += not e
            invalid += [i for i in e if i not in ids]
    return {"run": f"{d.name}_{f.stem}", "incident": o["incident"], "condition": o["condition"], "repeat": o["repeat"],
            "parsed": bool(p), "recovered": recovered, "decision": p.get("decision"),
            "top": f"{top.get('component')} :: {top.get('mechanism')}" if top else "",
            "top_component_ok": bool(top) and component_matches(top.get("component", ""), ac),
            "top_mechanism_ok": bool(top) and mechanism_matches(top.get("mechanism", ""), am),
            "any_hypothesis_ok": any(ok(h) for h in hs),
            "rationale_mechanism_ok": mechanism_matches(p.get("rationale", ""), am),
            "n_hypotheses": len(hs), "invalid_ids": invalid, "uncited_links": uncited,
            "prompt_tokens": o.get("prompt_tokens"), "completion_tokens": o.get("completion_tokens"),
            "seconds": o.get("seconds")}


def main():
    rows = [score(f) for f in sorted(RUNS.glob("INC-*/r*.json"))]
    cols = ["run", "recovered", "decision", "top_component_ok", "top_mechanism_ok", "any_hypothesis_ok", "rationale_mechanism_ok",
            "n_hypotheses", "invalid_ids", "uncited_links", "prompt_tokens", "completion_tokens", "seconds", "top"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    text = "\n".join(lines)
    print(text)
    (RUNS / "_auto_scores.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    if "--md" in sys.argv:
        Path(sys.argv[sys.argv.index("--md") + 1]).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
