export function fmtSeconds(s: number | null | undefined): string {
  if (s === null || s === undefined || Number.isNaN(s)) return '—'
  if (s < 60) return `${s.toFixed(1)} s`
  const m = Math.floor(s / 60)
  return `${m} min ${Math.round(s - m * 60)} s`
}

export function fmtNum(n: unknown): string {
  if (typeof n === 'number') return n.toLocaleString('en-US')
  if (n === null || n === undefined || n === '') return '—'
  return String(n)
}

/** ISO timestamp shown as UTC "YYYY-MM-DD HH:MM:SS.mmm Z" (times in BlackBox are UTC). */
export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2}(?:\.\d+)?)/.exec(iso)
  return m ? `${m[1]} ${m[2]} UTC` : iso
}

export function shortId(id: string, max = 28): string {
  return id.length > max ? id.slice(0, max - 1) + '…' : id
}

export function humanize(key: string): string {
  return key.replace(/_/g, ' ')
}

/** "r4c1==r3c1" -> Map(r4c1 -> r3c1): calls answered from the per-run cache. */
export function parseDuplicates(dups: string[]): Map<string, string> {
  const m = new Map<string, string>()
  for (const d of dups) {
    const [a, b] = d.split('==')
    if (a) m.set(a.trim(), (b ?? '').trim())
  }
  return m
}
