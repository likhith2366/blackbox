# Review worksheet: INC-2691_b1_none_r0

- Incident: `INC-2691` (scenario 3, unrelated_change)
- System: `b1`  ablation: `none`  repeat: 0
- Harness status: **completed**

All automated verdicts below are PROVISIONAL. Reviewer fields are intentionally blank; two reviewers fill them independently, then resolve disagreements.

## Expected answer (evaluator-private)

- Expected status: `diagnosis`
- Component (accepted): checkout-svc configuration (payment base_url), checkout-svc, checkout-svc config, config/checkout-svc.yaml
- Mechanism (accepted): configuration mismatch: checkout config points payment base_url at port 18112 where nothing listens (the sidecar is not deployed), so no payment call can connect; on this Windows host the refused connects surface as client timeouts after 2000ms (observed). The newer payment-svc release is unrelated, wrong endpoint, wrong port, misconfigured payment url, configuration mismatch, connection refused due to bad endpoint config, calls to a non-listening port time out
- Decisive evidence: config:checkout-svc@7595162, commit:7595162, deploy:d-545, log:df9fe83f35
- Distractors: deploy:d-546, commit:0791ee4, jira:DEMO-47

## System report

- Status: `diagnosis`
- Headline: Most likely cause: payment-svc - timeout on payment calls
- Cause #1: **payment-svc** / timeout on payment calls (confidence high; cites metric:42ce0389ed, log:c48ee3b3a3, trace:ab2ae8b16feacf80898ec832086cbba3, trace:538c5866020d19aa547c4c41bd6ce6a8, trace:08001f576a925e3917b8e2e9af07a90c, trace:63e6b30fae31efbfba19ec2307da2a6f)
- Cause #2: **checkout-svc** / retrying payment calls with timeout (confidence medium; cites metric:42ce0389ed, log:c48ee3b3a3, trace:ab2ae8b16feacf80898ec832086cbba3, trace:538c5866020d19aa547c4c41bd6ce6a8, trace:08001f576a925e3917b8e2e9af07a90c, trace:63e6b30fae31efbfba19ec2307da2a6f)
- Unresolved: Why is payment-svc timing out?
- Next check: Check payment-svc logs for errors or timeouts
- Suggested fix (untested): (untested) Increase timeout duration for payment-svc calls in checkout-svc

## Diagnosis verdict

- Automated (provisional): top-1 no, top-3 no, outcome correct no
- Reviewer A: ______  Reviewer B: ______  Resolved: ______

## Claims

Supplied evidence for support checks: `evidence_budget.included_ids` (10 ids). Split compound claims before grading.

### c1 (factual)

checkout-svc error rate increased due to payment-svc timeouts

- Automated (provisional): cited, pending semantic review
- Cited evidence:
    - [metric:42ce0389ed] checkout-svc error_rate | checkout-svc error_rate (5.0s points, 2026-11-20T02:50:22.028Z .. 2026-11-20T02:52:34.431Z): min=0 max=1 last=1 02:50:22 0; 02:50:27 0; 02:50:32 0; 02:50:37 0; 02:50:42 0; 02:50:47 0; 02:50:52 0; 02:50:58 0; 02:51:03 0; 02:51:08 0; 02:51:13 0; 02:51:18 0; 02:51:20 0; 02:51:29 1; 02:51:34 1; 02:51:39 1; 02:51:44 1; 02:51:49 1; 02:51:54 1; 02:51:59 1; 02:52:04 1; 02:52:09 1; 02:52:14 1; 02:52:19 1; 02:52:24 1; 02:52:29 1; 02:52:34 1
    - [log:c48ee3b3a3] ERROR checkout-svc: payment call timed out after 2000ms (x377 similar lines 2026-11-20T02:51:26.231Z .. 2026-11-20T02:52:39.169Z) | 2026-11-20T02:51:26.231Z ERROR checkout-svc payment call timed out after 2000ms {"trace_id":"ab2ae8b16feacf80898ec832086cbba3","span_id":"0b48de6623e27ca4","order_id":"ord-3300317","attempts":1}
- Reviewer A (supported / partly / unsupported): ______ notes: ______
- Reviewer B (supported / partly / unsupported): ______ notes: ______
- Resolved: ______

### c2 (factual)

payment-svc calls from checkout-svc are timing out after 2000ms

- Automated (provisional): cited, pending semantic review
- Cited evidence:
    - [trace:ab2ae8b16feacf80898ec832086cbba3] checkout-svc POST /orders 2013.29ms error | trace ab2ae8b16feacf80898ec832086cbba3 root=checkout-svc POST /orders start=2026-11-20T02:51:24.218Z duration=2013.29ms status=error spans=2 services=checkout-svc - checkout-svc POST /orders 2013.29ms error (HTTP 504) {"http.route":"/orders","order.id":"ord-3300317","http.status_code":504} - checkout-svc POST /charge 2011.29ms error (timeout after 2000ms) {"peer.service":"payment-svc","http.attempt":1,"http.timeout_ms":2000}
    - [trace:538c5866020d19aa547c4c41bd6ce6a8] checkout-svc POST /orders 2021.76ms error | trace 538c5866020d19aa547c4c41bd6ce6a8 root=checkout-svc POST /orders start=2026-11-20T02:51:24.241Z duration=2021.76ms status=error spans=2 services=checkout-svc - checkout-svc POST /orders 2021.76ms error (HTTP 504) {"http.route":"/orders","order.id":"ord-3300316","http.status_code":504} - checkout-svc POST /charge 2010.17ms error (timeout after 2000ms) {"peer.service":"payment-svc","http.attempt":1,"http.timeout_ms":2000}
    - [trace:08001f576a925e3917b8e2e9af07a90c] checkout-svc POST /orders 2020.62ms error | trace 08001f576a925e3917b8e2e9af07a90c root=checkout-svc POST /orders start=2026-11-20T02:51:24.260Z duration=2020.62ms status=error spans=2 services=checkout-svc - checkout-svc POST /orders 2020.62ms error (HTTP 504) {"http.route":"/orders","order.id":"ord-3300318","http.status_code":504} - checkout-svc POST /charge 2015.62ms error (timeout after 2000ms) {"peer.service":"payment-svc","http.attempt":1,"http.timeout_ms":2000}
    - [trace:63e6b30fae31efbfba19ec2307da2a6f] checkout-svc POST /orders 2017.11ms error | trace 63e6b30fae31efbfba19ec2307da2a6f root=checkout-svc POST /orders start=2026-11-20T02:51:24.264Z duration=2017.11ms status=error spans=2 services=checkout-svc - checkout-svc POST /orders 2017.11ms error (HTTP 504) {"http.route":"/orders","order.id":"ord-3300325","http.status_code":504} - checkout-svc POST /charge 2012.08ms error (timeout after 2000ms) {"peer.service":"payment-svc","http.attempt":1,"http.timeout_ms":2000}
- Reviewer A (supported / partly / unsupported): ______ notes: ______
- Reviewer B (supported / partly / unsupported): ______ notes: ______
- Resolved: ______

### c3 (causal)

checkout-svc is retrying payment calls but still timing out

- Automated (provisional): cited, pending semantic review
- Cited evidence:
    - [trace:ab2ae8b16feacf80898ec832086cbba3] checkout-svc POST /orders 2013.29ms error | trace ab2ae8b16feacf80898ec832086cbba3 root=checkout-svc POST /orders start=2026-11-20T02:51:24.218Z duration=2013.29ms status=error spans=2 services=checkout-svc - checkout-svc POST /orders 2013.29ms error (HTTP 504) {"http.route":"/orders","order.id":"ord-3300317","http.status_code":504} - checkout-svc POST /charge 2011.29ms error (timeout after 2000ms) {"peer.service":"payment-svc","http.attempt":1,"http.timeout_ms":2000}
    - [trace:538c5866020d19aa547c4c41bd6ce6a8] checkout-svc POST /orders 2021.76ms error | trace 538c5866020d19aa547c4c41bd6ce6a8 root=checkout-svc POST /orders start=2026-11-20T02:51:24.241Z duration=2021.76ms status=error spans=2 services=checkout-svc - checkout-svc POST /orders 2021.76ms error (HTTP 504) {"http.route":"/orders","order.id":"ord-3300316","http.status_code":504} - checkout-svc POST /charge 2010.17ms error (timeout after 2000ms) {"peer.service":"payment-svc","http.attempt":1,"http.timeout_ms":2000}
    - [trace:08001f576a925e3917b8e2e9af07a90c] checkout-svc POST /orders 2020.62ms error | trace 08001f576a925e3917b8e2e9af07a90c root=checkout-svc POST /orders start=2026-11-20T02:51:24.260Z duration=2020.62ms status=error spans=2 services=checkout-svc - checkout-svc POST /orders 2020.62ms error (HTTP 504) {"http.route":"/orders","order.id":"ord-3300318","http.status_code":504} - checkout-svc POST /charge 2015.62ms error (timeout after 2000ms) {"peer.service":"payment-svc","http.attempt":1,"http.timeout_ms":2000}
    - [trace:63e6b30fae31efbfba19ec2307da2a6f] checkout-svc POST /orders 2017.11ms error | trace 63e6b30fae31efbfba19ec2307da2a6f root=checkout-svc POST /orders start=2026-11-20T02:51:24.264Z duration=2017.11ms status=error spans=2 services=checkout-svc - checkout-svc POST /orders 2017.11ms error (HTTP 504) {"http.route":"/orders","order.id":"ord-3300325","http.status_code":504} - checkout-svc POST /charge 2012.08ms error (timeout after 2000ms) {"peer.service":"payment-svc","http.attempt":1,"http.timeout_ms":2000}
- Reviewer A (supported / partly / unsupported): ______ notes: ______
- Reviewer B (supported / partly / unsupported): ______ notes: ______
- Resolved: ______

## Report citation audit (objective: how each written citation resolved)

| where | as written | resolved | method |
|---|---|---|---|
| cause1 | `metric:42ce0389ed` | metric:42ce0389ed | exact |
| cause1 | `log:c48ee3b3a3` | log:c48ee3b3a3 | exact |
| cause1 | `trace:ab2ae8b16feacf80898ec832086cbba3` | trace:ab2ae8b16feacf80898ec832086cbba3 | exact |
| cause1 | `trace:538c5866020d19aa547c4c41bd6ce6a8` | trace:538c5866020d19aa547c4c41bd6ce6a8 | exact |
| cause1 | `trace:08001f576a925e3917b8e2e9af07a90c` | trace:08001f576a925e3917b8e2e9af07a90c | exact |
| cause1 | `trace:63e6b30fae31efbfba19ec2307da2a6f` | trace:63e6b30fae31efbfba19ec2307da2a6f | exact |
| cause2 | `metric:42ce0389ed` | metric:42ce0389ed | exact |
| cause2 | `log:c48ee3b3a3` | log:c48ee3b3a3 | exact |
| cause2 | `trace:ab2ae8b16feacf80898ec832086cbba3` | trace:ab2ae8b16feacf80898ec832086cbba3 | exact |
| cause2 | `trace:538c5866020d19aa547c4c41bd6ce6a8` | trace:538c5866020d19aa547c4c41bd6ce6a8 | exact |
| cause2 | `trace:08001f576a925e3917b8e2e9af07a90c` | trace:08001f576a925e3917b8e2e9af07a90c | exact |
| cause2 | `trace:63e6b30fae31efbfba19ec2307da2a6f` | trace:63e6b30fae31efbfba19ec2307da2a6f | exact |
| claim c1 | `metric:42ce0389ed` | metric:42ce0389ed | exact |
| claim c1 | `log:c48ee3b3a3` | log:c48ee3b3a3 | exact |
| claim c2 | `trace:ab2ae8b16feacf80898ec832086cbba3` | trace:ab2ae8b16feacf80898ec832086cbba3 | exact |
| claim c2 | `trace:538c5866020d19aa547c4c41bd6ce6a8` | trace:538c5866020d19aa547c4c41bd6ce6a8 | exact |
| claim c2 | `trace:08001f576a925e3917b8e2e9af07a90c` | trace:08001f576a925e3917b8e2e9af07a90c | exact |
| claim c2 | `trace:63e6b30fae31efbfba19ec2307da2a6f` | trace:63e6b30fae31efbfba19ec2307da2a6f | exact |
| claim c3 | `trace:ab2ae8b16feacf80898ec832086cbba3` | trace:ab2ae8b16feacf80898ec832086cbba3 | exact |
| claim c3 | `trace:538c5866020d19aa547c4c41bd6ce6a8` | trace:538c5866020d19aa547c4c41bd6ce6a8 | exact |
| claim c3 | `trace:08001f576a925e3917b8e2e9af07a90c` | trace:08001f576a925e3917b8e2e9af07a90c | exact |
| claim c3 | `trace:63e6b30fae31efbfba19ec2307da2a6f` | trace:63e6b30fae31efbfba19ec2307da2a6f | exact |

## Trajectory (objective)

- Tool calls requested: 0; accepted (budget consumed): 0 = backend executions 0 + cache hits 0; refused: 0
- Redundant: 0 (cache hits included); failed: 0
- Citations, ledger: 0 (exact 0, recovered 0, ambiguous 0, invalid 0); report: 22 (exact 22, recovered 0, ambiguous 0, invalid 0)
- Context: max prompt tokens 3298; truncated by server (reported) no; truncation (evaluator estimate) no; notebook records omitted (max) N/A; baseline omitted ids 0
- Orchestration interventions (coordinator, not model): 0 (first-tool 0, diagnosis gate 0)
- Decisive coverage: 0.00 (0/4); calls to first decisive: N/A
- Budget violations: 0; unauthorized attempted/executed: 0/0
- Tokens: 4194; wall s: 28.85
