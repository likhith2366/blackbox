# Experiment: causal-evidence gate (development incidents only)

## Setup

**Question.** Does a gate that checks evidence *roles* (change, mechanism, effect) instead of citation counts
stop wrong diagnoses without blocking supported ones, and does its refusal reason lead the model to fetch
the missing mechanism evidence?

**Arms.** The two arms differ only in `diagnosis_gate`:
- **v2:** `configs/dev_v2.yaml`, which uses gate v1
- **v2c:** `configs/dev_v2c.yaml`, which sets `diagnosis_gate: causal`

`rationale_first` is off in both. Nothing else changed: no prompt, output order, connector or frozen-evaluation
change. The default and `configs/frozen_v1.yaml` keep gate v1.

**Gate (`blackbox/investigator/causal_gate.py`).** It checks the top hypothesis against retrieved records only.
It uses no incident ids, SHAs, answer keys or expected mechanisms.
- **Component:** must be a service seen in the evidence.
- **Mechanism text:** must contain a specific term, not only symptom or change words such as
  "timeout", "error spike" or "due to new deployment".
- **Change role:** a change-based hypothesis must cite a deployment, commit, build or config record of the
  component.
- **Mechanism role:** must be one of the following. A deploy record never counts.
  - a commit with its diff;
  - a config;
  - a component metric that is not an error, latency or request-rate series;
  - a log or trace containing a mechanism term.
- **Effect role:** a runtime record distinct from the mechanism record.
- **Timing:** the effect must not start before the change, where both have timestamps (10 s tolerance).
- **Non-change faults:** a hypothesis that is not change-based needs only the mechanism and effect roles.

**Refusals.**
- A refused diagnosis gets a neutral reason, e.g. "mechanism evidence missing". The run continues within the
  same budget, and nothing is fetched for the model.
- The refusal reason and the role assignment are kept in each step's `stop_reason` and `orchestration_rules`.
- At the round limit the same gate decides the final status, so an unsupported ledger stays
  `insufficient_evidence`.

**Code.**
- Revision `b2ba275`.
- Tests are in `tests/test_causal_gate.py` (12 tests). They cover:
  - symptom-only citations;
  - a symptom-only mechanism text;
  - deploy without mechanism, and a commit message without its diff;
  - a supported change-based claim;
  - an effect that starts before the change;
  - a supported runtime fault with no change;
  - unavailable change sources;
  - an unknown component;
  - the default gate is v1, including in the frozen config;
  - v1 behaviour is unchanged;
  - a refusal lets the run continue with the reason kept in the trace;
  - the round-limit status.
- Full suite: 404 passed, 3 skipped, 1 xfailed.

**Offline checks before the runs** (no model):
- **Wrong acceptances.** The gate was applied to every saved diagnose request from the earlier v1, v2 and v2b
  development runs (71 requests). It refused all of them, including the 6 v2b diagnoses that gate v1 had
  accepted. Reasons:
  - "change evidence missing" for INC-0000;
  - "mechanism not stated" for INC-9001 and INC-9002.
- **False refusals.** A correct hypothesis citing the answer key's decisive records, fetched from each snapshot,
  passes for all three incidents. It also passes without the commit, because the component metric fills the
  mechanism role. It is refused when it cites only the deploy and commit ("runtime effect missing").

**Runs.**
- 3 incidents × 2 arms × 2 runs = 12 runs.
- Model settings: qwen3:4b, temperature 0, seed 7, num_ctx 8192, 6 rounds, 12 calls.
- Runs were sequential with arms interleaved, on 2026-11-23 from 18:15 to 18:52 UTC. Before each run the GPU
  was below 80 °C and not throttled, and at least 1.5 GB of RAM was free (readings in `_runner.log`).
- All 12 completed. Outputs are in `runs/dev_v2c_experiment/`, analysed by `scripts/analyze_gate_experiment.py`.

## Results

| Incident | Arm | Outcome (r1, r2) | Decisive retrieved | Decisive cited | Diagnose refusals (per run) | Tools after each gate refusal | get_commit / get_config | Calls | Wall s |
|---|---|---|---|---|---|---|---|---|---|
| INC-0000 | v2 | abstain, abstain | 3/4 (commit, deploy, attempts metric) | 1/4 (attempts metric) | 1 change check + 3 v1 "cites 1 id" | get_metrics only | 0 / 0 | 9, 6 | 99, 128 |
| INC-0000 | v2c | abstain, abstain | 3/4 (same) | 1/4 (same) | 1 change check + 3 "mechanism not stated" | get_metrics only | 0 / 0 | 6, 9 | 86, 98 |
| INC-9001 | v2 | abstain, abstain | 2/4 (commit, deploy) | 0/4 | 1 change check + 2 v1 | get_metrics only | 0 / 0 | 7, 7 | 172, 135 |
| INC-9001 | v2c | abstain, abstain | 2/4 (same) | 0/4 | 1 change check + 2 "mechanism evidence missing" | get_metrics only | 0 / 0 | 7, 7 | 163, 148 |
| INC-9002 | v2 | abstain, abstain | 3/4 (commit, deploy, attempts metric) | 1/4 | 1 change check + 3 v1 | get_metrics only | 0 / 0 | 4, 4 | 142, 134 |
| INC-9002 | v2c | abstain, abstain | 3/4 (same) | 1/4 | 1 change check + 3 "mechanism not stated" | get_metrics only | 0 / 0 | 4, 4 | 140, 139 |

**Totals (v2 vs v2c).**
- Correct component and mechanism: 0/6 vs 0/6.
- Wrong diagnoses: 0/6 vs 0/6.
- Abstentions: 6/6 vs 6/6.
- Rounds after a gate refusal in which the ledger changed: 0/16 vs 0/16.
- Mean calls: 6.2 vs 6.2. Mean wall time: 135 s vs 129 s.
- The tools after each refusal were the same `get_metrics` request each time. With `refuse_repeats` these were
  refused as repeats, 2–6 per run.
- Repeats: identical model outputs for INC-9001 and INC-9002 in both arms; INC-0000 differed between repeats.
  Effectively n = 3 per arm.

## Inspection

**Wrong acceptances.** None in the runs: neither arm diagnosed. In the offline replay, the gate refused the
six v2b wrong diagnoses that gate v1 had accepted.

**False refusals.** None observed. Every refused top hypothesis was a symptom label citing one error or attempts
metric:
- checkout-svc / "payment-svc timeout" (INC-0000, INC-9002);
- checkout-svc / "rate limiting" (INC-9001).

None matches the answer key. Each refusal was correct for the evidence cited.

**The model had the story but did not act on it.**
- **INC-9001 R5.** The rationale reads "payment-svc errors (503s) from database pool timeouts. The traces show
  payment-svc db.pool.acquire errors (PoolTimeout ...)". That is close to the true mechanism, retrieved from a
  trace. The ledger still said "rate limiting".
- **INC-9002 R4–R6.** The rationale names deploy d-581 and commit 0d6ba14 with retry logic.

In neither case did the hypotheses change, and the model never requested `get_commit`, `get_config` or the
payment-svc metric list.

## Conclusion

The gate does what an evidence gate should:
- it blocks the symptom-only and "recent commit" diagnoses that gate v1 accepted (shown offline on the v2b
  outputs);
- it passes a correctly supported hypothesis;
- it produced no false refusals in these runs.

It did not help the model retrieve the missing mechanism evidence:
- after 16 refusals with precise reasons, the model made the same `get_metrics` request each time;
- it never called `get_commit`;
- its ledger changed in 0 rounds.

On these incidents v2 already abstained, so the gate changed only the refusal reason, not the outcome. Its
benefit is limited to converting wrong diagnoses (such as v2b's) into abstentions. **Root-cause accuracy did not
improve (0/6 in both arms).** The bottleneck is still the frozen hypothesis ledger and the model's tool choice
(cause 1 in `STEP1_DIAGNOSIS.md`), not the gate.

As instructed, no further intervention is made in this experiment. `diagnosis_gate` stays `v1` by default and in
the frozen config; `rationale_first` stays off.

**Uncertainty.**
- n = 3 incidents (two share a fault family).
- The "mechanism not stated" check is lexical. It uses a generic word list and could refuse a correct but
  tersely worded mechanism. That was not observed here.
- The mechanism role accepts a commit only with its diff, and `render_record` still cuts diffs at 600
  characters in the prompt (latent, never exercised because `get_commit` was never called).
