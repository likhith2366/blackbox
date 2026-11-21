"""Reconstruct the exact round prompts of a saved run: replay its recorded model outputs (no model call)
through the same coordinator, config and snapshot, and check the replay reproduces the saved tool results.
Writes prompts to <out>/<run>_call<N>.txt. Read-only for the repo.

    python scripts/replay_prompts.py runs/dev_v2_experiment/INC-9001_v2 <out_dir>
"""
import json
import sys
from pathlib import Path

REPO = Path(r"A:\subjects\genAI\final_project\blackbox")
sys.path.insert(0, str(REPO))
from blackbox.evidence.backend import load_backend          # noqa: E402
from blackbox.investigator.coordinator import Coordinator   # noqa: E402
from blackbox.model.adapter import ModelReply, parse_json_object  # noqa: E402
from blackbox.schemas import RunConfig                      # noqa: E402


class Replay:
    def __init__(self, outputs):
        self.outputs, self.prompts, self.systems = list(outputs), [], []

    def chat(self, messages, schema=None, num_predict=None):
        self.systems.append(messages[0]["content"])
        self.prompts.append(messages[-1]["content"])
        text = self.outputs.pop(0)
        parsed, err = parse_json_object(text)
        return ModelReply(text=text, parsed=parsed, parse_error=err, prompt_tokens=0, completion_tokens=0, seconds=0)


def main(run_dir: str, out: str):
    run = Path(run_dir)
    o = json.loads((run / "output.json").read_text(encoding="utf-8"))
    cfg = RunConfig(**{k: v for k, v in o["config"].items() if k in RunConfig.model_fields})
    raws = [s["raw_output"] for s in o["steps"]] + [json.dumps(o["report"] or {})]
    a = Replay(raws)
    c = Coordinator(load_backend(o["incident_id"]), cfg, adapter=a)
    r = c.run()
    same = [[(t.tool, t.status, [x.evidence_id for x in t.records]) for t in s.tool_results] for s in r.steps] == \
           [[(t["tool"], t["status"], [x["evidence_id"] for x in t.get("records", [])]) for t in s["tool_results"]] for s in o["steps"]]
    Path(out).mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(a.prompts, 1):
        (Path(out) / f"{run.name}_call{i}.txt").write_text(p, encoding="utf-8")
    (Path(out) / f"{run.name}_system.txt").write_text(a.systems[0], encoding="utf-8")
    print(run.name, "replay reproduces saved tool results:", same, "| prompts:", len(a.prompts))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
