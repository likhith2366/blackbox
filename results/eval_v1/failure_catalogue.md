# Failure catalogue

Every failed, incomplete or erroneous run is listed; none are dropped. `likely_cause` is an automated guess for reviewers.

| run_id | incident_id | scenario | system | ablation | repeat | symptom | likely_cause (automated guess, needs review) | evidence |
|---|---|---|---|---|---|---|---|---|
| INC-3172_blackbox_none_r0 | INC-3172 | 1 | blackbox | none | 0 | false abstention on a diagnosable case (provisional, automated) | insufficient confidence or decisive evidence missed (coverage 0.00) | runs/eval_v1/INC-3172_blackbox_none_r0/worksheet |
| INC-3172_blackbox_none_r0 | INC-3172 | 1 | blackbox | none | 0 | 1 redundant tool call(s) | repeated identical request without new evidence | runs/eval_v1/INC-3172_blackbox_none_r0/hypothesis trace |
| INC-3172_blackbox_none_r1 | INC-3172 | 1 | blackbox | none | 1 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-3172_blackbox_none_r2 | INC-3172 | 1 | blackbox | none | 2 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-3172_blackbox_no_jira_r0 | INC-3172 | 1 | blackbox | no_jira | 0 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-3172_blackbox_no_provenance_r0 | INC-3172 | 1 | blackbox | no_provenance | 0 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-3172_blackbox_no_traces_r0 | INC-3172 | 1 | blackbox | no_traces | 0 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-3172_b1_none_r0 | INC-3172 | 1 | b1 | none | 0 | run status timeout, no usable report | see output.error / meta.exception | runs/eval_v1/INC-3172_b1_none_r0/meta.json, output.json |
| INC-3172_b0_none_r0 | INC-3172 | 1 | b0 | none | 0 | run status timeout, no usable report | see output.error / meta.exception | runs/eval_v1/INC-3172_b0_none_r0/meta.json, output.json |
| INC-5408_blackbox_none_r0 | INC-5408 | 2 | blackbox | none | 0 | false abstention on a diagnosable case (provisional, automated) | insufficient confidence or decisive evidence missed (coverage 0.00) | runs/eval_v1/INC-5408_blackbox_none_r0/worksheet |
| INC-5408_blackbox_none_r1 | INC-5408 | 2 | blackbox | none | 1 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-5408_blackbox_none_r2 | INC-5408 | 2 | blackbox | none | 2 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-5408_blackbox_no_jira_r0 | INC-5408 | 2 | blackbox | no_jira | 0 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-5408_blackbox_no_provenance_r0 | INC-5408 | 2 | blackbox | no_provenance | 0 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-5408_blackbox_no_traces_r0 | INC-5408 | 2 | blackbox | no_traces | 0 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-5408_b1_none_r0 | INC-5408 | 2 | b1 | none | 0 | false abstention on a diagnosable case (provisional, automated) | insufficient confidence or decisive evidence missed (coverage 0.00) | runs/eval_v1/INC-5408_b1_none_r0/worksheet |
| INC-5408_b0_none_r0 | INC-5408 | 2 | b0 | none | 0 | top-1 diagnosis not matched (provisional, automated) | retrieval: decisive coverage 0.00 | runs/eval_v1/INC-5408_b0_none_r0/worksheet / hypothesis trace |
| INC-2691_blackbox_none_r0 | INC-2691 | 3 | blackbox | none | 0 | top-1 diagnosis not matched (provisional, automated) | retrieval: decisive coverage 0.00 | runs/eval_v1/INC-2691_blackbox_none_r0/worksheet / hypothesis trace |
| INC-2691_blackbox_none_r0 | INC-2691 | 3 | blackbox | none | 0 | 2 redundant tool call(s) | repeated identical request without new evidence | runs/eval_v1/INC-2691_blackbox_none_r0/hypothesis trace |
| INC-2691_blackbox_none_r1 | INC-2691 | 3 | blackbox | none | 1 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-2691_blackbox_none_r2 | INC-2691 | 3 | blackbox | none | 2 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-2691_blackbox_no_jira_r0 | INC-2691 | 3 | blackbox | no_jira | 0 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-2691_blackbox_no_provenance_r0 | INC-2691 | 3 | blackbox | no_provenance | 0 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-2691_blackbox_no_traces_r0 | INC-2691 | 3 | blackbox | no_traces | 0 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-2691_b1_none_r0 | INC-2691 | 3 | b1 | none | 0 | top-1 diagnosis not matched (provisional, automated) | retrieval: decisive coverage 0.00 | runs/eval_v1/INC-2691_b1_none_r0/worksheet / hypothesis trace |
| INC-2691_b0_none_r0 | INC-2691 | 3 | b0 | none | 0 | top-1 diagnosis not matched (provisional, automated) | retrieval: decisive coverage 0.00 | runs/eval_v1/INC-2691_b0_none_r0/worksheet / hypothesis trace |
| INC-7735_blackbox_none_r0 | INC-7735 | 4 | blackbox | none | 0 | false abstention on a diagnosable case (provisional, automated) | insufficient confidence or decisive evidence missed (coverage 0.00) | runs/eval_v1/INC-7735_blackbox_none_r0/worksheet |
| INC-7735_blackbox_none_r0 | INC-7735 | 4 | blackbox | none | 0 | 1 redundant tool call(s) | repeated identical request without new evidence | runs/eval_v1/INC-7735_blackbox_none_r0/hypothesis trace |
| INC-7735_blackbox_none_r1 | INC-7735 | 4 | blackbox | none | 1 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-7735_blackbox_none_r2 | INC-7735 | 4 | blackbox | none | 2 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-7735_blackbox_no_jira_r0 | INC-7735 | 4 | blackbox | no_jira | 0 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-7735_blackbox_no_provenance_r0 | INC-7735 | 4 | blackbox | no_provenance | 0 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-7735_blackbox_no_traces_r0 | INC-7735 | 4 | blackbox | no_traces | 0 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-7735_b1_none_r0 | INC-7735 | 4 | b1 | none | 0 | run status failed, no usable report | see output.error / meta.exception | runs/eval_v1/INC-7735_b1_none_r0/meta.json, output.json |
| INC-7735_b0_none_r0 | INC-7735 | 4 | b0 | none | 0 | top-1 diagnosis not matched (provisional, automated) | retrieval: decisive coverage 0.33 | runs/eval_v1/INC-7735_b0_none_r0/worksheet / hypothesis trace |
| INC-1846_b1_none_r0 | INC-1846 | 5 | b1 | none | 0 | did not abstain on insufficient-evidence case (status no_fault_observed) | overconfident diagnosis | runs/eval_v1/INC-1846_b1_none_r0/worksheet |
| INC-1846_b0_none_r0 | INC-1846 | 5 | b0 | none | 0 | did not abstain on insufficient-evidence case (status diagnosis) | overconfident diagnosis | runs/eval_v1/INC-1846_b0_none_r0/worksheet |
| INC-6053_b1_none_r0 | INC-6053 | 6 | b1 | none | 0 | did not abstain on insufficient-evidence case (status no_fault_observed) | overconfident diagnosis | runs/eval_v1/INC-6053_b1_none_r0/worksheet |
| INC-6053_b0_none_r0 | INC-6053 | 6 | b0 | none | 0 | did not abstain on insufficient-evidence case (status diagnosis) | overconfident diagnosis | runs/eval_v1/INC-6053_b0_none_r0/worksheet |
| INC-4417_blackbox_none_r0 | INC-4417 | 7 | blackbox | none | 0 | 4 redundant tool call(s) | repeated identical request without new evidence | runs/eval_v1/INC-4417_blackbox_none_r0/hypothesis trace |
| INC-4417_b1_none_r0 | INC-4417 | 7 | b1 | none | 0 | false causal diagnosis on healthy run | overconfident attribution | runs/eval_v1/INC-4417_b1_none_r0/worksheet |
| INC-4417_b0_none_r0 | INC-4417 | 7 | b0 | none | 0 | false causal diagnosis on healthy run | overconfident attribution | runs/eval_v1/INC-4417_b0_none_r0/worksheet |
| INC-8290_blackbox_none_r0 | INC-8290 | 8 | blackbox | none | 0 | top-1 diagnosis not matched (provisional, automated) | retrieval: decisive coverage 0.00 | runs/eval_v1/INC-8290_blackbox_none_r0/worksheet / hypothesis trace |
| INC-8290_b1_none_r0 | INC-8290 | 8 | b1 | none | 0 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-8290_b0_none_r0 | INC-8290 | 8 | b0 | none | 0 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-2264_blackbox_none_r0 | INC-2264 | 9 | blackbox | none | 0 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-2264_b1_none_r0 | INC-2264 | 9 | b1 | none | 0 | planned run not executed | batch incomplete or interrupted | plan.json |
| INC-2264_b0_none_r0 | INC-2264 | 9 | b0 | none | 0 | planned run not executed | batch incomplete or interrupted | plan.json |
