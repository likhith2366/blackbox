# Decisive-evidence scores v1 vs v2: `ui_demo_dev` (development)

Generated 2026-11-20T03:56:48Z. v1 = `eval/metrics.py` sha256 `906d4b9ca8a8`; v2 = `scripts/score_v2.py` sha256 `97e16197b9b8`.
Rule (both): a decisive id counts only via the same id or an id listed explicitly in `decisive_equivalents`; no substring, prefix or similarity matching. v2 adds decisive evidence cited in the final report.

**DEVELOPMENT results: not part of the formal evaluation.**

| run | system | status | report | decisive | retrieved v1 | retrieved v2 | cited v2 | changed |
|---|---|---|---|---|---|---|---|---|
| 20261120T034855-a3febe | blackbox | completed | insufficient_evidence | 4 | 1 | 1 | 0 | no |
| 20261120T035254-b72b2e | blackbox | completed | insufficient_evidence | 4 | 1 | 1 | 1 | no |

Nothing changed: v1 and v2 give the same retrieved-decisive counts for every run.

