| run | outcome | gate_pass | gate_refusals | change_round | rounds_after_change | hyp_changed_after | relevant_change_cited | irrelevant_change_cited | agree | rationale_only | ledger_only | parse_failures | tool_calls | wall_s | top_cause |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| INC-0000_v2_r1 | abstained (insufficient_evidence) | False | 3 | 3 | 3 | 0 | [] | [] | 1 | 2 | 0 | 0 | 9 | 236 |  |
| INC-0000_v2_r2 | abstained (insufficient_evidence) | False | 3 | 3 | 3 | 0 | [] | [] | 2 | 1 | 0 | 0 | 6 | 141 |  |
| INC-0000_v2b_r1 | wrong | True | 0 | 2 | 1 | 1 | [] | [] | 0 | 1 | 0 | 0 | 3 | 152 | checkout-svc :: Payment integration error due to recent commit |
| INC-0000_v2b_r2 | wrong | True | 0 | 2 | 1 | 1 | [] | [] | 0 | 1 | 0 | 0 | 3 | 171 | checkout-svc :: Payment integration error due to recent commit |
| INC-9001_v2_r1 | abstained (insufficient_evidence) | False | 2 | 3 | 3 | 0 | [] | [] | 1 | 2 | 0 | 0 | 7 | 214 |  |
| INC-9001_v2_r2 | abstained (insufficient_evidence) | False | 2 | 3 | 3 | 0 | [] | [] | 1 | 2 | 0 | 0 | 7 | 201 |  |
| INC-9001_v2b_r1 | wrong | True | 0 | 3 | 1 | 0 | [] | [] | 0 | 1 | 0 | 0 | 4 | 133 | payment-svc :: Payment service failure causing checkout-svc errors |
| INC-9001_v2b_r2 | wrong | True | 0 | 3 | 1 | 0 | [] | [] | 0 | 1 | 0 | 0 | 4 | 235 | payment-svc :: Payment service failure causing checkout-svc errors |
| INC-9002_v2_r1 | abstained (insufficient_evidence) | False | 3 | 3 | 3 | 0 | [] | [] | 0 | 3 | 0 | 0 | 4 | 138 |  |
| INC-9002_v2_r2 | abstained (insufficient_evidence) | False | 3 | 3 | 3 | 0 | [] | [] | 0 | 3 | 0 | 0 | 4 | 140 |  |
| INC-9002_v2b_r1 | wrong | True | 0 | 2 | 1 | 1 | ['deploy:d-581'] | [] | 1 | 0 | 0 | 0 | 3 | 74 | checkout-svc :: error rate spike due to transient upstream failures (payment-svc) |
| INC-9002_v2b_r2 | wrong | True | 0 | 2 | 1 | 1 | ['deploy:d-581'] | [] | 1 | 0 | 0 | 0 | 3 | 87 | checkout-svc :: error rate spike due to transient upstream failures (payment-svc) |
