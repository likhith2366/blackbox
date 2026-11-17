import type { CitationCheck, Hypothesis, InvestigationStep, ToolRequest, ToolResult } from '../types'
import { fmtNum, fmtSeconds, humanize, parseDuplicates } from '../format'
import { Badge, Empty, KVTable, ToolStatusBadge } from './ui'
import { CitationList, EvidenceChip } from './evidence'

// Structured data only. InvestigationStep.raw_output (free text incl. the model's rationale)
// is deliberately never rendered.

export function HypothesisLedger({ hyps }: { hyps: Hypothesis[] }) {
  if (!hyps.length) return <Empty>No hypotheses in the ledger.</Empty>
  return (
    <div className="table-scroll">
      <table className="grid">
        <thead>
          <tr>
            <th>id</th>
            <th>component :: mechanism</th>
            <th>status</th>
            <th>confidence</th>
            <th>supporting</th>
            <th>contradicting</th>
          </tr>
        </thead>
        <tbody>
          {hyps.map((h) => (
            <tr key={h.hypothesis_id}>
              <td>
                <code>{h.hypothesis_id}</code>
              </td>
              <td>
                <strong>{h.component}</strong> :: {h.mechanism}
                {h.note && <div className="muted small">{h.note}</div>}
              </td>
              <td>
                <Badge tone={h.status === 'supported' ? 'info' : h.status === 'rejected' ? 'muted' : 'neutral'}>
                  {h.status}
                </Badge>
              </td>
              <td>{h.confidence}</td>
              <td>
                <CitationList ids={h.supporting} empty="—" />
              </td>
              <td>
                <CitationList ids={h.contradicting} empty="—" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ArgList({ args }: { args: Record<string, unknown> }) {
  const e = Object.entries(args)
  if (!e.length) return <span className="muted">no args</span>
  return (
    <dl className="args">
      {e.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>
            <code className="wrap">{typeof v === 'string' ? v : JSON.stringify(v)}</code>
          </dd>
        </div>
      ))}
    </dl>
  )
}

function ToolCall({ req, res, cachedFrom }: { req?: ToolRequest; res?: ToolResult; cachedFrom?: string }) {
  const callId = req?.call_id ?? res?.call_id ?? '?'
  const tool = req?.tool ?? res?.tool ?? '?'
  return (
    <div className="toolcall">
      <div className="toolcall-head">
        <code>{callId}</code>
        <strong>{tool}</strong>
        {res ? <ToolStatusBadge status={res.status} /> : <Badge tone="muted">no result recorded</Badge>}
        {cachedFrom !== undefined && (
          <Badge tone="warn" title="identical call answered from the per-run cache; still counts against the budget">
            cache hit{cachedFrom ? ` (= ${cachedFrom})` : ''}
          </Badge>
        )}
        {res?.is_retry && <Badge tone="muted">retry</Badge>}
        {res && <span className="muted small">{fmtNum(Math.round(res.elapsed_ms))} ms</span>}
      </div>
      {req && <ArgList args={req.args} />}
      {res && (
        <div className="toolcall-result">
          <span>
            {res.records.length} record{res.records.length === 1 ? '' : 's'} returned
            {res.total_matches !== null && res.total_matches !== undefined && ` of ${fmtNum(res.total_matches)} matches`}
          </span>
          {res.truncated && (
            <Badge tone="warn" title="more matches existed than were returned">
              truncated
            </Badge>
          )}
          {res.error && <pre className="pre-wrap error-text">{res.error}</pre>}
          {res.records.length > 0 && (
            <div className="chips">
              {res.records.map((r, i) => (
                <EvidenceChip key={`${r.evidence_id}-${i}`} id={r.evidence_id} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export function CitationAuditTable({ audit }: { audit: CitationCheck[] }) {
  if (!audit.length) return <Empty>No citations audited.</Empty>
  return (
    <div className="table-scroll">
      <table className="grid">
        <thead>
          <tr>
            <th>where</th>
            <th>raw (as written)</th>
            <th>resolved</th>
            <th>method</th>
          </tr>
        </thead>
        <tbody>
          {audit.map((c, i) => (
            <tr key={i} className={c.method === 'invalid' || c.method === 'ambiguous' ? 'row-bad' : ''}>
              <td>{c.where}</td>
              <td>
                <code className="wrap">{c.raw}</code>
              </td>
              <td>{c.resolved ? <EvidenceChip id={c.resolved} /> : <span className="muted">unresolved</span>}</td>
              <td>
                <Badge
                  tone={c.method === 'exact' ? 'good' : c.method === 'invalid' || c.method === 'ambiguous' ? 'bad' : 'warn'}
                  title={c.method === 'exact' ? 'cited exactly' : c.resolved ? 'recovered from a non-exact citation' : undefined}
                >
                  {c.method === 'exact' ? 'exact' : c.resolved ? `recovered (${c.method})` : c.method}
                </Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function auditCounts(audit: CitationCheck[]) {
  let exact = 0,
    recovered = 0,
    ambiguous = 0,
    invalid = 0
  for (const c of audit) {
    if (c.method === 'exact') exact++
    else if (c.method === 'ambiguous') ambiguous++
    else if (c.method === 'invalid') invalid++
    else if (c.resolved) recovered++
  }
  return { exact, recovered, ambiguous, invalid, total: audit.length }
}

const STOP_TONE: Record<string, 'info' | 'warn' | 'bad' | 'good' | 'muted'> = {
  continue: 'muted',
  stop_diagnosis: 'info',
  stop_insufficient: 'warn',
  stop_no_fault: 'good',
  stop_budget: 'warn',
  stop_rounds: 'warn',
  stop_error: 'bad',
  stop_timeout: 'bad',
}

const CONTEXT_HEADLINE = ['prompt_tokens_est', 'prompt_tokens_actual', 'notebook_tokens', 'notebook_budget']

export function RoundCard({ step, defaultOpen = false }: { step: InvestigationStep; defaultOpen?: boolean }) {
  const dups = parseDuplicates(step.duplicate_calls ?? [])
  const resultsById = new Map((step.tool_results ?? []).map((r) => [r.call_id, r]))
  const requested = new Set((step.tool_requests ?? []).map((r) => r.call_id))
  const orphanResults = (step.tool_results ?? []).filter((r) => !requested.has(r.call_id))
  const ctx = step.context ?? {}
  const hasCitationIssues =
    (step.invalid_citations?.length ?? 0) + (step.normalized_citations?.length ?? 0) + (step.citation_audit?.length ?? 0) > 0

  return (
    <details className="round" open={defaultOpen}>
      <summary className="round-head">
        <span className="round-num">Round {step.round}</span>
        <span>
          model decision: <strong>{step.model_decision || '—'}</strong>
        </span>
        <Badge tone={STOP_TONE[step.stop_decision] ?? 'neutral'}>{humanize(step.stop_decision)}</Badge>
        <span className="muted small">
          {(step.tool_requests ?? []).length} tool call{(step.tool_requests ?? []).length === 1 ? '' : 's'}
          {dups.size > 0 && ` · ${dups.size} cached`} · {fmtNum(step.prompt_tokens)} prompt / {fmtNum(step.completion_tokens)}{' '}
          completion tokens · {fmtSeconds(step.model_seconds)}
        </span>
        {!step.parse_ok && <Badge tone="bad">parse error</Badge>}
      </summary>

      <div className="round-body">
        {!step.parse_ok && step.parse_error && (
          <div className="callout callout-bad">
            <strong>Model output could not be parsed:</strong> <span className="pre-wrap">{step.parse_error.slice(0, 400)}</span>
          </div>
        )}

        {(step.orchestration_rules ?? []).length > 0 && (
          <div className="callout">
            <strong>Orchestration rules applied</strong>
            <ul>
              {step.orchestration_rules.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        )}

        <h4>Tool calls</h4>
        {(step.tool_requests ?? []).length === 0 && orphanResults.length === 0 ? (
          <Empty>No tool calls this round.</Empty>
        ) : (
          <div className="toolcalls">
            {(step.tool_requests ?? []).map((req) => (
              <ToolCall key={req.call_id} req={req} res={resultsById.get(req.call_id)} cachedFrom={dups.get(req.call_id)} />
            ))}
            {orphanResults.map((res) => (
              <ToolCall key={res.call_id} res={res} cachedFrom={dups.get(res.call_id)} />
            ))}
          </div>
        )}

        <h4>Hypothesis ledger after this round</h4>
        {(step.hypothesis_changes ?? []).length > 0 ? (
          <ul className="changes">
            {step.hypothesis_changes.map((c, i) => (
              <li key={i}>
                <code className="wrap">{c}</code>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted small">No ledger changes recorded this round.</p>
        )}
        <HypothesisLedger hyps={step.hypotheses_after ?? []} />

        {hasCitationIssues && (
          <>
            <h4>Citations this round</h4>
            {(step.invalid_citations ?? []).length > 0 && (
              <p>
                Invalid (never retrieved):{' '}
                {step.invalid_citations.map((c) => (
                  <code key={c} className="bad-text">
                    {c}{' '}
                  </code>
                ))}
              </p>
            )}
            {(step.normalized_citations ?? []).length > 0 && (
              <p>
                Normalized:{' '}
                {step.normalized_citations.map((c) => (
                  <code key={c}>{c} </code>
                ))}
              </p>
            )}
            {(step.citation_audit ?? []).length > 0 && <CitationAuditTable audit={step.citation_audit} />}
          </>
        )}

        <h4>Stopping decision</h4>
        <p>
          <Badge tone={STOP_TONE[step.stop_decision] ?? 'neutral'}>{humanize(step.stop_decision)}</Badge>{' '}
          {step.stop_reason || <span className="muted">no reason recorded</span>}
        </p>

        <h4>Context</h4>
        <div className="ctx-strip">
          {CONTEXT_HEADLINE.filter((k) => k in ctx).map((k) => (
            <span key={k}>
              <span className="muted">{humanize(k)}</span> <strong>{fmtNum(ctx[k])}</strong>
            </span>
          ))}
        </div>
        <details className="small">
          <summary>all context accounting fields</summary>
          <KVTable data={ctx} />
        </details>
      </div>
    </details>
  )
}

export function Trace({ steps, openLast = false, openAll = false }: { steps: InvestigationStep[]; openLast?: boolean; openAll?: boolean }) {
  if (!steps.length) return <Empty>No investigation rounds recorded (single-call systems have none).</Empty>
  return (
    <div className="rounds">
      {steps.map((s, i) => (
        <RoundCard key={`${s.round}-${openAll}`} step={s} defaultOpen={openAll || (openLast && i === steps.length - 1)} />
      ))}
    </div>
  )
}
