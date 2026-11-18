# Review worksheet: INC-4417_b1_none_r0

- Incident: `INC-4417` (scenario 7, healthy)
- System: `b1`  ablation: `none`  repeat: 0
- Harness status: **completed**

All automated verdicts below are PROVISIONAL. Reviewer fields are intentionally blank; two reviewers fill them independently, then resolve disagreements.

## Expected answer (evaluator-private)

- Expected status: `no_fault_observed`
- Component (accepted): -
- Mechanism (accepted): -
- Decisive evidence: metric:97faf26904, metric:d7fcb61222
- Distractors: deploy:d-558

## System report

- Status: `diagnosis`
- Headline: Most likely cause: checkout-svc - request rate spike due to payment-svc latency
- Cause #1: **checkout-svc** / request rate spike due to payment-svc latency (confidence high; cites metric:97faf26904)
- Cause #2: **payment-svc** / request rate spike due to internal processing (confidence medium; cites metric:709f0f13a6)
- Unresolved: Why did both services spike at the same time?
- Next check: Check for payment-svc latency spikes in the same time window

## Diagnosis verdict

- Automated (provisional): top-1 N/A, top-3 N/A, outcome correct no
- Reviewer A: ______  Reviewer B: ______  Resolved: ______

## Claims

Supplied evidence for support checks: `evidence_budget.included_ids` (2 ids). Split compound claims before grading.

### c1 (factual)

checkout-svc request rate spiked to 21 requests per second at 03:09:09Z

- Automated (provisional): cited, pending semantic review
- Cited evidence:
    - [metric:97faf26904] checkout-svc requests_per_s | checkout-svc requests_per_s (5.0s points, 2026-11-20T03:07:38.266Z .. 2026-11-20T03:10:15.423Z): min=1.8 max=21 last=4.8 03:07:38 2.2; 03:07:43 4.2; 03:07:48 3.8; 03:07:53 6.4; 03:07:58 5.4; 03:08:03 4.4; 03:08:08 5.6; 03:08:13 5.4; 03:08:18 5.6; 03:08:23 7; 03:08:25 1.8; 03:08:34 9.6; 03:08:39 4.6; 03:08:44 5; 03:08:49 6.2; 03:08:54 4.2; 03:08:59 5.6; 03:09:04 7.4; 03:09:09 21; 03:09:14 14; 03:09:20 13.8; 03:09:25 17.2; 03:09:30 17.6; 03:09:35 12.4; 03:09:40 14; 03 [...]
- Reviewer A (supported / partly / unsupported): ______ notes: ______
- Reviewer B (supported / partly / unsupported): ______ notes: ______
- Resolved: ______

### c2 (factual)

payment-svc request rate spiked to 21 requests per second at 03:09:09Z

- Automated (provisional): cited, pending semantic review
- Cited evidence:
    - [metric:709f0f13a6] payment-svc requests_per_s | payment-svc requests_per_s (5.0s points, 2026-11-20T03:07:34.327Z .. 2026-11-20T03:10:15.467Z): min=0.2 max=21 last=4.8 03:07:34 0.2; 03:07:39 3.6; 03:07:44 3.2; 03:07:49 4.6; 03:07:54 5.8; 03:07:59 5; 03:08:04 5; 03:08:09 6; 03:08:14 5.2; 03:08:19 6.2; 03:08:24 5.6; 03:08:29 1.4; 03:08:34 9.6; 03:08:39 4.6; 03:08:44 5; 03:08:49 6.2; 03:08:54 4.2; 03:08:59 5.6; 03:09:04 7.4; 03:09:09 21; 03:09:14 14; 03:09:20 13.8; 03:09:25 17.2; 03:09:30 17.6; 03:09:35 12.4; 03:09:4 [...]
- Reviewer A (supported / partly / unsupported): ______ notes: ______
- Reviewer B (supported / partly / unsupported): ______ notes: ______
- Resolved: ______

### c3 (causal)

Both services experienced a spike in request rates at the same time, suggesting a coordinated issue

- Automated (provisional): cited, pending semantic review
- Cited evidence:
    - [metric:97faf26904] checkout-svc requests_per_s | checkout-svc requests_per_s (5.0s points, 2026-11-20T03:07:38.266Z .. 2026-11-20T03:10:15.423Z): min=1.8 max=21 last=4.8 03:07:38 2.2; 03:07:43 4.2; 03:07:48 3.8; 03:07:53 6.4; 03:07:58 5.4; 03:08:03 4.4; 03:08:08 5.6; 03:08:13 5.4; 03:08:18 5.6; 03:08:23 7; 03:08:25 1.8; 03:08:34 9.6; 03:08:39 4.6; 03:08:44 5; 03:08:49 6.2; 03:08:54 4.2; 03:08:59 5.6; 03:09:04 7.4; 03:09:09 21; 03:09:14 14; 03:09:20 13.8; 03:09:25 17.2; 03:09:30 17.6; 03:09:35 12.4; 03:09:40 14; 03 [...]
    - [metric:709f0f13a6] payment-svc requests_per_s | payment-svc requests_per_s (5.0s points, 2026-11-20T03:07:34.327Z .. 2026-11-20T03:10:15.467Z): min=0.2 max=21 last=4.8 03:07:34 0.2; 03:07:39 3.6; 03:07:44 3.2; 03:07:49 4.6; 03:07:54 5.8; 03:07:59 5; 03:08:04 5; 03:08:09 6; 03:08:14 5.2; 03:08:19 6.2; 03:08:24 5.6; 03:08:29 1.4; 03:08:34 9.6; 03:08:39 4.6; 03:08:44 5; 03:08:49 6.2; 03:08:54 4.2; 03:08:59 5.6; 03:09:04 7.4; 03:09:09 21; 03:09:14 14; 03:09:20 13.8; 03:09:25 17.2; 03:09:30 17.6; 03:09:35 12.4; 03:09:4 [...]
- Reviewer A (supported / partly / unsupported): ______ notes: ______
- Reviewer B (supported / partly / unsupported): ______ notes: ______
- Resolved: ______

## Report citation audit (objective: how each written citation resolved)

| where | as written | resolved | method |
|---|---|---|---|
| cause1 | `metric:97faf26904` | metric:97faf26904 | exact |
| cause2 | `metric:709f0f13a6` | metric:709f0f13a6 | exact |
| claim c1 | `metric:97faf26904` | metric:97faf26904 | exact |
| claim c2 | `metric:709f0f13a6` | metric:709f0f13a6 | exact |
| claim c3 | `metric:97faf26904` | metric:97faf26904 | exact |
| claim c3 | `metric:709f0f13a6` | metric:709f0f13a6 | exact |

## Trajectory (objective)

- Tool calls requested: 0; accepted (budget consumed): 0 = backend executions 0 + cache hits 0; refused: 0
- Redundant: 0 (cache hits included); failed: 0
- Citations, ledger: 0 (exact 0, recovered 0, ambiguous 0, invalid 0); report: 6 (exact 6, recovered 0, ambiguous 0, invalid 0)
- Context: max prompt tokens 1659; truncated by server (reported) no; truncation (evaluator estimate) no; notebook records omitted (max) N/A; baseline omitted ids 0
- Orchestration interventions (coordinator, not model): 0 (first-tool 0, diagnosis gate 0)
- Decisive coverage: N/A (0/0); calls to first decisive: N/A
- Budget violations: 0; unauthorized attempted/executed: 0/0
- Tokens: 2035; wall s: 17.88
