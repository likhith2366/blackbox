# Coordinator v2 on development incidents (not the frozen evaluation)

## What v2 changes

The three options were added in commit 8c5b6d5 on live-integration. All are off in `configs/frozen_v1.yaml`
and on in `configs/dev_v2.yaml`:

- **change_check:** the run may not end with a diagnosis, an abstention or a no-fault answer until it has
  called `list_deployments` and `search_commits`. The last round is exempt.
- **refuse_repeats:** an identical repeat call is refused and does not use budget. In v1 it was served
  from the cache and did count.
- **multi_call_example:** the prompt example shows two calls in one round, `list_deployments` and
  `get_metrics`. v1 showed one ERROR log search.

The run settings were otherwise the frozen ones: qwen3:4b, temperature 0, seed 7, 6 rounds, 12 calls.
Each incident was run once per arm on 2026-11-20 after eval_v1 had finished, using the same snapshots
and the same code for both arms. The script is `scripts/run_dev_v2.py` and the runs are in
`runs/dev_v2_experiment/`.

## Results

Decisive records are counted out of 4 per incident, with the documented equivalents included.

| Incident (fault) | Arm | Outcome | Decisive retrieved | Decisive cited in report | Change tools used |
|---|---|---|---|---|---|
| INC-0000 (retry amplification) | v1 | abstained | 1 | 1 | none |
| | v2 | abstained | **3** | **3** | deployments, commits |
| INC-9001 (connection leak) | v1 | abstained | 0 | 0 | none |
| | v2 | abstained | **2** | **2** | deployments, commits |
| INC-9002 (retry amplification) | v1 | abstained | 0 | 0 | none |
| | v2 | abstained | **3** | **1** | deployments, commits |
| **Total** | v1 | 0/3 correct | **1/12** | 1/12 | 0 of 3 runs |
| | v2 | 0/3 correct | **8/12** | **6/12** | 3 of 3 runs |

## What it shows

- **Retrieval is fixed.** v2 retrieved the fault commit and deployment in all three runs; v1 never did.
  Decisive coverage rose from 1/12 to 8/12.
- **Reasoning is not.** After retrieving the commit and the deployment, the model never updated its
  hypotheses to cite them. Every hypothesis kept citing a single metric, so gate v1 refused each
  diagnosis ("cites 1 retrieved evidence id"), and all three runs still ended without an answer.
- **New failure:** after a repeat was refused, the model sent the same refused call again in the next
  rounds (INC-9002: rounds 4–6), wasting those rounds.

## Limits

These are three development incidents with one run per arm. INC-0000 and INC-9002 share a fault family,
so the comparison is indicative, not a measured accuracy change. Nothing here touches the frozen eval_v1
results.

## Next step

After the change tools return records, add a coordinator step that asks the model to compare each
deployment and commit time with the symptom onset, and to cite the matching change in a hypothesis. If
the model sends a call that was already refused, stop the run early rather than spend the remaining
rounds. Run this as v3 on the same development incidents before any new frozen evaluation (eval_v2).
