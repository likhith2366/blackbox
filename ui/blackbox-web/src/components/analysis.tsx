// Derived views over real run data (SystemOutput / InvestigationStep). Nothing here invents values:
// every row comes from a recorded tool request/result, ledger entry or report field.
import type { EvidenceRecord, FinalReport, Hypothesis, InvestigationStep, SystemOutput } from '../types'
import { fmtNum, fmtSeconds, fmtTime, parseDuplicates } from '../format'
import { Badge, Empty, ToolStatusBadge } from './ui'
import { CitationList, EvidenceChip } from './evidence'

const FAIL = new Set(['error', 'timeout', 'invalid_args', 'denied', 'budget_exceeded', 'unavailable'])

/** One row per requested tool call across all rounds, in order. */
export function ToolTimeline({ steps }: { steps: InvestigationStep[] }) {
  const rows = steps.flatMap((s) => {
    const dups = parseDuplicates(s.duplicate_calls ?? [])
    const res = new Map((s.tool_results ?? []).map((r) => [r.call_id, r]))
    return (s.tool_requests ?? []).map((q) => ({ step: s, q, r: res.get(q.call_id), cached: dups.get(q.call_id) }))
  })
  if (!rows.length) return <Empty>No tool calls were requested.</Empty>
  return (
    <div className="table-scroll">
      <table className="grid timeline-table">
        <thead>
          <tr>
            <th>round</th>
            <th>call</th>
            <th>tool</th>
            <th>arguments</th>
            <th>status</th>
            <th className="num">records</th>
            <th className="num">ms</th>
            <th>notes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ step, q, r, cached }) => (
            <tr key={q.call_id} className={r && FAIL.has(r.status) ? 'row-bad' : ''}>
              <td>{step.round}</td>
              <td>
                <code>{q.call_id}</code>
              </td>
              <td>
                <strong>{q.tool}</strong>
              </td>
              <td className="wrapcell">
                <code className="wrap small">{JSON.stringify(q.args)}</code>
              </td>
              <td>{r ? <ToolStatusBadge status={r.status} /> : <Badge tone="muted">no result</Badge>}</td>
              <td className="num">
                {r ? r.records.length : '—'}
                {r?.total_matches != null && r.total_matches > r.records.length && (
                  <span className="muted small"> / {fmtNum(r.total_matches)}</span>
                )}
              </td>
              <td className="num">{r ? fmtNum(Math.round(r.elapsed_ms)) : '—'}</td>
              <td className="wrapcell small">
                {cached !== undefined && <Badge tone="warn">cache hit = {cached}</Badge>}{' '}
                {r?.truncated && <Badge tone="warn">truncated</Badge>} {r?.error && <span className="error-text">{r.error}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Tool failures, refusals, unavailable sources and empty answers: evidence the run could not get. */
export function MissingEvidence({ steps, output }: { steps: InvestigationStep[]; output?: SystemOutput }) {
  const rows = steps.flatMap((s) =>
    (s.tool_results ?? [])
      .filter((r) => FAIL.has(r.status) || r.status === 'empty')
      .map((r) => ({ round: s.round, r, req: (s.tool_requests ?? []).find((q) => q.call_id === r.call_id) })),
  )
  const omitted = output?.evidence_budget?.omitted_ids ?? []
  const parseFails = steps.filter((s) => !s.parse_ok)
  if (!rows.length && !omitted.length && !parseFails.length && !(output?.unauthorized_attempts?.length ?? 0))
    return <Empty>Every tool call returned records; nothing was refused, unavailable or omitted.</Empty>
  return (
    <div>
      {rows.length > 0 && (
        <ul className="plain">
          {rows.map(({ round, r, req }) => (
            <li key={r.call_id}>
              <span className="muted small">round {round}</span> <code>{r.call_id}</code> <strong>{r.tool}</strong>{' '}
              <ToolStatusBadge status={r.status} />{' '}
              {req && <code className="small wrap">{JSON.stringify(req.args)}</code>}{' '}
              {r.error ? <span className="error-text small">{r.error}</span> : r.status === 'empty' && <span className="muted small">answered, nothing matched</span>}
            </li>
          ))}
        </ul>
      )}
      {parseFails.length > 0 && (
        <p>
          <Badge tone="bad">model output unparseable</Badge> in round(s) {parseFails.map((s) => s.round).join(', ')}
        </p>
      )}
      {(output?.unauthorized_attempts?.length ?? 0) > 0 && (
        <p>
          <Badge tone="bad">refused</Badge> {output!.unauthorized_attempts.length} request(s) for tools outside the allowlist
          (executed: {output!.unauthorized_executed})
        </p>
      )}
      {omitted.length > 0 && (
        <p>
          <Badge tone="warn">omitted</Badge> {fmtNum(omitted.length)} record(s) did not fit the single-call context window (
          {fmtNum(output?.evidence_budget?.omitted_tokens)} tokens).
        </p>
      )}
    </div>
  )
}

function norm(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
}

/** Final ledger hypothesis that corresponds to a ranked cause (same component and mechanism text). */
function ledgerMatch(ledger: Hypothesis[], component: string, mechanism: string): Hypothesis | undefined {
  const c = norm(component)
  const m = norm(mechanism)
  return (
    ledger.find((h) => norm(h.component) === c && norm(h.mechanism) === m) ??
    ledger.find((h) => norm(h.component) === c && (norm(h.mechanism).includes(m) || m.includes(norm(h.mechanism))))
  )
}

/**
 * Ranked causes with supporting AND contradicting evidence. The report schema carries only the cause's
 * citations; contradicting evidence exists in the hypothesis ledger, so it is shown when (and only when)
 * a final-ledger hypothesis has the same component and mechanism. Rejected hypotheses are listed too.
 */
export function CausesWithLedger({ report, steps }: { report: FinalReport; steps: InvestigationStep[] }) {
  const ledger = steps.length ? (steps[steps.length - 1].hypotheses_after ?? []) : []
  const causes = [...report.ranked_causes].sort((a, b) => a.rank - b.rank)
  const rejected = ledger.filter((h) => h.status === 'rejected')
  return (
    <div>
      {causes.length === 0 ? (
        <Empty>No ranked cause in this report.</Empty>
      ) : (
        <ol className="causes">
          {causes.map((c) => {
            const h = ledgerMatch(ledger, c.component, c.mechanism)
            return (
              <li key={c.rank} className="cause">
                <div className="cause-head">
                  <span className="cause-rank">#{c.rank}</span>
                  <code className="cause-name">
                    {c.component}::{c.mechanism}
                  </code>
                  <Badge tone={c.confidence === 'high' ? 'info' : 'muted'}>{c.confidence} confidence (model-stated)</Badge>
                </div>
                <div className="ev-pair">
                  <div>
                    <h4>Supporting (cited in report)</h4>
                    <CitationList ids={c.evidence_ids} empty="no citations" />
                  </div>
                  <div>
                    <h4>Contradicting (from ledger)</h4>
                    {h ? (
                      <CitationList ids={h.contradicting} empty="none recorded" />
                    ) : (
                      <span className="muted small">no matching ledger hypothesis</span>
                    )}
                  </div>
                </div>
                {h && (
                  <p className="ledger-note">
                    Ledger hypothesis <code>{h.hypothesis_id}</code> ({h.status}); ledger support:{' '}
                    {h.supporting.length ? h.supporting.map((i) => <EvidenceChip key={i} id={i} />) : 'none'}
                  </p>
                )}
              </li>
            )
          })}
        </ol>
      )}
      {rejected.length > 0 && (
        <>
          <h4>Rejected hypotheses</h4>
          <ul className="plain">
            {rejected.map((h) => (
              <li key={h.hypothesis_id}>
                <code>{h.hypothesis_id}</code> <strong>{h.component}</strong> :: {h.mechanism} — contradicted by{' '}
                <CitationList ids={h.contradicting} empty="(no citation)" />
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

/** Plain Markdown report built only from the saved output (download, no server round trip). */
export function reportMarkdown(runId: string, o: SystemOutput): string {
  const r = o.report
  const lines: string[] = [`# BlackBox report: ${o.incident_id}`, '', `- run: \`${runId}\``, `- system: ${o.system}, ablation: ${o.ablation}`]
  lines.push(`- run status: ${o.status}${o.error ? ` (${o.error})` : ''}`)
  lines.push(`- runtime: ${fmtSeconds(o.wall_seconds)}, rounds: ${o.rounds_used}, tool calls: ${o.tool_calls_used}`)
  lines.push(`- code revision: ${String(o.config?.code_revision ?? '—')}, model: ${String(o.config?.model ?? '—')}`, '')
  if (!r) {
    lines.push('**No report was produced.**')
    return lines.join('\n')
  }
  lines.push(`## ${r.status.replace(/_/g, ' ')}`, '', r.headline, '')
  if (r.ranked_causes.length) {
    lines.push('## Ranked causes', '')
    for (const c of [...r.ranked_causes].sort((a, b) => a.rank - b.rank))
      lines.push(`${c.rank}. **${c.component}** :: ${c.mechanism} (model-stated confidence: ${c.confidence}); evidence: ${c.evidence_ids.map((i) => `\`${i}\``).join(', ') || 'none'}`)
    lines.push('')
  }
  if (r.claims.length) {
    lines.push('## Claims', '')
    for (const c of r.claims) lines.push(`- [${c.kind}] ${c.text} ${c.evidence_ids.map((i) => `\`${i}\``).join(', ')}`)
    lines.push('')
  }
  if (r.next_check) lines.push('## Next check', '', r.next_check, '')
  if (r.unresolved_questions.length) lines.push('## Unresolved questions', '', ...r.unresolved_questions.map((q) => `- ${q}`), '')
  if (r.suggested_fix) lines.push('## Suggested fix (UNTESTED)', '', r.suggested_fix, '')
  if (r.invalid_citations.length) lines.push('## Invalid citations removed', '', r.invalid_citations.map((i) => `\`${i}\``).join(', '), '')
  const cited = new Set([...r.ranked_causes.flatMap((c) => c.evidence_ids), ...r.claims.flatMap((c) => c.evidence_ids)])
  const recs: EvidenceRecord[] = (o.evidence_seen ?? []).filter((e) => cited.has(e.evidence_id))
  if (recs.length) {
    lines.push('## Cited evidence', '')
    for (const e of recs) lines.push(`### \`${e.evidence_id}\` (${e.source}${e.service ? `, ${e.service}` : ''}, ${fmtTime(e.event_time)})`, '', '```', e.content, '```', '')
  }
  return lines.join('\n')
}

export function downloadText(name: string, text: string, type = 'text/markdown') {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
