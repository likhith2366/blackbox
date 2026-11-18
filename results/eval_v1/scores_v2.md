# Decisive-evidence scores v1 vs v2: `eval_v1` (formal)

Generated 2026-11-20T03:57:03Z. v1 = `eval/metrics.py` sha256 `906d4b9ca8a8`; v2 = `scripts/score_v2.py` sha256 `97e16197b9b8`.
Rule (both): a decisive id counts only via the same id or an id listed explicitly in `decisive_equivalents`; no substring, prefix or similarity matching. v2 adds decisive evidence cited in the final report.

| run | system | status | report | decisive | retrieved v1 | retrieved v2 | cited v2 | changed |
|---|---|---|---|---|---|---|---|---|
| INC-3172_b0_none_r0 | b0 | timeout | - | 4 | 1 | 1 | 0 | no |
| INC-3172_b1_none_r0 | b1 | timeout | - | 4 | 0 | 0 | 0 | no |
| INC-3172_blackbox_none_r0 | blackbox | completed | insufficient_evidence | 4 | 0 | 0 | 0 | no |

Nothing changed: v1 and v2 give the same retrieved-decisive counts for every run.

