# Source ablations vs full evidence (BlackBox, single runs, exploratory)

Accuracy and claim support are (provisional, automated).

| incident_id | ablation | run_status | report_status | top1_auto | top3_auto | claims_asserted | claims_unsupported_auto | decisive_coverage | tool_calls_accepted | failed_calls | total_tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|
| INC-2691 | none | not_run | N/A | no | no | 0 | 0 | 0.00 | N/A | N/A | N/A |
| INC-2691 | no_jira | not_run | N/A | no | no | 0 | 0 | 0.00 | N/A | N/A | N/A |
| INC-2691 | no_provenance | not_run | N/A | no | no | 0 | 0 | 0.00 | N/A | N/A | N/A |
| INC-2691 | no_traces | not_run | N/A | no | no | 0 | 0 | 0.00 | N/A | N/A | N/A |
| INC-3172 | none | completed | insufficient_evidence | no | no | 0 | 0 | 0.00 | 6 | 0 | 22363 |
| INC-3172 | no_jira | not_run | N/A | no | no | 0 | 0 | 0.00 | N/A | N/A | N/A |
| INC-3172 | no_provenance | not_run | N/A | no | no | 0 | 0 | 0.00 | N/A | N/A | N/A |
| INC-3172 | no_traces | not_run | N/A | no | no | 0 | 0 | 0.00 | N/A | N/A | N/A |
| INC-5408 | none | running | N/A | no | no | 0 | 0 | 0.00 | N/A | N/A | N/A |
| INC-5408 | no_jira | not_run | N/A | no | no | 0 | 0 | 0.00 | N/A | N/A | N/A |
| INC-5408 | no_provenance | not_run | N/A | no | no | 0 | 0 | 0.00 | N/A | N/A | N/A |
| INC-5408 | no_traces | not_run | N/A | no | no | 0 | 0 | 0.00 | N/A | N/A | N/A |
| INC-7735 | none | not_run | N/A | no | no | 0 | 0 | 0.00 | N/A | N/A | N/A |
| INC-7735 | no_jira | not_run | N/A | no | no | 0 | 0 | 0.00 | N/A | N/A | N/A |
| INC-7735 | no_provenance | not_run | N/A | no | no | 0 | 0 | 0.00 | N/A | N/A | N/A |
| INC-7735 | no_traces | not_run | N/A | no | no | 0 | 0 | 0.00 | N/A | N/A | N/A |


## Aggregate over diagnosable primary cases

| ablation | runs | top1_auto | top3_auto | claims_asserted | unsupported_auto | tool_calls |
|---|---|---|---|---|---|---|
| none | 4 | 0/4 | 0/4 | 0 | 0 | 6 |
| no_jira | 4 | 0/4 | 0/4 | 0 | 0 | 0 |
| no_provenance | 4 | 0/4 | 0/4 | 0 | 0 | 0 |
| no_traces | 4 | 0/4 | 0/4 | 0 | 0 | 0 |
