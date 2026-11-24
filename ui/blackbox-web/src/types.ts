// TypeScript mirror of blackbox/schemas.py (SCHEMA_VERSION 1.0) and CONTRACT.md section 12.
// Read-only view types. Nothing here describes scores, answer keys or fault labels:
// the API never returns them and the UI never derives them.

export type SourceName =
  | 'logs' | 'traces' | 'metrics' | 'deployments' | 'builds' | 'commits' | 'configs' | 'tickets'
export type Ablation = 'none' | 'no_jira' | 'no_provenance' | 'no_traces'
export type SystemName = 'b0' | 'b1' | 'blackbox'
export type ReportStatus = 'diagnosis' | 'insufficient_evidence' | 'no_fault_observed' | 'failed'
export type Confidence = 'low' | 'medium' | 'high'
export type ToolStatus =
  | 'ok' | 'empty' | 'unavailable' | 'error' | 'timeout' | 'invalid_args' | 'denied' | 'budget_exceeded'

export const INSUFFICIENT_HEADLINE = 'No reliable root cause identified.'

export interface Alert {
  alert_id: string
  title: string
  service: string
  signal: string
  condition: string
  observed_value?: number | null
  fired_at: string
}

export interface EvidenceRecord {
  evidence_id: string
  source: SourceName | string
  service?: string | null
  event_time?: string | null
  available_time?: string | null
  version?: string | null
  title: string
  content: string
  content_ref?: string | null
  attributes: Record<string, unknown>
  truncated: boolean
  fixture: boolean
}

export interface ToolRequest {
  call_id: string
  round: number
  tool: string
  args: Record<string, unknown>
}

export interface ToolResult {
  call_id: string
  tool: string
  status: ToolStatus | string
  records: EvidenceRecord[]
  total_matches?: number | null
  truncated: boolean
  error?: string | null
  elapsed_ms: number
  is_retry: boolean
}

export interface Hypothesis {
  hypothesis_id: string
  component: string
  mechanism: string
  status: 'active' | 'supported' | 'rejected'
  confidence: Confidence
  supporting: string[]
  contradicting: string[]
  note: string
}

export interface CitationCheck {
  where: string
  raw: string
  resolved?: string | null
  method: 'exact' | 'embedded' | 'suffix' | 'invalid' | 'ambiguous'
}

export interface InvestigationStep {
  round: number
  prompt_tokens?: number | null
  completion_tokens?: number | null
  model_seconds: number
  parse_ok: boolean
  parse_error?: string | null
  raw_output?: string // never rendered (may contain free-text rationale)
  model_decision: string
  hypotheses_before: Hypothesis[]
  hypotheses_after: Hypothesis[]
  hypothesis_changes: string[]
  tool_requests: ToolRequest[]
  tool_results: ToolResult[]
  invalid_citations: string[]
  normalized_citations: string[]
  duplicate_calls: string[]
  citation_audit: CitationCheck[]
  orchestration_rules: string[]
  context: Record<string, unknown>
  stop_decision: string
  stop_reason: string
}

export interface RankedCause {
  rank: number
  component: string
  mechanism: string
  confidence: Confidence
  evidence_ids: string[]
}

export interface Claim {
  claim_id: string
  text: string
  kind: 'factual' | 'causal' | 'tentative'
  evidence_ids: string[]
}

export interface FinalReport {
  incident_id: string
  system: SystemName
  status: ReportStatus
  headline: string
  ranked_causes: RankedCause[]
  claims: Claim[]
  unresolved_questions: string[]
  next_check: string
  suggested_fix?: string | null
  invalid_citations: string[]
  normalized_citations: string[]
  citation_audit: CitationCheck[]
}

export interface Usage {
  model_calls: number
  prompt_tokens: number
  completion_tokens: number
  model_seconds: number
}

export interface EvidenceBudget {
  context_tokens: number
  reserved_tokens: number
  evidence_token_budget: number
  evidence_tokens_total: number
  included_ids: string[]
  omitted_ids: string[]
  omitted_tokens: number
  matched?: boolean | null
}

export interface SystemOutput {
  schema_version: string
  system: SystemName
  incident_id: string
  ablation: Ablation
  status: 'completed' | 'failed' | 'timeout'
  error?: string | null
  report?: FinalReport | null
  steps: InvestigationStep[]
  evidence_seen: EvidenceRecord[]
  usage: Usage
  wall_seconds: number
  rounds_used: number
  tool_calls_used: number
  budget_violations: number
  unauthorized_attempts: ToolRequest[]
  unauthorized_executed: number
  evidence_budget?: EvidenceBudget | null
  context: Record<string, unknown>
  config: Record<string, unknown>
  prompt_versions: Record<string, string>
}

// ---------------------------------------------------------------- HTTP API (CONTRACT 12)
export interface Health {
  ok: boolean
  model?: string
  gpu?: { busy: boolean; holder?: string | null; since?: string | null }
  queue?: { queued: number; running: number }
}

export interface ProjectSource {
  source: string
  mode: 'live' | 'snapshot' | 'unavailable' | string
  healthy: boolean
  detail?: string | null
  error?: string | null
}

export interface Project {
  repo?: string | null
  sources: ProjectSource[]
}

export interface IncidentSummary {
  incident_id: string
  alert: Alert
  window_start: string
  window_end: string
  investigation_cutoff: string
  services: string[]
  available_sources: string[]
}

export interface RunSummary {
  run_id: string
  batch?: string | null
  path?: string | null
  system?: string | null
  incident_id?: string | null
  ablation?: string | null
  status?: string | null
  report_status?: string | null
  headline?: string | null
  wall_seconds?: number | null
  rounds?: number | null
  tool_calls?: number | null
  code_revision?: string | null
  mode?: string | null
  modified?: string | null
}

export interface RunDetail {
  run_id: string
  meta: Record<string, unknown> | null
  output: SystemOutput
}

export type JobStatus = 'queued' | 'waiting_gpu' | 'running' | 'cancel_requested' | 'completed' | 'failed' | 'cancelled'

export interface JobMeta {
  mode?: string
  config_file?: string
  model?: Record<string, unknown>
  code_revision?: string
  snapshot?: string
  snapshot_manifest_sha256?: string
  [extra: string]: unknown
}

export interface EvidenceSource {
  kind: 'github' | 'snapshot' | 'local_log_file' | string
  url?: string | null
  file?: string | null
  path?: string | null
  line?: string | number | null
  raw?: unknown
  [extra: string]: unknown
}


export interface InvestigationRequest {
  incident_id: string
  mode: 'snapshot' | 'live' | 'live_stack'
  alert?: string
  problem_description?: string
  window_start?: string
  window_end?: string
  followup_of?: string
}

export interface TimelineEvent {
  time: string
  kind: 'alert' | 'deploy' | 'build' | 'commit' | 'error_log' | string
  id?: string | null
  service?: string | null
  text: string
}

export interface IncidentTimeline {
  incident_id: string
  window_start: string
  investigation_cutoff: string
  events: TimelineEvent[]
  notes: string[]
}

export interface Job {
  job_id: string
  status: JobStatus | string
  created?: string | null
  started?: string | null
  ended?: string | null
  run_id?: string | null
  error?: string | null
  request?: Partial<InvestigationRequest> | null
  position?: number | null
  meta?: JobMeta | null
  retry_of?: string | null
  [extra: string]: unknown
}

export interface JobCreated {
  job_id: string
  status: string
  position?: number | null
}

export interface LiveSource {
  source: string
  status: 'live' | 'not configured' | 'unavailable' | 'error'
  detail: string
}
export interface LiveSources {
  checked_at: string
  sources: LiveSource[]
}

export interface ChatVerification {
  verdict: 'verified' | 'partly verified' | 'not verified' | 'no evidence'
  cited: number
  cited_known: number
  unknown_ids: string[]
  checked_tokens: string[]
  unsupported_tokens: string[]
}
export interface ChatToolUse {
  tool: string
  args: Record<string, unknown>
  status: string
  records: number
  error?: string | null
  backends: string[]
  ids: string[]
}
export interface ChatTurn {
  role: 'user' | 'assistant'
  text: string
  at: number
  citations: string[]
  confidence?: string | null
  verification?: ChatVerification | null
  tools: ChatToolUse[]
  seconds: number
}
export interface ChatSessionData {
  session_id: string
  title: string
  mode: string
  incident: IncidentSummary
  turns: ChatTurn[]
  evidence: Record<string, EvidenceRecord>
}
export interface ChatSessionSummary {
  session_id: string
  title: string
  mode: string
  incident_id: string
  turns: number
  open: boolean
}
