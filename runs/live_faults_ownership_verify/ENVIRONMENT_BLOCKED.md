# Ownership live verification: environment blocked (2026-11-26, ~19:05-19:10 local)

No fault was injected and no incident was created. The comparison (fresh retry-policy and payment-stop incidents on
BlackBox c3aeb4b, `configs/dev_v3e.yaml`, prompt d7f971eae3) was stopped at the pre-injection check.

## Pre-injection check (observability stack and demo not started)

| Observation | Value |
|---|---|
| GPU idle temperature (1 min, 5 s samples) | 73 -> 77 C and rising |
| GPU utilization with nothing of BlackBox running | 20-25 % (VS Code ~16 %, Windows desktop compositor ~12 %) |
| GPU memory in use / total | 546 / 6144 MiB (enough for qwen3:4b, 3.9 GB) |

## Preflight: one synthesis-sized model call, no incident

`preflight_synth.py`: the current synthesis prompt and schema, dev_v3e model options, evidence from the saved
`runs/live_faults_windowfix/retry-policy` run (4975 prompt tokens). Full GPU trace in `preflight_synthesis_call.json`.

- The call completed in 33.2 s (timeout 240 s) and returned schema-valid JSON.
- Temperature went 76 -> 89 C within 30 s. Software thermal slowdown was active from second 15 to the end of the call.
- One minute after the call the GPU was still at 81-82 C with the same 20 % background load.

## Why this blocks the comparison

A live investigation makes about 8 model calls over 6-12 minutes, and synthesis is the last call, so it runs on
the hottest GPU. The previous attempt (`runs/live_faults_ownership`) timed out in synthesis at 95 C under the same
conditions. The sweep waits for < 80 C after injecting, and the idle floor here is ~80 C, so payment-stop would
probably again be injected and never investigated. Both outcomes are what this check is meant to prevent.

The preflight output is a check of the environment, not a result; it is not scored.
