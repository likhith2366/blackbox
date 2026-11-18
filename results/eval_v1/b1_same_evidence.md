# B1 vs BlackBox on the same evidence

Only B1 runs whose evidence fitted the prompt (`evidence_budget.matched == True`) and whose source BlackBox run completed with a usable report support same-evidence iteration claims. Other runs are listed but excluded. B1 tests reasoning on retrieved evidence, not retrieval or equal compute.

| incident_id | scenario | b1_matched | omitted_ids | b1_source_status | included | b1_outcome_auto | blackbox_outcome_auto | b1_unsupported_auto | blackbox_unsupported_auto |
|---|---|---|---|---|---|---|---|---|---|
| INC-3172 | 1 | yes | 0 | completed | yes | no | no | 0 | 0 |
| INC-5408 | 2 | yes | 0 | completed | yes | no | no | 0 | 0 |
| INC-2691 | 3 | yes | 0 | completed | yes | no | no | 0 | 0 |
| INC-7735 | 4 | yes | 0 | completed | yes | no | no | 0 | 0 |
| INC-1846 | 5 | yes | 0 | completed | yes | no | yes | 0 | 0 |
| INC-6053 | 6 | yes | 0 | completed | yes | no | yes | 0 | 0 |
| INC-4417 | 7 | yes | 0 | completed | yes | no | no | 0 | 0 |
| INC-8290 | 8 | N/A | N/A | N/A | EXCLUDED | no | no | 0 | 0 |
| INC-2264 | 9 | N/A | N/A | N/A | EXCLUDED | no | no | 0 | 0 |
