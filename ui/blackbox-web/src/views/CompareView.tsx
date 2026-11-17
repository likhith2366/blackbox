import { useEffect, useMemo, useState } from 'react'
import { api, errText } from '../api'
import { href, useLoad } from '../hooks'
import { fmtNum, fmtSeconds } from '../format'
import type { RunDetail, RunSummary } from '../types'
import { Badge, Card, Empty, ErrorBox, FixtureBanner, Loading, ReportStatusBadge, Stat } from '../components/ui'
import { CitationList, EvidenceIndex, buildIndex } from '../components/evidence'

// Side-by-side view of saved BlackBox / B1 / B0 outputs for ONE incident in ONE batch.
// Valid comparison = same incident, same batch, and B1 built from that BlackBox run's evidence.
// No scores are shown: the API does not provide them and the UI does not compute them.

const ORDER = ['blackbox', 'b1', 'b0']

function pick(rows: RunSummary[], system: string): RunSummary | undefined {
  const cands = rows.filter((r) => r.system === system && (r.ablation ?? 'none') === 'none')
  return cands.find((r) => r.run_id.endsWith(`_${system}_none_r0`)) ?? cands[0]
}

export function CompareView({ batch, incident }: { batch: string; incident: string }) {
  const runs = useLoad(() => api.runs(batch), [batch])
  const chosen = useMemo(() => {
    const rows = (runs.data ?? []).filter((r) => r.incident_id === incident)
    return ORDER.map((s) => pick(rows, s)).filter((r): r is RunSummary => !!r)
  }, [runs.data, incident])
  const [details, setDetails] = useState<Record<string, RunDetail>>({})
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setDetails({})
    Promise.all(chosen.map((r) => api.run(r.run_id).then((d) => [r.run_id, d.data] as const))).then(
      (pairs) => !cancelled && setDetails(Object.fromEntries(pairs)),
      (e) => !cancelled && setErr(errText(e)),
    )
    return () => {
      cancelled = true
    }
  }, [chosen])

  const bb = chosen.find((r) => r.system === 'blackbox')
  const b1 = chosen.find((r) => r.system === 'b1')
  const bbOut = bb ? details[bb.run_id]?.output : undefined
  const b1Detail = b1 ? details[b1.run_id] : undefined
  const b1Source = (b1Detail?.meta?.b1_source_run as string | undefined) ?? null
  const bbIds = new Set((bbOut?.evidence_seen ?? []).map((e) => e.evidence_id))
  const b1Ids = b1Detail?.output.evidence_budget?.included_ids ?? b1Detail?.output.evidence_seen.map((e) => e.evidence_id) ?? []
  const sameEvidence = bbOut && b1Detail ? b1Ids.length === bbIds.size && b1Ids.every((i) => bbIds.has(i)) : null
  const sourceOk = bb && b1Source ? bb.run_id.split('~').pop() === b1Source : null

  const index = useMemo(
    () => buildIndex(Object.values(details).flatMap((d) => [...(d.output.evidence_seen ?? []), ...(d.output.steps ?? []).flatMap((s) => s.tool_results.flatMap((t) => t.records))])),
    [details],
  )

  return (
    <div className="page">
      <p>
        <a href={href('runs')}>← all runs</a>
      </p>
      {runs.fromFixture && <FixtureBanner />}
      <Card title={`Compare systems: ${incident} in batch ${batch}`}>
        {runs.loading && <Loading what="runs" />}
        {runs.error && <ErrorBox error={runs.error} onRetry={runs.reload} />}
        {err && <ErrorBox error={err} />}
        {!runs.loading && chosen.length === 0 && <Empty>No saved run for {incident} in batch {batch}.</Empty>}
        {chosen.length > 0 && (
          <div className="callout">
            <strong>Comparison validity</strong>
            <ul>
              <li>Same incident and batch: yes (runs selected by incident {incident}, batch {batch}, ablation none).</li>
              <li>
                B1 built from this BlackBox run:{' '}
                {!b1 ? (
                  <span className="muted">no B1 run in this batch</span>
                ) : sourceOk === null ? (
                  <Badge tone="warn">unknown (meta has no b1_source_run)</Badge>
                ) : sourceOk ? (
                  <Badge tone="good">yes ({b1Source})</Badge>
                ) : (
                  <Badge tone="bad">no ({b1Source})</Badge>
                )}
              </li>
              <li>
                B1 received the same evidence:{' '}
                {sameEvidence === null ? (
                  <span className="muted">—</span>
                ) : sameEvidence ? (
                  <Badge tone="good">yes ({b1Ids.length} records)</Badge>
                ) : (
                  <Badge tone="bad">
                    no (B1 {b1Ids.length} vs BlackBox {bbIds.size}
                    {b1Detail?.output.evidence_budget?.matched === false ? ', truncated' : ''})
                  </Badge>
                )}
              </li>
            </ul>
          </div>
        )}
      </Card>
      <EvidenceIndex.Provider value={index}>
        <div className="cmp-grid">
          {chosen.map((r) => {
            const d = details[r.run_id]
            const o = d?.output
            const rep = o?.report
            return (
              <div key={r.run_id} className="cmp-col">
                <h3>
                  {r.system?.toUpperCase()} <a href={href('runs', r.run_id)} className="small">{r.run_id.split('~').pop()}</a>
                </h3>
                {!d && <Loading what={r.run_id} />}
                {o && (
                  <>
                    <p>
                      <ReportStatusBadge status={rep?.status} /> {o.status !== 'completed' && <Badge tone="bad">run {o.status}</Badge>}
                    </p>
                    <p>{rep?.headline ?? 'No report.'}</p>
                    <div className="stats">
                      <Stat label="runtime" value={fmtSeconds(o.wall_seconds)} />
                      <Stat label="rounds / calls" value={o.system === 'blackbox' ? `${o.rounds_used} / ${o.tool_calls_used}` : 'single call'} />
                      <Stat label="evidence records" value={fmtNum(o.evidence_budget ? o.evidence_budget.included_ids.length : o.evidence_seen.length)} />
                      <Stat label="tokens" value={`${fmtNum(o.usage.prompt_tokens)}+${fmtNum(o.usage.completion_tokens)}`} />
                      <Stat label="invalid citations" value={rep ? rep.invalid_citations.length : '—'} />
                    </div>
                    {o.evidence_budget && o.evidence_budget.omitted_ids.length > 0 && (
                      <p className="small">
                        <Badge tone="warn">omitted</Badge> {fmtNum(o.evidence_budget.omitted_ids.length)} records did not fit the context window.
                      </p>
                    )}
                    <h4>Ranked explanations</h4>
                    {rep && rep.ranked_causes.length ? (
                      <ol className="causes">
                        {[...rep.ranked_causes].sort((a, b) => a.rank - b.rank).map((c) => (
                          <li key={c.rank}>
                            <code className="wrap">
                              {c.component}::{c.mechanism}
                            </code>
                            <CitationList ids={c.evidence_ids} />
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <Empty>No ranked cause.</Empty>
                    )}
                    {rep?.next_check && (
                      <p className="small">
                        <span className="muted">Next check:</span> {rep.next_check}
                      </p>
                    )}
                  </>
                )}
              </div>
            )
          })}
        </div>
      </EvidenceIndex.Provider>
    </div>
  )
}
