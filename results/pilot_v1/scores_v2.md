# Decisive-evidence scores v1 vs v2: `pilot_v1` (pilot)

Generated 2026-11-20T03:57:00Z. v1 = `eval/metrics.py` sha256 `906d4b9ca8a8`; v2 = `scripts/score_v2.py` sha256 `97e16197b9b8`.
Rule (both): a decisive id counts only via the same id or an id listed explicitly in `decisive_equivalents`; no substring, prefix or similarity matching. v2 adds decisive evidence cited in the final report.

**PILOT results: not part of the formal evaluation.**

| run | system | status | report | decisive | retrieved v1 | retrieved v2 | cited v2 | changed |
|---|---|---|---|---|---|---|---|---|
| INC-0000_b0_none_r0 | b0 | completed | diagnosis | 4 | 0 | 0 | 0 | no |
| INC-0000_b1_none_r0 | b1 | completed | diagnosis | 4 | 0 | 0 | 0 | no |
| INC-0000_blackbox_none_r0 | blackbox | completed | insufficient_evidence | 4 | 0 | 0 | 0 | no |

Nothing changed: v1 and v2 give the same retrieved-decisive counts for every run.

