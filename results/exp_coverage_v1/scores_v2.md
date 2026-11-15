# Decisive-evidence scores v1 vs v2: `exp_coverage_v1` (development)

Generated 2026-11-20T03:57:01Z. v1 = `eval/metrics.py` sha256 `906d4b9ca8a8`; v2 = `scripts/score_v2.py` sha256 `97e16197b9b8`.
Rule (both): a decisive id counts only via the same id or an id listed explicitly in `decisive_equivalents`; no substring, prefix or similarity matching. v2 adds decisive evidence cited in the final report.

**DEVELOPMENT results: not part of the formal evaluation.**

| run | system | status | report | decisive | retrieved v1 | retrieved v2 | cited v2 | changed |
|---|---|---|---|---|---|---|---|---|
| after_r1 | blackbox | completed | diagnosis | 4 | 1 | 1 | 1 | no |
| after_r2 | blackbox | completed | diagnosis | 4 | 1 | 1 | 1 | no |
| before_r1 | blackbox | completed | insufficient_evidence | 4 | 0 | 0 | 0 | no |
| before_r2 | blackbox | completed | insufficient_evidence | 4 | 0 | 0 | 0 | no |

Nothing changed: v1 and v2 give the same retrieved-decisive counts for every run.

