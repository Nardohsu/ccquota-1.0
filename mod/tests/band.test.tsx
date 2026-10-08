import { expect, test } from 'claude-code/testing'

const BAND = {
  plugin: 'ccquota',
  component: 'AbovePrompt',
  props: { hasSurvey: false, isWorking: false, maxRows: 10 },
} as const

const MEASURE = {
  context: { window: 1_000_000, tokens: 120_000, percent: 12 },
  rateLimits: [
    { kind: 'five_hour', percentUsed: 87, resetsAt: '2099-01-01T00:00:00Z' },
    { kind: 'seven_day', percentUsed: 31 },
  ],
  cost: { usd: 3.2 },
  changed: ['context', 'rateLimits', 'cost'],
} as const

const LOCAL = JSON.stringify({
  today_tokens: 2_947_475, today_cache_read: 0, today_requests: 400, sessions: 3,
})

// Nothing stands beneath the plugin in a test, so the test answers for the
// engine: ccquota.py through process.run, and the session's own events.
function fakePython(on: any) {
  on('process.run', () => ({ value: { exitCode: 0, stdout: LOCAL, stderr: '' } }))
  on('session.start', (_$: any, e: any) => ({ cwd: e.cwd }))
  on('session.measure', (_$: any, e: any) => ({ changed: e.changed }))
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 1_000_000 }, rateLimits: [] } }))
  on('command.register', () => ({ value: undefined }))
  on('clock.every', () => ({ value: undefined }))
  on('ui.log', () => ({ value: undefined }))
  on('turn.complete', (_$: any, e: any) => ({ text: e.answer }))
  on('clock.now', () => ({ value: Date.parse('2026-10-08T00:00:00Z') }))
  // The engine's own band when no plugin draws one: an empty box.
  on('ui.render', ($: any, e: any) => {
    const { Box } = $.ui.resolve(e)
    return <Box key="engine" />
  })
}

test('the band shows quota, context, today and sessions on both surfaces', async ($: any, on) => {
  fakePython(on)
  await $.session.start({ source: 'startup', cwd: 'F:/Projects/ccquota' })
  await $.session.measure(MEASURE)

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...BAND, surface })
    expect(await ui.find({ type: 'Text', text: '87%' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: '31%' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: '12%' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: '2.9M' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: ' 3' })).toBeDefined()
    await ui.unmount()
  }
})

test('/ccquota hides the band, and again shows it', async ($: any, on) => {
  fakePython(on)
  await $.session.start({ source: 'startup', cwd: 'F:/Projects/ccquota' })
  await $.session.measure(MEASURE)

  const hidden = await $.command.run({ command: 'ccquota', args: '' })
  expect(hidden.text).toContain('hidden')
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...BAND, surface })
    expect(await ui.find({ type: 'Text', text: '87%' })).toBeUndefined()
    await ui.unmount()
  }

  const shown = await $.command.run({ command: 'ccquota', args: '' })
  expect(shown.text).toContain('shown')
  const ui = await $.ui.mount({ ...BAND, surface: 'desktop' })
  expect(await ui.find({ type: 'Text', text: '87%' })).toBeDefined()
  await ui.unmount()
})

function turn(read: number, write: number, input: number, agentId?: string) {
  return {
    answer: '', durationMs: 1000, isAborted: false, turnId: `t${read}-${write}`,
    ...(agentId ? { agentId } : {}),
    usage: {
      model: 'claude-opus-5', input_tokens: input, output_tokens: 10,
      cache_read_input_tokens: read, cache_creation_input_tokens: write,
    },
  }
}

test('cache hit rate sums the main thread and ignores subagents', async ($: any, on) => {
  fakePython(on)
  await $.session.start({ source: 'startup', cwd: 'F:/Projects/ccquota' })
  await $.session.measure(MEASURE)
  await $.turn.complete(turn(0, 90_000, 10_000))        // cold first turn: 0%
  await $.turn.complete(turn(95_000, 4_000, 1_000))     // warm: 95%
  await $.turn.complete(turn(0, 50_000, 0, 'sub-1'))    // subagent: not counted

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...BAND, surface })
    // (0 + 95k) / (100k + 100k) = 47.5% -> 48%
    expect(await ui.find({ type: 'Text', text: '48%' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: ' cold' })).toBeUndefined()
    await ui.unmount()
  }

  await $.turn.complete(turn(0, 100_000, 0))           // the cache expired
  const ui = await $.ui.mount({ ...BAND, surface: 'desktop' })
  expect(await ui.find({ type: 'Text', text: ' cold' })).toBeDefined()
  await ui.unmount()
})
