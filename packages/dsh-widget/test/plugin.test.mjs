import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { apply } from '../src/index.js'

const snapshot = JSON.parse(await readFile(new URL('../fixtures/snapshot.json', import.meta.url), 'utf8'))
const asOf = new Date(snapshot.asOf)

async function mount(t, config = {}, lifecycle = {}) {
  const directory = await mkdtemp(join(tmpdir(), 'ai4s-widget-'))
  t.after(() => rm(directory, { recursive: true, force: true }))
  const snapshotPath = join(directory, 'snapshot.json')
  const host = {
    registerCapability(request, adapter) {
      this.request = request
      this.adapter = adapter
      return { dispose() {} }
    },
    async registerExternalApp(contribution) {
      this.contribution = contribution
      return { dispose() {} }
    },
    state: () => ({ placements: [], packages: {} }),
    onChange: () => () => {},
    onAuthoringChange: () => () => {},
    ...lifecycle,
  }
  const logger = { warn: (...args) => assert.fail(args.join(' ')) }
  const dispose = await apply({ mosaicBoard: host, logger }, { snapshotPath, ...config })
  t.after(dispose)
  return {
    host,
    write: (value) => writeFile(snapshotPath, typeof value === 'string' ? value : JSON.stringify(value)),
    read: (previous = { status: 'empty' }) => host.adapter.invoke({ input: { previous } }),
    remove: () => rm(snapshotPath),
  }
}

test('the Launcher registers an empty page intent and the client resolves the owner URL', async (t) => {
  const { host } = await mount(t)
  const { plugin, launcher } = host.contribution
  const manifest = readManifest(host)
  assert.deepEqual(manifest.schemas.config, { type: 'object', additionalProperties: false })
  assert.deepEqual(manifest.schemas.intents['mosaic.external-app.open'], {
    type: 'object', additionalProperties: false,
  })
  const html = Buffer.from(launcher.source.files.find(({ path }) => path === 'index.html').bytes).toString()
  assert.match(html, /<button [^>]+>打开应用页<\/button>/)
  assert.match(html, /emitIntent\('mosaic\.external-app\.open', \{\}\)/)
  assert.doesNotMatch(html, /consoleUrl|https:\/\/ai4s\./)
  const script = await readFile(new URL('../src/client.js', import.meta.url), 'utf8')
  let provider
  const mosaicAppPages = {
    register(value) {
      provider = value
      return { dispose() { provider = undefined } }
    },
  }
  let stop
  runInNewContext(script, { window: { __ModuleLoader__: {
    load({ id, factory }) {
      assert.equal(id, plugin.packageName)
      const client = factory()
      assert.equal(client.__esModule, true)
      assert.equal(client.inject[0], 'mosaicAppPages')
      stop = client.apply({ mosaicAppPages })
    },
  } } })
  assert.equal(provider.id, launcher.target.payload.pageId)
  const signal = new AbortController().signal
  assert.equal((await provider.resolve(signal)).url, 'https://ai4s.echobyte.cn:8445/')
  stop()
  assert.equal(provider, undefined)
})

function readManifest(host) {
  const file = host.contribution.launcher.source.files.find(({ path }) => path === 'manifest.json')
  return JSON.parse(Buffer.from(file.bytes))
}

test('the Launcher binds a first read and scheduled reads to /main', { timeout: 1000 }, async (t) => {
  const id = 'ai4s.dashboard-launcher'
  const placement = { id, instanceId: 'component.ai4s.dashboard-launcher', revisionVersion: '1.0.0',
    createdAt: asOf.toISOString(), bindings: [] }
  const ready = Promise.withResolvers()
  const automations = []
  const owner = {
    async automationInspect() { throw Object.assign(new Error('missing'), { code: 'not_found' }) },
    async automationApply({ automation }) { automations.push(automation) },
    async boardApply({ operations }) { placement.bindings = operations[0].bindings },
    async executeWidgetIntent(intent) { ready.resolve(intent) },
  }
  const { host } = await mount(t, {}, {
    state: () => ({ packages: { [id]: { manifest: { id } } }, placements: [placement], revision: 1 }),
    loadBoard: () => ({ id: 'testing' }),
    onAuthoringChange(listener) { listener(owner); return () => {} },
  })
  const initial = await ready.promise
  assert.equal(initial.placementId, id)
  assert.equal(initial.name, 'refresh')
  const interval = automations.find(({ trigger }) => trigger.kind === 'interval')
  assert.equal(interval.trigger.everyMs, 600_000)
  assert.equal(interval.action.installationId, host.request.id)
  assert.deepEqual(interval.action.input.previous, {
    kind: 'pointer', source: 'placement_data', placementId: id, pointer: '/main',
  })
  assert.deepEqual(placement.bindings.map(({ automationId, target }) => [automationId, target]),
    automations.map(({ id }) => [id, '/main']))
})

test('the snapshot capability maps the fixture to the Launcher data shape', async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: asOf })
  const { write, read } = await mount(t)
  await write(snapshot)
  assert.deepEqual(await read(), {
    status: 'ok', asOf: snapshot.asOf, day: snapshot.day, today: true, stale: false,
    currency: 'USD', requests: 32, models: snapshot.models, keys: snapshot.keys,
    channels: snapshot.channels,
    anomalies: [{ name: 'demo-channel', reason: '成功率 85%' }],
    channelCount: 1, availableChannelCount: 1, failureRatePct: 15,
    failingChannels: [{ name: 'demo-channel', failureRatePct: 15 }],
  })
})

test('missing or damaged files retain only the last valid aggregate and recover on replacement', async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: asOf })
  const { write, read, remove } = await mount(t)
  assert.deepEqual(await read(), { status: 'error', error: 'missing' })
  await write('{"token":"demo-secret"')
  assert.deepEqual(await read(), { status: 'error', error: 'invalid' })

  await write(snapshot)
  const last = await read()
  await remove()
  assert.deepEqual(await read(last), { ...last, status: 'error', error: 'missing' })
  await write({ ...snapshot, channels: [], keys: null })
  assert.deepEqual(await read(last), { ...last, status: 'error', error: 'invalid' })

  await write({ ...snapshot, requests: 0, cost: 0, models: [], channels: [], keys: [] })
  const recovered = await read(last)
  assert.equal(recovered.status, 'ok')
  assert.equal(recovered.error, undefined)
  assert.equal(recovered.requests, 0)
  assert.equal(recovered.cost, 0)
  assert.deepEqual(recovered.anomalies, [])
})

test('freshness and the gateway day use the successful timestamp and Shanghai midnight', async (t) => {
  const nearMidnight = { ...snapshot, asOf: '2026-11-20T15:55:00Z' }
  t.mock.timers.enable({ apis: ['Date'], now: new Date('2026-11-20T15:59:59Z') })
  const { write, read, remove } = await mount(t)
  await write(nearMidnight)
  const last = await read()
  assert.equal(last.today, true)
  assert.equal(last.stale, false)

  t.mock.timers.setTime(Date.parse('2026-11-20T16:14:59Z'))
  assert.equal((await read()).today, false)
  assert.equal((await read()).stale, false)
  t.mock.timers.setTime(Date.parse('2026-11-20T16:15:00Z'))
  await remove()
  const preserved = await read(last)
  assert.equal(preserved.today, false)
  assert.equal(preserved.stale, true)
  assert.equal(preserved.asOf, nearMidnight.asOf)
  assert.equal(preserved.channelCount, 1)
  assert.equal(preserved.availableChannelCount, 1)
  assert.equal(preserved.failureRatePct, 15)
  assert.deepEqual(preserved.failingChannels, [{ name: 'demo-channel', failureRatePct: 15 }])
})

test('counts all channels and weights the failure rate by attempts with measured results', async (t) => {
  const { write, read } = await mount(t)
  await write({
    ...snapshot,
    channels: [
      { name: 'demo-standard', disabled: false, attempts: 20, successRatePct: 95 },
      { name: 'demo-degraded', disabled: false, attempts: 20, successRatePct: 85 },
      { name: 'demo-paused', disabled: true, attempts: 5, successRatePct: 100 },
      { name: 'demo-small-sample', disabled: false, attempts: 1, successRatePct: 0 },
      { name: 'demo-awaiting-result', disabled: false, attempts: 0, successRatePct: null },
      { name: 'demo-idle', disabled: false, attempts: 0, successRatePct: null },
      { name: 'demo-paused-idle', disabled: true, attempts: 0, successRatePct: null },
    ],
  })
  const result = await read()
  assert.equal(result.channelCount, 7)
  assert.equal(result.availableChannelCount, 5)
  assert.ok(Math.abs(result.failureRatePct - 500 / 46) < 1e-9)
  assert.deepEqual(result.failingChannels, [
    { name: 'demo-small-sample', failureRatePct: 100 },
    { name: 'demo-degraded', failureRatePct: 15 },
    { name: 'demo-standard', failureRatePct: 5 },
  ])
})

test('zero traffic keeps the channel inventory and has no failure rate', async (t) => {
  const { write, read } = await mount(t)
  await write({
    ...snapshot, requests: 0,
    channels: [
      { name: 'idle', disabled: false, attempts: 0, successRatePct: null },
      { name: 'paused', disabled: true, attempts: 0, successRatePct: null },
    ],
  })
  const result = await read()
  assert.equal(result.channelCount, 2)
  assert.equal(result.availableChannelCount, 1)
  assert.equal(result.failureRatePct, undefined)
  assert.deepEqual(result.failingChannels, [])
  assert.deepEqual(result.anomalies, [{ name: 'paused', reason: '已停用' }])
})

test('ranks the top three and keeps only disabled or sufficiently sampled low-rate channels in pending', async (t) => {
  const { host, write, read } = await mount(t)
  await write({
    ...snapshot, token: 'demo-secret', failures: 9,
    models: [2, 6, 3, 8].map((requests) => ({ name: `model-${requests}`, requests, credential: 'demo-secret' })),
    keys: [2, 6, 3, 8].map((cost) => ({ name: `key-${cost}`, cost, value: 'demo-secret' })),
    channels: [
      { name: 'small-sample', disabled: false, attempts: 9, successRatePct: 20 },
      { name: 'at-threshold', disabled: false, attempts: 10, successRatePct: 90 },
      { name: 'never-used', disabled: false, attempts: 0, successRatePct: null },
      { name: 'disabled', disabled: true, attempts: 0, successRatePct: null },
      { name: 'below-threshold', disabled: false, attempts: 10, successRatePct: 89.5, rawError: 'demo-secret' },
    ],
  })
  const result = await read()
  assert.deepEqual(result.models.map(({ name }) => name), ['model-8', 'model-6', 'model-3'])
  assert.deepEqual(result.keys.map(({ name }) => name), ['key-8', 'key-6', 'key-3'])
  assert.deepEqual(result.anomalies, [
    { name: 'disabled', reason: '已停用' },
    { name: 'below-threshold', reason: '成功率 89.5%' },
  ])
  assert.doesNotMatch(JSON.stringify(result), /demo-secret|failures/)
  const manifest = readManifest(host)
  assert.deepEqual(manifest.summary.pending, ['anomalies'])
  assert.equal(manifest.summary.list.pointer, '/data/main/anomalies')
  assert.deepEqual(manifest.identity, ['/data/main/keys/*/name'])
})

test('applies configured thresholds to a measured zero rate and ignores missing rates', async (t) => {
  const { write, read } = await mount(t, { minAttempts: 2, minSuccessRatePct: 50, readIntervalSeconds: 5 })
  await write({
    ...snapshot,
    channels: [
      { name: 'zero', disabled: false, attempts: 2, successRatePct: 0 },
      { name: 'unknown', disabled: false, attempts: 2, successRatePct: null },
    ],
  })
  assert.deepEqual((await read()).anomalies, [{ name: 'zero', reason: '成功率 0%' }])
})

test('rejects a timestamp with a non-RFC3339 hour', async (t) => {
  const { write, read } = await mount(t)
  await write({ ...snapshot, asOf: '2026-11-20T24:00:00+08:00' })
  assert.deepEqual(await read(), { status: 'error', error: 'invalid' })
})
