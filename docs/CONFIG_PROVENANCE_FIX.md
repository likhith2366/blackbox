# Step 6 (development only): deployment-relative config provenance and its effect

## What changed

`get_config` (`blackbox/evidence/backend.py`) now compares a deployment's config with the config of the **previous
deployment of the same service**, rather than with the previous version in the repository.
- **Which deployment came before:** chosen by deployment start time, from the deployment records.
- **Which config each deployment ran:** found by walking commit parents from its deployed commit. Commit time is
  never used.
- **Possible outputs:**
  - "unchanged from previous deployment d-574 (… started …, commit …)";
  - a real diff between the two deployments;
  - an explicit "not possible", or a provenance conflict, when the previous deployment is missing, its commit is
    unknown, the build and deployment disagree, or two deployments started at the same time.

  Uncertainty is never reported as "unchanged". The current value stays visible.
- **Tests:** `tests/test_config_provenance.py` (9). Full suite at the time: 413 passed, 3 skipped, 1 xfailed.

## Packets

`runs/reasoning_probe_v3/` uses the same rule as v2, and the v2 packets and outputs are untouched.
- **Record-level diff:** `_packet_diff_vs_v2.txt`. Only the 6 config records of the full packets changed; ids,
  order and the prompt are identical.
- **Leakage check:** `_leakage_check.txt`. 44 terms checked, 0 hits.
- **INC-9001 before:** the payment-svc config record showed "change vs previous version: 3000 → 1000".
- **INC-9001 after:** it says "unchanged from previous deployment d-574 (payment-svc 2.4.1, started
  03:43:49.742Z, commit b7ae74f): both deployments run config b1e5a60".

## Runs

- **Setup:** same as Step 5 — qwen3:4b and qwen3:8b, temperature 0, seed 7, num_ctx 12288, num_predict 4000.
- **Volume:** 2 repeats per packet. 24/24 finished with `stop`.
- **Review:** a blinded automated review scored the outputs with the Step 5 rubric. The 8 low-confidence items are in
  `_human_review_likhith.md` and `_human_review_praneeth.md` (blank, independent).

## Results

| | v2 (old config record) | v3 (corrected) |
|---|---|---|
| INC-9001 full: top hypothesis built on the old 3000→1000 ms timeout | 4/4 | **0/4** |
| INC-9001 full: verdict | 4 wrong | 4 partially correct |
| All packets, qwen3:4b | 10 partial, 2 wrong | 12 partial |
| All packets, qwen3:8b | 1 correct (disputed), 8 partial, 3 wrong | 12 partial |
| Full mechanism in the top hypothesis | 1/24 (disputed) | 0/24 |
| Unsupported citations in top hypotheses | several (wrong diagnoses) | 0 |
| Abstentions | 0/24 | 0/24 |

**INC-9001, whether the models noticed the missing release:**
- **qwen3:4b** now names the new fast path (d-576, d142601) and cites the DB-pool metric (`metric:03d4f590b8`)
  and the source record. Its mechanism is "high concurrency of small charges", not the unreleased connection.
- **qwen3:8b** says "pool exhausted due to a recent deployment".
- Neither model mentions the missing `POOL.release`, although the source record shows it.

## Conclusion

- **The provenance defect was real and caused the distractor.** Once the config record stopped implying that d-576
  shipped the timeout change, neither model blamed it.
- **The fix removed wrong diagnoses but did not produce the correct mechanism.** All 24 answers are now "partially
  correct": right component and right change, wrong or missing "how".
- **The remaining gap is model reasoning over code:** spotting that an early return skips a release, and that
  retries without backoff multiply calls.
- Development-only. The agent implementation was not changed on the basis of this result.
