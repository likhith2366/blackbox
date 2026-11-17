# Gate v2: experimental evidence-sufficiency check (design, not implemented)

Status: design only. Not part of the frozen evaluation configuration, not part of the completed
before/after experiment (`runs/exp_coverage_v1`). It is an evidence-sufficiency check, not a
guarantee that a diagnosis is correct.

## Why
Gate v1 (`Coordinator._supports_diagnosis`) checks only that a hypothesis cites at least two
retrieved records, one of them from a runtime source. In `runs/exp_coverage_v1/after_r1` it
accepted `payment-svc :: "HTTP 500 errors"`, supported by two checkout-svc ERROR lines that
describe the symptom from the caller's side. Both citations were valid; neither established a
mechanism. Gate v1 measures citation and format validity, not causal support.

## Checks
Each check is labelled **deterministic** (code decides from records) or **semantic** (needs a
judgment about meaning; produced by the model or a reviewer, recorded separately, never treated
as proof).

1. **Attribution by an explicit relationship** (deterministic). At least one cited record must
   connect the blamed component to the symptom through a recorded relationship, not necessarily a
   record emitted by that component: a caller span whose child or peer is the component, a
   configuration or dependency mapping naming it, a deployment/commit/config change to it, or its
   own telemetry. A symptom line that merely mentions the component's name in an error message
   does not count on its own.
2. **A causal mechanism, not a symptom label** (semantic, with a deterministic floor).
   Deterministic floor: the mechanism text must not be only a status code, an error class or a
   restatement of the alert signal. Semantic part: the model states the chain cause -> effect with
   a cited record for each link; whether the chain is causal is a judgment, recorded as such and
   checked by human review, not by the gate.
3. **Temporal consistency where timestamps establish it** (deterministic). When the cause-side
   record has an *effective* time (deploy finished, config committed and deployed, restart, change
   applied), it must not be later than the first symptom record. A record *observed* after the
   incident (e.g. a config read at the cutoff) may describe a pre-existing state; observation time
   is not effective time. If no effective time exists, the check is reported as **uncertain**, not
   passed and not failed.
4. **Alternatives only when the evidence leaves them open** (deterministic trigger, semantic
   content). If the retrieved evidence is consistent with another component or mechanism (e.g.
   other services show errors, or another change landed in the window), the investigation must
   record a check that distinguishes them, citing evidence. The model is never required to invent
   an alternative and "reject" it without evidence just to pass.

Outcome per hypothesis: pass / fail (with the failing check) / uncertain (temporal evidence
missing). Uncertain does not block a diagnosis by itself; it is reported.

## Experiment (separate from evaluation)
- Flag `gate_version: v1 | v2` in RunConfig; v1 stays the frozen default.
- Development cases only (pilot and dev variants), never the frozen evaluation set, so the gate is
  not tuned on evaluation data.
- Measure on answerable and unanswerable cases separately: wrong diagnoses blocked, correct
  diagnoses wrongly blocked (false abstention), abstentions, and how often each check fires. A
  gate that removes wrong answers only by abstaining on answerable cases is a failure.
- Report results as preliminary; repeat runs as for the before/after experiment.
