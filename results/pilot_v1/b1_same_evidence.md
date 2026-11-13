# B1 vs BlackBox on the same evidence

Only B1 runs whose evidence fitted the prompt (`evidence_budget.matched == True`) and whose source BlackBox run completed with a usable report support same-evidence iteration claims. Other runs are listed but excluded. B1 tests reasoning on retrieved evidence, not retrieval or equal compute.

| incident_id | scenario | b1_matched | omitted_ids | b1_source_status | included | b1_outcome_auto | blackbox_outcome_auto | b1_unsupported_auto | blackbox_unsupported_auto |
|---|---|---|---|---|---|---|---|---|---|
| INC-0000 | 0 | yes | 0 | N/A | yes | no | no | 0 | 0 |
