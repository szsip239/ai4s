import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { apply } from '../src/index.js'

const snapshot = JSON.parse(await readFile(new URL('../fixtures/snapshot.json', import.meta.url), 'utf8'))
const asOf = new Date(snapshot.asOf)

async function mount(t, config = {}) {
  const directory = await mkdtemp(join(tmpdir(), 'ai4s-widget-'))
  t.after(() => rm(directory, { recursive: true, force: true }))
  const snapshotPath = join(directory, 'snapshot.json')
  const host = {
    registerCapability(request, adapter) {
      this.request = request
      this.adapter = adapter
      return { dispose() {} }
    },
    async registerWidgetLibrary(library) {
      this.library = library
      return { dispose() {} }
    },
  }
  const dispose = await apply({ mosaicBoard: host }, { snapshotPath, ...config })
  t.after(dispose)
  return {
    host,
    write: (value) => writeFile(snapshotPath, typeof value === 'string' ? value : JSON.stringify(value)),
    read: (previous = { status: 'empty' }) => host.adapter.invoke({ input: { previous } }),
    remove: () => rm(snapshotPath),
  }
}

test('the widget library binds the local snapshot action to /main every 600 seconds', async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: asOf })
  const { host, write, read } = await mount(t)
  await write(snapshot)
  const [widget] = host.library.catalog
  const { automations, bindings } = host.library.compose({
    package: widget, placementId: 'card-1', createdAt: asOf.toISOString(),
  })
  const interval = automations.find(({ trigger }) => trigger.kind === 'interval')
  assert.equal(interval.trigger.everyMs, 600_000)
  assert.equal(interval.action.installationId, host.request.id)
  assert.deepEqual(interval.action.input.previous, {
    kind: 'pointer', source: 'placement_data', placementId: 'card-1', pointer: '/main',
  })
  assert.equal(bindings.find(({ automationId }) => automationId === interval.id).target, '/main')
  assert.deepEqual(await read(), {
    status: 'ok', asOf: snapshot.asOf, day: snapshot.day, today: true, stale: false,
    currency: 'USD', requests: 32, models: snapshot.models, keys: snapshot.keys,
    channels: snapshot.channels,
    anomalies: [{ name: 'demo-channel', reason: '成功率 85%' }],
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
  const [reference] = host.library.catalog
  const template = await host.library.templates.read({ kind: 'widget', ...reference })
  const manifest = JSON.parse(Buffer.from(template.files.find(({ path }) => path === 'manifest.json').bytes))
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
