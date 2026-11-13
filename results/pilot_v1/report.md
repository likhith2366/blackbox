# Batch `pilot_v1` summary

Generated 2026-11-20T02:55:44Z from run records on disk. Phase: `pilot`.

## Completed vs planned

- Planned runs: 6; records on disk: 3; planned but not run: 3
- Harness status counts: {"completed": 3, "not_run": 3}
- Excluded stub records: 0
- Answer keys used: `runs\pilot_v1\_answer_keys_at_run` (sha256 INC-0000=cc02bf62a926)

- Model: `qwen3:4b` digest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`; git `45e13142d4343c71f382fc932e3f45d563ca7f82` dirty=True

## 1. Measured results (objective, from run records and answer-key IDs)

| system | n_runs | failed_or_incomplete_runs | tool_calls_accepted | redundant_calls | redundant_rate | failed_calls | budget_violations | unauthorized_attempted | unauthorized_executed | invalid_citations_recomputed | decisive_coverage_mean | decisive_coverage_pooled | total_tokens | wall_seconds | b1_matched | b1_unmatched |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| b0 | 1 | 0 | 0 | 0 | N/A | 0 | 0 | 0 | 0 | 0 | 0.00 | 0.00 | 6944 | 111.22 | 0 | 0 |
| b1 | 1 | 0 | 0 | 0 | N/A | 0 | 0 | 0 | 0 | 0 | 0.00 | 0.00 | 10588 | 310.52 | 1 | 0 |
| blackbox | 1 | 0 | 6 | 3 | 0.50 | 0 | 0 | 0 | 0 | 0 | 0.00 | 0.00 | 25379 | 292.09 | 0 | 0 |

Abstention metrics below are read from report status against the answer key's expected status (objective given the key):

| system | insufficient_cases | correct_abstentions | correct_abstention_rate | false_abstentions | false_abstention_rate | healthy_cases | false_causal_diagnoses | false_causal_rate |
|---|---|---|---|---|---|---|---|---|
| b0 | 0 | 0 | N/A | 0 | N/A | 0 | 0 | N/A |
| b1 | 0 | 0 | N/A | 0 | N/A | 0 | 0 | N/A |
| blackbox | 0 | 0 | N/A | 0 | N/A | 0 | 0 | N/A |


## 2. Provisional automated judgments (need two-reviewer confirmation)

Primary denominator = 0 diagnosable primary cases; abstentions, failures and missing runs count as misses. Matching is lexical against accepted wordings. Unsupported-claim rate here is an automated LOWER BOUND (no citation, or only non-supplied citations); cited claims await semantic review.

| system | diag_primary_present | top1_correct_auto | top1_accuracy_auto | top3_correct_auto | top3_accuracy_auto | claims_asserted | claims_unsupported_auto | unsupported_rate_auto | claims_pending_review | claims_tentative |
|---|---|---|---|---|---|---|---|---|---|---|
| b0 | 0 | 0 | N/A | 0 | N/A | 3 | 0 | 0.00 | 3 | 0 |
| b1 | 0 | 0 | N/A | 0 | N/A | 3 | 0 | 0.00 | 3 | 0 |
| blackbox | 0 | 0 | N/A | 0 | N/A | 2 | 0 | 0.00 | 2 | 1 |


Robustness variants (reported separately, not in the primary denominator):

_none_


BlackBox repeat top-1 accuracy min/max (provisional, automated): N/A / N/A

Failure catalogue entries: 7 (see failure_catalogue.md).

## 3. Untested assumptions

- Answer keys and accepted wordings are taken as correct; teammate review of each key is a separate step not verified by this code.
- The lexical matcher may accept or reject equivalent wordings incorrectly; every diagnosis verdict is in worksheets/diagnosis_review.csv for human confirmation.
- Claims citing supplied evidence are not assumed supported; no human review has been recorded (reviewer columns are blank).
- Repeat runs use identical decoding settings; agreement reflects serving nondeterminism only.
- Single-run baseline and ablation cells: differences are exploratory, not significant.
- B1 controls for evidence, not compute; unmatched B1 runs are excluded from same-evidence claims.
- Tickets and build history are team-authored fixtures, not live integrations.
- Compound claims are not split automatically; reviewers split them before grading.
