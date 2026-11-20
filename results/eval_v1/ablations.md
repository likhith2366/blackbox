# Source ablations vs full evidence (BlackBox, single runs, exploratory)

Accuracy and claim support are (provisional, automated).

| incident_id | ablation | run_status | report_status | top1_auto | top3_auto | claims_asserted | claims_unsupported_auto | decisive_coverage | tool_calls_accepted | failed_calls | total_tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|
| INC-2691 | none | completed | diagnosis | no | no | 2 | 0 | 0.00 | 5 | 0 | 13662 |
| INC-2691 | no_jira | completed | diagnosis | no | no | 1 | 0 | 0.00 | 5 | 0 | 14360 |
| INC-2691 | no_provenance | completed | diagnosis | no | no | 1 | 0 | 0.00 | 4 | 0 | 12413 |
| INC-2691 | no_traces | completed | insufficient_evidence | no | no | 4 | 0 | 0.00 | 8 | 0 | 15854 |
| INC-3172 | none | completed | insufficient_evidence | no | no | 0 | 0 | 0.00 | 6 | 0 | 22363 |
| INC-3172 | no_jira | completed | diagnosis | no | no | 3 | 0 | 0.00 | 5 | 0 | 18988 |
| INC-3172 | no_provenance | completed | diagnosis | no | no | 3 | 0 | 0.00 | 6 | 0 | 25430 |
| INC-3172 | no_traces | completed | insufficient_evidence | no | no | 2 | 0 | 0.00 | 8 | 0 | 13777 |
| INC-5408 | none | completed | insufficient_evidence | no | no | 0 | 0 | 0.00 | 1 | 0 | 2834 |
| INC-5408 | no_jira | completed | insufficient_evidence | no | no | 0 | 0 | 0.00 | 1 | 0 | 2846 |
| INC-5408 | no_provenance | completed | insufficient_evidence | no | no | 0 | 0 | 0.00 | 1 | 0 | 2839 |
| INC-5408 | no_traces | completed | insufficient_evidence | no | no | 0 | 0 | 0.00 | 1 | 0 | 3013 |
| INC-7735 | none | completed | insufficient_evidence | no | no | 2 | 0 | 0.00 | 6 | 0 | 22307 |
| INC-7735 | no_jira | completed | insufficient_evidence | no | no | 0 | 0 | 0.00 | 6 | 0 | 22777 |
| INC-7735 | no_provenance | completed | insufficient_evidence | no | no | 0 | 0 | 0.00 | 6 | 0 | 21121 |
| INC-7735 | no_traces | completed | insufficient_evidence | no | no | 2 | 0 | 0.00 | 7 | 1 | 12992 |


## Aggregate over diagnosable primary cases

| ablation | runs | top1_auto | top3_auto | claims_asserted | unsupported_auto | tool_calls |
|---|---|---|---|---|---|---|
| none | 4 | 0/4 | 0/4 | 4 | 0 | 18 |
| no_jira | 4 | 0/4 | 0/4 | 4 | 0 | 17 |
| no_provenance | 4 | 0/4 | 0/4 | 4 | 0 | 17 |
| no_traces | 4 | 0/4 | 0/4 | 8 | 0 | 24 |
