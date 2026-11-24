import { useMemo, useState } from 'react'
import { api, downloadExport, errText } from '../api'
import { useLoad, href, useRoute } from '../hooks'
import type { RunDetail } from '../types'
import { fmtNum, fmtSeconds, fmtTime } from '../format'
import { Badge, Card, Empty, ErrorBox, FixtureBanner, KVTable, Loading, ReportStatusBadge, Stat, Tabs } from '../components/ui'
import { EvidenceExplorer, EvidenceIndex, RunContext, buildIndex } from '../components/evidence'
import { MissingEvidence, ToolTimeline, downloadText, reportMarkdown } from '../components/analysis'
import { EvidenceBudgetCard, Headline, ReportBody } from '../components/report'
import { Trace } from '../components/trace'

export function RunDetailView({ runId }: { runId: string }) {
  const st = useLoad(() => api.run(runId), [runId])
  const { query } = useRoute()
  const batch = runId.includes('~') ? runId.split('~')[0] : undefined
  const summary = useLoad(() => api.runs(batch), [batch])
  const saved = summary.data?.find((r) => r.run_id === runId)
  return (
    <div className="page">
      {saved && (saved.path || saved.modified) && (
        <p className="source-line small muted">
          {saved.path && (
            <>
              Saved file <code>{saved.path}</code>
            </>
          )}
          {saved.modified && <> · saved {fmtTime(saved.modified)}</>}
        </p>
      )}
      {st.fromFixture && <FixtureBanner />}
      {st.loading && <Loading what={`run ${runId}`} />}
      {st.error && <ErrorBox error={st.error} onRetry={st.reload} />}
      {st.data && <RunDetailBody detail={st.data} fromFixture={st.fromFixture} expand={query.get('expand') === '1'} />}
    </div>
  )
}

export function RunDetailBody({ detail, fromFixture, expand = false }: { detail: RunDetail; fromFixture: boolean; expand?: boolean }) {
  const o = detail.output
  const index = useMemo(() => {
    const recs = [...(o.evidence_seen ?? [])]
    for (const s of o.steps ?? []) for (const r of s.tool_results ?? []) recs.push(...r.records)
    return buildIndex(recs)
  }, [o])
  const [exportErr, setExportErr] = useState<string | null>(null)
  const [allRounds, setAllRounds] = useState(expand)
  const report = o.report
  const batch = detail.run_id.includes('~') ? detail.run_id.split('~')[0] : ''
  const cited = useMemo(
    () => new Set([...(report?.ranked_causes ?? []).flatMap((c) => c.evidence_ids), ...(report?.claims ?? []).flatMap((c) => c.evidence_ids)]),
    [report],
  )

  const [tab, setTab] = useState('report')

  return (
    <RunContext.Provider value={fromFixture ? null : detail.run_id}>
    <EvidenceIndex.Provider value={index}>
      <Card
        className="run-head"
        title={
          <>
            Run <code>{detail.run_id}</code>
          </>
        }
        actions={
          <>
            <button
              type="button"
              onClick={() => {
                setExportErr(null)
                downloadExport(detail.run_id, fromFixture).catch((e) => setExportErr(errText(e)))
              }}
            >
              Export JSON
            </button>
            <button
              type="button"
              onClick={() => downloadText(`${detail.run_id.replace(/[~/\\]/g, '__')}.report.md`, reportMarkdown(detail.run_id, o))}
            >
              Export report (.md)
            </button>
            {batch && (
              <a className="button" href={href('compare', batch, o.incident_id)}>
                Compare systems
              </a>
            )}
            <a className="button" href={href('incidents', o.incident_id)}>
              Incident timeline
            </a>
            {!fromFixture && (
              <a className="button" href={`${href('start')}?incident=${encodeURIComponent(o.incident_id)}&followup=${encodeURIComponent(detail.run_id)}`}>
                Follow-up investigation
              </a>
            )}
          </>
        }
      >
        <div className="tags">
          <Badge tone="neutral">system {o.system}</Badge>
          <Badge tone="neutral">incident {o.incident_id}</Badge>
          <Badge tone="neutral">ablation {o.ablation}</Badge>
          <Badge tone={o.status === 'completed' ? 'good' : 'bad'}>run {o.status}</Badge>
          {!report && <ReportStatusBadge status={null} />}
        </div>
        {exportErr && <ErrorBox error={exportErr} />}
        {report ? (
          <Headline report={report} />
        ) : (
          <div className="headline headline-failed">
            <ReportStatusBadge status="failed" />
            <span className="headline-text">No report was produced.</span>
          </div>
        )}
        {o.error && <pre className="pre-wrap error-text">{o.error}</pre>}
        <div className="stats">
          <Stat label="runtime" value={fmtSeconds(o.wall_seconds)} />
          <Stat label="rounds" value={o.system === 'blackbox' ? `${o.rounds_used} / ${String(o.config?.max_rounds ?? '?')}` : 'single call'} />
          <Stat label="tool calls" value={o.system === 'blackbox' ? `${o.tool_calls_used} / ${String(o.config?.max_tool_calls ?? '?')}` : '—'} />
          <Stat label="evidence records seen" value={fmtNum(o.evidence_seen?.length ?? 0)} />
        </div>
      </Card>

      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { key: 'report', label: 'Report' },
          ...(o.system === 'blackbox' ? [{ key: 'trace', label: 'Investigation trace', count: o.steps.length }] : []),
          { key: 'evidence', label: 'Evidence', count: o.evidence_seen?.length ?? 0 },
          { key: 'details', label: 'Run details' },
        ]}
      />

      {tab === 'report' &&
        (report ? <ReportBody report={report} steps={o.steps ?? []} /> : <Empty>No final report: see the error above.</Empty>)}

      {tab === 'trace' && o.system === 'blackbox' && (
        <>
          <Card title={`Tool-call timeline (${o.tool_calls_used} counted against the budget)`}>
            <ToolTimeline steps={o.steps ?? []} />
          </Card>
          <Card title="Failures, refusals and missing evidence">
            <MissingEvidence steps={o.steps ?? []} output={o} />
          </Card>
          <Card
            title={`Rounds (${o.steps.length})`}
            actions={
              <button type="button" onClick={() => setAllRounds((v) => !v)}>
                {allRounds ? 'Collapse' : 'Expand'} all rounds
              </button>
            }
          >
            <Trace steps={o.steps} openAll={allRounds} />
          </Card>
          {(o.unauthorized_attempts?.length ?? 0) > 0 && (
            <Card title="Refused tool requests (not on the allowlist)">
              <pre className="pre-wrap small">{JSON.stringify(o.unauthorized_attempts, null, 1)}</pre>
            </Card>
          )}
        </>
      )}

      {tab === 'evidence' && (
        <>
          <Card title={`Evidence ${o.system === 'blackbox' ? 'retrieved' : 'supplied'} (${o.evidence_seen?.length ?? 0})`}>
            <EvidenceExplorer records={o.evidence_seen ?? []} cited={cited} />
          </Card>
          {o.evidence_budget && <EvidenceBudgetCard eb={o.evidence_budget} system={o.system} />}
        </>
      )}

      {tab === 'details' && (
        <>
          <Card title="Model usage and safety">
            <div className="stats">
              <Stat label="model calls" value={fmtNum(o.usage?.model_calls)} />
              <Stat label="prompt tokens" value={fmtNum(o.usage?.prompt_tokens)} />
              <Stat label="completion tokens" value={fmtNum(o.usage?.completion_tokens)} />
              <Stat label="model time" value={fmtSeconds(o.usage?.model_seconds)} />
              <Stat label="budget violations" value={fmtNum(o.budget_violations)} />
              <Stat label="unauthorized attempts / executed" value={`${o.unauthorized_attempts?.length ?? 0} / ${o.unauthorized_executed}`} />
            </div>
          </Card>
          {o.system !== 'blackbox' && (
            <Card title="Failures and missing evidence">
              <MissingEvidence steps={o.steps ?? []} output={o} />
            </Card>
          )}
          <div className="two-col">
            <Card title="Context accounting (report / single call)">
              <KVTable data={o.context ?? {}} />
            </Card>
            <Card title="Configuration">
              <KVTable data={{ ...o.config, prompt_versions: o.prompt_versions }} />
            </Card>
          </div>
          {detail.meta && (
            <Card title="Run metadata (meta.json)">
              <KVTable data={detail.meta} />
            </Card>
          )}
        </>
      )}
    </EvidenceIndex.Provider>
    </RunContext.Provider>
  )
}
