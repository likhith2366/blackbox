# Decisive-evidence scores v1 vs v2: `eval_v1` (formal)

Generated 2026-11-20T05:39:34Z. v1 = `eval/metrics.py` sha256 `906d4b9ca8a8`; v2 = `scripts/score_v2.py` sha256 `97e16197b9b8`.
Rule (both): a decisive id counts only via the same id or an id listed explicitly in `decisive_equivalents`; no substring, prefix or similarity matching. v2 adds decisive evidence cited in the final report.

| run | system | status | report | decisive | retrieved v1 | retrieved v2 | cited v2 | changed |
|---|---|---|---|---|---|---|---|---|
| INC-1846_b0_none_r0 | b0 | completed | diagnosis | 0 | 0 | 0 | 0 | no |
| INC-1846_b1_none_r0 | b1 | completed | no_fault_observed | 0 | 0 | 0 | 0 | no |
| INC-1846_blackbox_none_r0 | blackbox | completed | insufficient_evidence | 0 | 0 | 0 | 0 | no |
| INC-2264_b0_none_r0 | b0 | completed | diagnosis | 4 | 0 | 0 | 0 | no |
| INC-2264_b1_none_r0 | b1 | completed | no_fault_observed | 4 | 0 | 0 | 0 | no |
| INC-2264_blackbox_none_r0 | blackbox | completed | insufficient_evidence | 4 | 0 | 0 | 0 | no |
| INC-2691_b0_none_r0 | b0 | completed | diagnosis | 4 | 0 | 0 | 0 | no |
| INC-2691_b1_none_r0 | b1 | completed | diagnosis | 4 | 0 | 0 | 0 | no |
| INC-2691_blackbox_no_jira_r0 | blackbox | completed | diagnosis | 4 | 0 | 0 | 0 | no |
| INC-2691_blackbox_no_provenance_r0 | blackbox | completed | diagnosis | 4 | 0 | 0 | 0 | no |
| INC-2691_blackbox_no_traces_r0 | blackbox | completed | insufficient_evidence | 4 | 0 | 0 | 0 | no |
| INC-2691_blackbox_none_r0 | blackbox | completed | diagnosis | 4 | 0 | 0 | 0 | no |
| INC-2691_blackbox_none_r1 | blackbox | completed | insufficient_evidence | 4 | 0 | 0 | 0 | no |
| INC-2691_blackbox_none_r2 | blackbox | completed | diagnosis | 4 | 0 | 0 | 0 | no |
| INC-3172_b0_none_r0 | b0 | timeout | - | 4 | 1 | 1 | 0 | no |
| INC-3172_b1_none_r0 | b1 | timeout | - | 4 | 0 | 0 | 0 | no |
| INC-3172_blackbox_no_jira_r0 | blackbox | completed | diagnosis | 4 | 0 | 0 | 0 | no |
| INC-3172_blackbox_no_provenance_r0 | blackbox | completed | diagnosis | 4 | 0 | 0 | 0 | no |
| INC-3172_blackbox_no_traces_r0 | blackbox | completed | insufficient_evidence | 4 | 0 | 0 | 0 | no |
| INC-3172_blackbox_none_r0 | blackbox | completed | insufficient_evidence | 4 | 0 | 0 | 0 | no |
| INC-3172_blackbox_none_r1 | blackbox | completed | diagnosis | 4 | 0 | 0 | 0 | no |
| INC-3172_blackbox_none_r2 | blackbox | completed | diagnosis | 4 | 1 | 1 | 0 | no |
| INC-4417_b0_none_r0 | b0 | completed | diagnosis | 0 | 0 | 0 | 0 | no |
| INC-4417_b1_none_r0 | b1 | completed | diagnosis | 0 | 0 | 0 | 0 | no |
| INC-4417_blackbox_none_r0 | blackbox | completed | insufficient_evidence | 0 | 0 | 0 | 0 | no |
| INC-5408_b0_none_r0 | b0 | completed | diagnosis | 4 | 0 | 0 | 0 | no |
| INC-5408_b1_none_r0 | b1 | completed | no_fault_observed | 4 | 0 | 0 | 0 | no |
| INC-5408_blackbox_no_jira_r0 | blackbox | completed | insufficient_evidence | 4 | 0 | 0 | 0 | no |
| INC-5408_blackbox_no_provenance_r0 | blackbox | completed | insufficient_evidence | 4 | 0 | 0 | 0 | no |
| INC-5408_blackbox_no_traces_r0 | blackbox | completed | insufficient_evidence | 4 | 0 | 0 | 0 | no |
| INC-5408_blackbox_none_r0 | blackbox | completed | insufficient_evidence | 4 | 0 | 0 | 0 | no |
| INC-5408_blackbox_none_r1 | blackbox | completed | insufficient_evidence | 4 | 0 | 0 | 0 | no |
| INC-5408_blackbox_none_r2 | blackbox | completed | insufficient_evidence | 4 | 0 | 0 | 0 | no |
| INC-6053_b0_none_r0 | b0 | completed | diagnosis | 0 | 0 | 0 | 0 | no |
| INC-6053_b1_none_r0 | b1 | completed | no_fault_observed | 0 | 0 | 0 | 0 | no |
| INC-6053_blackbox_none_r0 | blackbox | completed | insufficient_evidence | 0 | 0 | 0 | 0 | no |
| INC-7735_b0_none_r0 | b0 | completed | diagnosis | 3 | 1 | 1 | 1 | no |
| INC-7735_b1_none_r0 | b1 | failed | - | 3 | 0 | 0 | 0 | no |
| INC-7735_blackbox_no_jira_r0 | blackbox | completed | insufficient_evidence | 3 | 0 | 0 | 0 | no |
| INC-7735_blackbox_no_provenance_r0 | blackbox | completed | insufficient_evidence | 3 | 0 | 0 | 0 | no |
| INC-7735_blackbox_no_traces_r0 | blackbox | completed | insufficient_evidence | 3 | 0 | 0 | 0 | no |
| INC-7735_blackbox_none_r0 | blackbox | completed | insufficient_evidence | 3 | 0 | 0 | 0 | no |
| INC-7735_blackbox_none_r1 | blackbox | completed | insufficient_evidence | 3 | 0 | 0 | 0 | no |
| INC-7735_blackbox_none_r2 | blackbox | completed | insufficient_evidence | 3 | 0 | 0 | 0 | no |
| INC-8290_b0_none_r0 | b0 | completed | diagnosis | 4 | 0 | 0 | 0 | no |
| INC-8290_b1_none_r0 | b1 | completed | diagnosis | 4 | 0 | 0 | 0 | no |
| INC-8290_blackbox_none_r0 | blackbox | completed | diagnosis | 4 | 0 | 0 | 0 | no |

Nothing changed: v1 and v2 give the same retrieved-decisive counts for every run.

