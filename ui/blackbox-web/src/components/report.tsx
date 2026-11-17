import type { Claim, EvidenceBudget, FinalReport, InvestigationStep } from '../types'
import { INSUFFICIENT_HEADLINE } from '../types'
import { fmtNum } from '../format'
import { Badge, Card, ConfidenceBadge, Empty, ReportStatusBadge, Stat } from './ui'
import { CitationList, IdList } from './evidence'
import { CitationAuditTable, auditCounts } from './trace'
import { CausesWithLedger } from './analysis'

export function Headline({ report }: { report: FinalReport }) {
  // insufficient_evidence must read exactly as the contract headline
  const text = report.status === 'insufficient_evidence' ? INSUFFICIENT_HEADLINE : report.headline
  return (
    <div className={`headline headline-${report.status}`}>
      <ReportStatusBadge status={report.status} />
      <span className="headline-text">{text}</span>
    </div>
  )
}

const KIND_TONE = { factual: 'neutral', causal: 'info', tentative: 'muted' } as const

function ClaimRow({ c }: { c: Claim }) {
  return (
    <li className="claim">
      <div>
        <code>{c.claim_id}</code> <Badge tone={KIND_TONE[c.kind] ?? 'neutral'}>{c.kind}</Badge> {c.text}
      </div>
      <CitationList ids={c.evidence_ids} empty="no citations given" />
    </li>
  )
}

export function ReportBody({ report, steps = [] }: { report: FinalReport; steps?: InvestigationStep[] }) {
  const counts = auditCounts(report.citation_audit ?? [])
  return (
    <>
      <Card title="Ranked explanations">
        {steps.length > 0 && report.ranked_causes.length > 0 ? (
          <CausesWithLedger report={report} steps={steps} />
        ) : report.ranked_causes.length === 0 ? (
          <Empty>
            {report.status === 'insufficient_evidence'
              ? 'No cause ranked: the evidence retrieved did not support a reliable root cause.'
              : 'No ranked causes in this report.'}
          </Empty>
        ) : (
          <ol className="causes">
            {[...report.ranked_causes]
              .sort((a, b) => a.rank - b.rank)
              .map((c) => (
                <li key={c.rank} className="cause">
                  <div className="cause-head">
                    <span className="cause-rank">#{c.rank}</span>
                    <code className="cause-name">
                      {c.component}::{c.mechanism}
                    </code>
                    <ConfidenceBadge c={c.confidence} />
                  </div>
                  <CitationList ids={c.evidence_ids} />
                </li>
              ))}
          </ol>
        )}
      </Card>

      <Card title="Claims">
        {report.claims.length === 0 ? (
          <Empty>No claims.</Empty>
        ) : (
          (['factual', 'causal', 'tentative'] as const).map((kind) => {
            const list = report.claims.filter((c) => c.kind === kind)
            if (!list.length) return null
            return (
              <div key={kind} className="claim-group">
                <h3>
                  {kind} ({list.length})
                </h3>
                <ul className="claims">
                  {list.map((c) => (
                    <ClaimRow key={c.claim_id} c={c} />
                  ))}
                </ul>
              </div>
            )
          })
        )}
      </Card>

      <div className="two-col">
        <Card title="Next check">
          {report.next_check ? <p>{report.next_check}</p> : <Empty>None given.</Empty>}
          {report.unresolved_questions.length > 0 && (
            <>
              <h3>Unresolved questions</h3>
              <ul>
                {report.unresolved_questions.map((q, i) => (
                  <li key={i}>{q}</li>
                ))}
              </ul>
            </>
          )}
        </Card>
        <Card
          title={
            <>
              Suggested fix <Badge tone="warn">untested</Badge>
            </>
          }
        >
          {report.suggested_fix ? (
            <>
              <p>{report.suggested_fix}</p>
              <p className="muted small">Untested suggestion produced by the model. Not verified against the system.</p>
            </>
          ) : (
            <Empty>No fix suggested.</Empty>
          )}
        </Card>
      </div>

      <Card title="Citation audit">
        <div className="stats">
          <Stat label="citations" value={counts.total} />
          <Stat label="exact (raw)" value={counts.exact} />
          <Stat label="recovered" value={counts.recovered} hint="embedded / suffix match resolved to a retrieved id" />
          <Stat label="ambiguous" value={counts.ambiguous} hint="never resolved" />
          <Stat label="invalid" value={counts.invalid} hint="cited id was never retrieved" />
        </div>
        {report.invalid_citations.length > 0 && (
          <p>
            Invalid citations:{' '}
            {report.invalid_citations.map((c) => (
              <code key={c} className="bad-text">
                {c}{' '}
              </code>
            ))}
          </p>
        )}
        {report.normalized_citations.length > 0 && (
          <p>
            Normalized:{' '}
            {report.normalized_citations.map((c) => (
              <code key={c}>{c} </code>
            ))}
          </p>
        )}
        <CitationAuditTable audit={report.citation_audit ?? []} />
      </Card>
    </>
  )
}

export function EvidenceBudgetCard({ eb, system }: { eb: EvidenceBudget; system: string }) {
  return (
    <Card title={`Evidence budget (${system.toUpperCase()} single call)`}>
      <div className="stats">
        <Stat label="context tokens" value={fmtNum(eb.context_tokens)} />
        <Stat label="reserved" value={fmtNum(eb.reserved_tokens)} />
        <Stat label="evidence budget" value={fmtNum(eb.evidence_token_budget)} />
        <Stat label="evidence tokens available" value={fmtNum(eb.evidence_tokens_total)} />
        <Stat label="included records" value={fmtNum(eb.included_ids.length)} />
        <Stat label="omitted records" value={fmtNum(eb.omitted_ids.length)} />
        <Stat label="omitted tokens" value={fmtNum(eb.omitted_tokens)} />
        <Stat
          label="matched (B1)"
          value={eb.matched === null || eb.matched === undefined ? 'n/a' : eb.matched ? 'yes' : 'no'}
          hint="B1 only: false when truncation broke the same-evidence premise"
        />
      </div>
      {eb.matched === false && (
        <div className="callout callout-bad">Truncation broke the same-evidence premise for this B1 run.</div>
      )}
      <details>
        <summary>included ids ({eb.included_ids.length})</summary>
        <CitationList ids={eb.included_ids} empty="none" />
      </details>
      <details>
        <summary>omitted ids ({eb.omitted_ids.length.toLocaleString('en-US')})</summary>
        <IdList ids={eb.omitted_ids} />
      </details>
    </Card>
  )
}
