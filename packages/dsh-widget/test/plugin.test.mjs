import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { runInNewContext } from 'node:vm'
import { apply } from '../src/index.js'

const snapshot = JSON.parse(await readFile(new URL('../fixtures/snapshot.json', import.meta.url), 'utf8'))
const asOf = new Date(snapshot.asOf)
const deliveredPeriods = {
  day: { requests: 32, attempts: 40, failed: 6, input: 4200, output: 650, cached: 1800 },
  week: { requests: 1262, attempts: 1376, failed: 18, input: 4800000, output: 612400, cached: 2900000 },
  month: { requests: 18400, attempts: 22998, failed: 1300, input: 1200000000, output: 2000000, cached: 49800000 },
}

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
    async applyPackageUpgrades() {},
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
  assert.doesNotMatch(html, /打开应用页|emitIntent/)
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

test('the Launcher retargets the existing placement and refreshes its already-bound snapshot', { timeout: 1000 }, async (t) => {
  const id = 'ai4s.dashboard-launcher'
  const placement = {
    id, instanceId: 'component.ai4s.dashboard-launcher', revisionVersion: '1.0.0',
    createdAt: asOf.toISOString(),
    bindings: [
      { id: `binding.automation.ai4s.refresh.${id}` },
      { id: `binding.automation.ai4s.interval.${id}` },
    ],
  }
  const refresh = Promise.withResolvers()
  let upgrade
  const { host } = await mount(t, {}, {
    state: () => ({
      placements: [placement],
      packages: placement.revisionVersion === '2.0.0' ? { [id]: { manifest: { id } } } : {},
    }),
    async applyPackageUpgrades(plans) {
      upgrade = plans
      placement.revisionVersion = '2.0.0'
    },
    onAuthoringChange(listener) {
      listener({ async executeWidgetIntent(intent) { refresh.resolve(intent) } })
      return () => {}
    },
  })
  assert.equal((await refresh.promise).placementId, id)
  assert.deepEqual(upgrade, [{
    packageId: id, fromPackageVersion: '1.0.0', toPackageVersion: readManifest(host).version,
    toInstanceRevisionVersion: '2.0.0',
  }])
})

test('the snapshot capability emits the three periods and ignores legacy usage fields', async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: asOf })
  const { write, read } = await mount(t)
  await write({ ...snapshot, cost: 'unused', models: null, keys: null })
  assert.deepEqual(await read(), {
    status: 'ok', asOf: snapshot.asOf, day: snapshot.day, today: true, stale: false,
    periods: deliveredPeriods,
    channels: snapshot.channels,
    anomalies: [{ name: 'demo-channel', reason: '成功率 85%' }],
  })
})

test('all three periods and their counters are required', async (t) => {
  const { write, read } = await mount(t)
  for (const periods of [
    undefined,
    { ...snapshot.periods, week: undefined },
    { ...snapshot.periods, day: { ...snapshot.periods.day, failed: undefined } },
    { ...snapshot.periods, month: { ...snapshot.periods.month, cachedTokens: -1 } },
  ]) {
    await write({ ...snapshot, currency: 'USD', requests: 32, cost: null, models: [], keys: [], periods })
    assert.deepEqual(await read(), { status: 'error', error: 'invalid' })
  }
})

test('missing or damaged files retain the last valid aggregate and recover on replacement', async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: asOf })
  const { write, read, remove } = await mount(t)
  assert.deepEqual(await read(), { status: 'error', error: 'missing' })
  await write('{"token":"demo-secret"')
  assert.deepEqual(await read(), { status: 'error', error: 'invalid' })

  await write(snapshot)
  const last = await read()
  await remove()
  assert.deepEqual(await read(last), { ...last, status: 'error', error: 'missing' })
  await write({ ...snapshot, periods: null })
  assert.deepEqual(await read(last), { ...last, status: 'error', error: 'invalid' })
  await write({ ...snapshot, channels: [] })
  assert.deepEqual(await read(last), { ...last, channels: [], anomalies: [] })
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
  assert.deepEqual(preserved.periods, deliveredPeriods)
})

test('pending includes only enabled channels with enough attempts and a rate below threshold', async (t) => {
  const { host, write, read } = await mount(t)
  await write({
    ...snapshot, currency: 'USD', requests: 32, cost: null, models: [], keys: [],
    token: 'demo-secret',
    channels: [
      { name: 'small-sample', disabled: false, attempts: 9, successRatePct: 20 },
      { name: 'at-threshold', disabled: false, attempts: 10, successRatePct: 90 },
      { name: 'never-used', disabled: false, attempts: 0, successRatePct: null },
      { name: 'disabled-idle', disabled: true, attempts: 0, successRatePct: null },
      { name: 'disabled-active', disabled: true, attempts: 20, successRatePct: 10 },
      { name: 'below-threshold', disabled: false, attempts: 10, successRatePct: 89.5, rawError: 'demo-secret' },
    ],
  })
  const result = await read()
  assert.deepEqual(result.anomalies, [{ name: 'below-threshold', reason: '成功率 89.5%' }])
  assert.equal(result.channels.find(({ name }) => name === 'disabled-active').disabled, true)
  assert.doesNotMatch(JSON.stringify(result), /demo-secret/)
  const manifest = readManifest(host)
  assert.deepEqual(manifest.summary.pending, ['anomalies'])
  assert.equal(manifest.summary.list.pointer, '/data/main/anomalies')
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
