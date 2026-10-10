import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Cache, Limit, Local, Quota } from '../types'

// Quota, context and cost come from the engine itself ($.session.usage and
// session.measure) - the same live figures the status line is handed, and the
// reason this works in the desktop app, which never runs a status line command.
// Only today's tokens across every session and the count of running Claude
// Code processes need the machine's files, so those come from ccquota.py.

const quota = atom({ plugin: 'ccquota', key: 'quota' } as const, null)
const local = atom({ plugin: 'ccquota', key: 'local' } as const, null)
const cache = atom({ plugin: 'ccquota', key: 'cache' } as const, null)
const isHidden = atom({ plugin: 'ccquota', key: 'isHidden' } as const, false)

const LOCAL_EVERY_MS = 60_000

const LABEL: Record<string, string> = {
  five_hour: '5h',
  seven_day: '週',
  spend_limit: '花費上限',
}

function tone(pct: number | undefined): 'success' | 'warning' | 'error' | 'subtle' {
  if (pct === undefined) return 'subtle'
  if (pct >= 90) return 'error'
  if (pct >= 70) return 'warning'
  return 'success'
}

function human(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`
  return String(n)
}

function until(iso: string | undefined, now: number): string {
  if (!iso) return ''
  const s = Math.max(0, Math.floor((Date.parse(iso) - now) / 1000))
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d) return `${d}d${h}h`
  if (h) return `${h}h${String(m).padStart(2, '0')}m`
  return `${m}m`
}

function toQuota(u: {
  rateLimits: { kind: string; percentUsed: number; resetsAt?: string }[]
  context: { percent?: number }
  cost?: { usd: number }
}): Quota {
  const limits: Limit[] = u.rateLimits.map(r => ({
    kind: r.kind,
    percent: r.percentUsed,
    resetsAt: r.resetsAt,
  }))
  return { limits, contextPercent: u.context.percent, costUsd: u.cost?.usd }
}

let isFetching = false

// ccquota.py ships in the plugin's own folder, so an empty setting means that
// copy: a marketplace install and a clone both work without editing anything.
function scriptPath($: EngineInterface, configured: string): string {
  if (configured.trim()) return configured.trim()
  const root = $.plugin.root.replace(/[\\/]+$/, '').replace(/[\\/]\.claude-plugin$/, '')
  return `${root}/ccquota.py`
}

// The sweep is ~0.1 s but a hook should never wait on it: run it, and let the
// atom write redraw the band when it lands.
async function refreshLocal($: EngineInterface, python: string, configured: string) {
  if (isFetching) return
  isFetching = true
  const script = scriptPath($, configured)
  try {
    const r = await $.process.run([python, script, '--local'], { timeoutMs: 20_000 })
    if (r.exitCode !== 0) {
      $.ui.log(`ccquota: ${script} exited ${r.exitCode}: ${r.stderr.slice(0, 200)}`, { to: 'debug' })
      return
    }
    const j = JSON.parse(r.stdout)
    const next: Local = {
      todayTokens: Number(j.today_tokens) || 0,
      todayRequests: Number(j.today_requests) || 0,
      sessions: Number(j.sessions) || 0,
      codex: j.codex ?? null,
      at: await $.clock.now(),
    }
    await update($, local, () => next)
  } catch (err) {
    $.ui.log(`ccquota: local figures failed: ${String(err)}`, { to: 'debug' })
  } finally {
    isFetching = false
  }
}

export const register: Register = (on, options) => {
  const script = String(options?.script ?? '')
  const python = String(options?.python ?? 'python')
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.command.register({
      name: 'ccquota',
      description: 'Show or hide the quota band above the prompt',
    })
    void (async () => {
      try {
        const u = await $.session.usage()
        // A session.measure may land while this is in flight; its figures are
        // newer, so this first read only fills a band that is still empty.
        await update($, quota, q => q ?? toQuota(u))
      } catch (err) {
        $.ui.log(`ccquota: usage failed: ${String(err)}`, { to: 'debug' })
      }
    })()
    void refreshLocal($, python, script)
    $.clock.every(LOCAL_EVERY_MS, () => void refreshLocal($, python, script))
    return started
  })

  // Pushed by the engine whenever a window moves a whole point, the context
  // fill moves, or cost grows: no polling for the official figures.
  on('session.measure', async ($, e, next) => {
    await update($, quota, () => toQuota(e))
    return next(e)
  })

  // A finished turn is when today's total has just grown, and when the cache
  // ratio gets its next reading. Subagent turns run on their own transcripts
  // and caches, so only the main thread's count toward this session's ratio.
  on('turn.complete', async ($, e, next) => {
    const u = e.usage
    if (u && !e.agentId) {
      const read = u.cache_read_input_tokens
      const total = u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens
      if (total > 0) {
        await update($, cache, c => ({
          read: (c?.read ?? 0) + read,
          total: (c?.total ?? 0) + total,
          lastRead: read,
          lastTotal: total,
        }))
      }
    }
    void refreshLocal($, python, script)
    return next(e)
  })

  on('command.run', { command: 'ccquota' }, async $ => {
    const hidden = await update($, isHidden, h => !h)
    if (!hidden) void refreshLocal($, python, script)
    return { text: hidden ? 'ccquota band hidden.' : 'ccquota band shown.' }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey || (await read($, isHidden))) return next(e)

    const q = await read($, quota)
    const l = await read($, local)
    const c = await read($, cache)
    if (!q && !l) return next(e)

    const { Box, Text } = $.ui.resolve(e)
    const now = await $.clock.now()
    const limits = (q?.limits ?? []).filter(x => x.kind !== 'spend_limit' || x.percent > 0)
    const sep = <Text dimColor>{'  ·  '}</Text>

    return (
      <Box flexDirection="row" flexWrap="wrap">
        {limits.length === 0 && <Text dimColor>額度：尚無讀數</Text>}
        {limits.map((x, i) => (
          <Box key={x.kind} flexDirection="row">
            {i > 0 && sep}
            <Text dimColor>{LABEL[x.kind] ?? x.kind} </Text>
            <Text color={tone(x.percent)} bold>{`${x.percent}%`}</Text>
            {x.resetsAt && <Text dimColor>{` ${until(x.resetsAt, now)}`}</Text>}
          </Box>
        ))}
        {q?.contextPercent !== undefined && (
          <Box key="ctx" flexDirection="row">
            {sep}
            <Text dimColor>ctx </Text>
            <Text color={tone(q.contextPercent)}>{`${q.contextPercent}%`}</Text>
          </Box>
        )}
        {c && (
          <Box key="cache" flexDirection="row">
            {sep}
            <Text dimColor>cache </Text>
            {/* Low is the warning here: a miss re-sends the whole context. */}
            <Text color={tone(100 - Math.round((c.read / c.total) * 100))}>
              {`${Math.round((c.read / c.total) * 100)}%`}
            </Text>
            {c.lastTotal > 0 && c.lastRead === 0 && <Text color="warning">{' cold'}</Text>}
          </Box>
        )}
        {l && (
          <Box key="today" flexDirection="row">
            {sep}
            <Text dimColor>今日 </Text>
            <Text>{human(l.todayTokens)}</Text>
          </Box>
        )}
        {q?.costUsd !== undefined && (
          <Box key="cost" flexDirection="row">
            {sep}
            <Text dimColor>{`$${q.costUsd.toFixed(2)}`}</Text>
          </Box>
        )}
        {l && l.sessions > 0 && (
          <Box key="sessions" flexDirection="row">
            {sep}
            <Text color="success">●</Text>
            <Text>{` ${l.sessions}`}</Text>
          </Box>
        )}
        {l?.codex && (
          <Box key="codex" flexDirection="row">
            {sep}
            <Text dimColor>Codex </Text>
            {l.codex.stale && <Text dimColor>~</Text>}
            <Text dimColor>{LABEL.five_hour} </Text>
            <Text color={tone(l.codex.five_hour?.percent)}>
              {l.codex.five_hour ? `${l.codex.five_hour.percent}%` : '?'}
            </Text>
            <Text dimColor>{` ${LABEL.seven_day} `}</Text>
            <Text color={tone(l.codex.weekly?.percent)}>
              {l.codex.weekly ? `${l.codex.weekly.percent}%` : '?'}
            </Text>
          </Box>
        )}
      </Box>
    )
  })
}
