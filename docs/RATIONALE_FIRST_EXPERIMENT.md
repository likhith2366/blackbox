# Experiment: rationale before hypotheses (development incidents only)

## Setup

**Hypothesis.** If the model writes its rationale before the structured hypotheses, and is asked to
reconcile the two, it will update its hypotheses when it finds relevant change evidence.

**Arms.** The two arms differ only in `rationale_first`:
- **v2:** `configs/dev_v2.yaml`
- **v2b:** `configs/dev_v2b.yaml`, which sets `rationale_first: true`

**What the variant changes.**
- The round schema orders `rationale` before `hypotheses`.
- The closing instruction asks the model to state:
  - the change, its effective deployment time and the affected component;
  - the symptom onset and the linking evidence ids (or "no causal link established");
  - then hypotheses that match this reasoning, with exact evidence ids.
- There are no incident-specific rules, no connector changes and no change to the evidence gate.
- The option is off by default and off in `configs/frozen_v1.yaml`. The 47 frozen runs were not touched.

**Code.**
- Revision `f16e167`.
- Files changed: `blackbox/schemas.py`, `blackbox/investigator/common.py`,
  `blackbox/investigator/coordinator.py`, `tests/test_investigator_core.py` (3 new tests).
- The full suite has 392 passed, 3 skipped, 1 xfailed.

**Runs.**
- 3 incidents (INC-0000, INC-9001, INC-9002) × 2 arms × 2 runs = 12 runs.
- Model settings: qwen3:4b, temperature 0, seed 7, num_ctx 8192, 6 rounds, 12 calls.
- Runs were sequential with arms interleaved, on 2026-11-23 from 16:02 to 16:48 UTC.
- Before each run the GPU was under 80 °C and not throttled (readings in `_runner.log`).
- All 12 runs completed with no parse failures.
- Outputs are in `runs/dev_v2b_experiment/`. The analysis is `scripts/analyze_rationale_experiment.py`.

## Per-run results

| Run | Outcome (matcher) | Gate pass | Change round | Rounds after it with a changed ledger | Relevant deploy/commit id cited by a hypothesis | Rationale/ledger: agree, rationale-only | Calls | Wall s | Top-ranked cause |
|---|---|---|---|---|---|---|---|---|---|
| INC-0000 v2 r1 | abstained | no (3 refusals) | R3 | 0 of 3 | none | 1, 2 | 9 | 236 | — |
| INC-0000 v2 r2 | abstained | no (3) | R3 | 0 of 3 | none | 2, 1 | 6 | 141 | — |
| INC-0000 v2b r1 | **wrong** | yes | R2 | 1 of 1 | none | 0, 1 | 3 | 152 | checkout-svc :: "Payment integration error due to recent commit" (cites 2 metrics) |
| INC-0000 v2b r2 | **wrong** | yes | R2 | 1 of 1 | none | 0, 1 | 3 | 171 | same as r1 |
| INC-9001 v2 r1 | abstained | no (2) | R3 | 0 of 3 | none | 1, 2 | 7 | 214 | — |
| INC-9001 v2 r2 | abstained | no (2) | R3 | 0 of 3 | none | 1, 2 | 7 | 201 | — |
| INC-9001 v2b r1 | **wrong** | yes | R3 | 0 of 1 | none | 0, 1 | 4 | 133 | payment-svc :: "Payment service failure causing checkout-svc errors" (cites 2 metrics) |
| INC-9001 v2b r2 | **wrong** | yes | R3 | 0 of 1 | none | 0, 1 | 4 | 235 | same as r1 |
| INC-9002 v2 r1 | abstained | no (3) | R3 | 0 of 3 | none | 0, 3 | 4 | 138 | — |
| INC-9002 v2 r2 | abstained | no (3) | R3 | 0 of 3 | none | 0, 3 | 4 | 140 | — |
| INC-9002 v2b r1 | **wrong** | yes | R2 | 1 of 1 | deploy:d-581 (2nd-ranked) | 1, 0 | 3 | 74 | checkout-svc :: "error rate spike due to transient upstream failures (payment-svc)" |
| INC-9002 v2b r2 | **wrong** | yes | R2 | 1 of 1 | deploy:d-581 (2nd-ranked) | 1, 0 | 3 | 87 | same as r1 |

**Totals.**

| | v2 | v2b |
|---|---|---|
| Correct component and mechanism (top-1 and top-3) | 0/6 | 0/6 |
| Wrong diagnoses | 0/6 | **6/6** |
| Abstentions | 6/6 | 0/6 |
| Gate passes | 0/6 | 6/6 |
| Rounds after change retrieval with a changed hypothesis | 0/18 | 4/6 |
| Runs where a hypothesis cites a relevant deploy/commit id | 0/6 | 2/6 (both INC-9002) |
| Mean tool calls per run | 6.2 | 3.3 |
| Mean wall time per run | 178 s | 142 s |

Repeats are not independent: r1 and r2 produced identical model outputs in every v2b pair and in v2 for
INC-9001 and INC-9002. Only INC-0000 v2 differed between repeats. In effect this is n = 3 per arm.

## Inspection of the cited records

A hypothesis only counts as a causal explanation if it links a change to the symptom through evidence.
None of the v2b diagnoses does:

- **INC-0000 (retry amplification).** The top cause, "Payment integration error due to recent commit",
  cites only `metric:3631246b54` (upstream_errors_per_s) and `metric:83cc53b310` (error_rate). It names a
  commit but cites none: an unsupported causal leap. A second hypothesis, "Error rate spike due to recent
  deployment", cites nothing. The report's claims do state that commit e7a0cc0 "introduced retry logic",
  but nothing ties retries to the failure.
- **INC-9001 (connection leak).** The top cause, "payment-svc :: Payment service failure", is a symptom
  label citing two error-rate metrics. Its rationale makes the right temporal observation: payment-svc
  deploy d-576 at 03:44:06, errors at 03:44:22. But the hypothesis contains no mechanism (pool, leak),
  cites no change, and the decisive pool metric was never retrieved.
- **INC-9002 (retry amplification).** This is the closest case. The second-ranked hypothesis cites
  `deploy:d-581` with the mechanism "error rate spike due to new deployment (version 1.9.0)". That
  attributes the symptom to the latest deploy without a mechanism. The report also claims "1.9.0
  deployment caused increased upstream attempts" and "includes retry logic", which is the correct causal
  story, but it sits in the claims and the second-ranked cause, not in the top-ranked mechanism. The
  top-ranked cause is the symptom label "transient upstream failures".

In every v2b run, the gate passed on two runtime metric citations in the same round that the change
evidence arrived. The variant made the model commit, not reason better.

## Conclusion: the intervention failed

- **What moved.** The variant did make the structured hypotheses move: 4/6 post-change rounds changed,
  against 0/18 for v2.
- **What did not move.** The new hypotheses are symptom labels or "recent change" attributions, not
  mechanisms.
- **The net effect is worse.** Six abstentions became six wrong diagnoses that passed gate v1, which is
  the same failure the coverage-note experiment produced earlier. On the cautious-answer criteria that
  BlackBox previously met, this variant would be a regression.
- **Why.** Three observations explain it:
  1. Writing the rationale first gives the model a ready-made narrative ("symptom at T, deploy at T−x").
     It then fills the hypothesis slot with that narrative, while the gate checks only the count of
     runtime citations.
  2. The decisive mechanism evidence (the diff, pool metrics, attempt counts per request) was still not
     retrieved or cited. The model diagnosed one or two rounds after the change records arrived.
  3. Gate v1 cannot distinguish "change X linked to symptom Y by evidence Z" from two symptom metrics.
- **Next step.** As instructed, no further prompt change is stacked on this experiment. `rationale_first`
  stays off by default. The finding points at the gate, not the prompt: the gate needs a check that
  causal hypotheses cite the change (gate v2 design), and that should be tested separately.
