// DEV FIXTURES: files under src/dev-fixtures/ are byte-for-byte copies of real files
// (runs/<batch>/<run>/output.json + meta.json, scenarios/snapshots/<id>/incident.json).
// Nothing here is invented. They are only used while the BlackBox API (port 8700) does
// not answer GET /api/health, and the app shows a "DEV FIXTURE" banner whenever they are.
import type { IncidentSummary, RunDetail, RunSummary, SystemOutput } from './types'

const outputs = import.meta.glob('./dev-fixtures/runs/*/output.json', {
  query: '?raw',
  import: 'default',
}) as Record<string, () => Promise<string>>
const metas = import.meta.glob('./dev-fixtures/runs/*/meta.json', {
  query: '?raw',
  import: 'default',
}) as Record<string, () => Promise<string>>
const incidents = import.meta.glob('./dev-fixtures/incidents/*.json', {
  import: 'default',
}) as Record<string, () => Promise<IncidentSummary>>

// directory name "<batch>__<run>" -> run id "<batch>~<run>" (same id scheme as the API: path under runs/ with "/" -> "~")
function dirOf(path: string): string {
  return path.split('/').slice(-2, -1)[0]
}
function runIdOf(dir: string): string {
  return dir.replace('__', '~')
}

async function loadDir(dir: string): Promise<{ raw: string; output: SystemOutput; meta: Record<string, unknown> | null }> {
  const outKey = `./dev-fixtures/runs/${dir}/output.json`
  const metaKey = `./dev-fixtures/runs/${dir}/meta.json`
  if (!outputs[outKey]) throw new Error(`No fixture for run ${runIdOf(dir)}`)
  const raw = await outputs[outKey]()
  const meta = metas[metaKey] ? (JSON.parse(await metas[metaKey]()) as Record<string, unknown>) : null
  return { raw, output: JSON.parse(raw) as SystemOutput, meta }
}

export async function fixtureRuns(): Promise<RunSummary[]> {
  const dirs = Object.keys(outputs).map(dirOf)
  const rows = await Promise.all(
    dirs.map(async (dir) => {
      const { output, meta } = await loadDir(dir)
      const runId = runIdOf(dir)
      const cfg = output.config as Record<string, unknown>
      return {
        run_id: runId,
        batch: runId.split('~')[0],
        path: `runs/${runId.replace('~', '/')}`,
        system: output.system,
        incident_id: output.incident_id,
        ablation: output.ablation,
        status: output.status,
        report_status: output.report?.status ?? null,
        headline: output.report?.headline ?? null,
        wall_seconds: output.wall_seconds,
        rounds: output.rounds_used,
        tool_calls: output.tool_calls_used,
        code_revision: (cfg?.code_revision as string) || ((meta?.code_revision as string) ?? null),
        mode: null,
      } satisfies RunSummary
    }),
  )
  return rows.sort((a, b) => a.run_id.localeCompare(b.run_id))
}

export async function fixtureRun(runId: string): Promise<RunDetail> {
  const { output, meta } = await loadDir(runId.replace('~', '__'))
  return { run_id: runId, meta, output }
}

export async function fixtureExport(runId: string): Promise<string> {
  return (await loadDir(runId.replace('~', '__'))).raw
}

export async function fixtureIncidents(): Promise<IncidentSummary[]> {
  const all = await Promise.all(Object.values(incidents).map((load) => load()))
  return all.sort((a, b) => a.incident_id.localeCompare(b.incident_id))
}
