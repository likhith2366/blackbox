"""Preflight: one synthesis-sized model call (dev_v3e options, current prompt) on saved evidence; no incident.
Samples the GPU every 2 s. Writes a JSON summary to argv[1]."""
import json, subprocess, sys, threading, time
sys.path.insert(0, "A:/subjects/genAI/final_project/blackbox")
from blackbox.cli import load_config
from blackbox.investigator import synthesis as synth
from blackbox.investigator.common import render_incident, render_record
from blackbox.model.adapter import OllamaAdapter, ModelError
from blackbox.schemas import EvidenceRecord, Incident

cfg = load_config("A:/subjects/genAI/final_project/blackbox/configs/dev_v3e.yaml")
run = "A:/subjects/genAI/final_project/blackbox/runs/live_faults_windowfix/retry-policy"
inc = Incident.model_validate(json.load(open(f"{run}/incident.json", encoding="utf-8")))
o = json.load(open(f"{run}/output.json", encoding="utf-8"))
recs = [EvidenceRecord.model_validate(r) for r in o["evidence_seen"]]
code = {"commits", "configs", "builds"}
ev = "\n".join(render_record(r, full=r.source in code, max_chars=5000 if r.source in code else 600) for r in recs)
user = f"INCIDENT\n{render_incident(inc)}\n\nEVIDENCE\n{ev}\n\nReply with the JSON object."

samples, stop = [], threading.Event()
def sample():
    while not stop.is_set():
        out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,clocks.sm,"
                              "clocks_throttle_reasons.active,clocks_throttle_reasons.hw_thermal_slowdown,"
                              "clocks_throttle_reasons.sw_thermal_slowdown", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True).stdout.strip()
        samples.append([time.time()] + [x.strip() for x in out.split(",")])
        stop.wait(2)
th = threading.Thread(target=sample, daemon=True); th.start()
t0 = time.time(); res = {}
try:
    rep = OllamaAdapter(cfg).chat([{"role": "system", "content": synth.PROMPT}, {"role": "user", "content": user}],
                                  schema=synth.SCHEMA, num_predict=max(cfg.num_predict, synth.OUTPUT_TOKENS))
    res = {"status": "completed", "seconds": round(rep.seconds, 1), "prompt_tokens": rep.prompt_tokens,
           "completion_tokens": rep.completion_tokens, "parsed": rep.parsed is not None}
except ModelError as e:
    res = {"status": "model_error", "error": str(e), "seconds": round(time.time() - t0, 1)}
stop.set(); th.join()
temps = [int(s[1]) for s in samples if s[1].isdigit()]
res.update({"purpose": "preflight only, no incident; evidence from runs/live_faults_windowfix/retry-policy",
            "prompt_version": synth.PROMPT_VERSION, "model_timeout_s": cfg.model_timeout_s,
            "temp_start": temps[0] if temps else None, "temp_max": max(temps) if temps else None,
            "thermal_slowdown_samples": sum(1 for s in samples if "Active" in s[6:8]),
            "samples": samples})
json.dump(res, open(sys.argv[1], "w"), indent=1)
print({k: v for k, v in res.items() if k != "samples"})
