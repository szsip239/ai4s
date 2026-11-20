import { readFile } from 'node:fs/promises'
import { isAbsolute } from 'node:path'
import pkg from '../package.json' with { type: 'json' }
import manifest from '../widget/manifest.json' with { type: 'json' }
import { readSnapshot } from './snapshot.js'

export const name = 'ai4s-dashboard'
export const inject = ['mosaicBoard']

const widget = { id: manifest.id, version: manifest.version }
const installationId = 'installation.ai4s.snapshot'
const actionName = 'snapshot.read'
const main = manifest.schemas.data.properties.main
const emptyObject = { type: 'object', additionalProperties: false }
const capability = {
  schemaVersion: 1,
  ...widget,
  title: manifest.title,
  description: manifest.description,
  interfaceVersion: '1',
  upgradeRole: 'managed_reference',
  actions: {
    [actionName]: {
      input: { ...emptyObject, properties: { previous: main }, required: ['previous'] },
      output: main,
      effect: 'read',
      errors: [],
      connections: [],
    },
  },
  triggers: {},
  sharedConfig: { schema: emptyObject, defaults: {} },
  connectionRequirements: {},
}

async function widgetLibrary(readIntervalSeconds) {
  const template = {
    schemaVersion: 1, kind: 'widget', ...widget,
    title: manifest.title, description: manifest.description,
  }
  const [description, markup, styles, view] = await Promise.all(
    ['manifest.json', 'index.html', 'assets/card.css', 'assets/view.js'].map(
      (path) => readFile(new URL(`../widget/${path}`, import.meta.url), 'utf8'),
    ),
  )
  const html = markup.replace('/* card styles */', () => styles).replace('/* card view */', () => view)
  const files = [
    { path: 'manifest.json', bytes: Array.from(Buffer.from(description)) },
    { path: 'index.html', bytes: Array.from(Buffer.from(html)) },
  ]
  return {
    plugin: { packageName: pkg.name, version: pkg.version },
    catalog: [widget],
    templates: {
      async list(kind) {
        return !kind || kind === 'widget' ? [template] : []
      },
      async read({ kind, id, version }) {
        if (kind === 'widget' && id === widget.id && version === widget.version) {
          return { manifest: template, files }
        }
      },
    },
    compose({ placementId, createdAt }) {
      const base = {
        schemaVersion: 1,
        revision: 1,
        title: '读取 AI4S 统计',
        enabled: true,
        action: {
          installationId, name: actionName, connections: {},
          input: { previous: { kind: 'pointer', source: 'placement_data', placementId, pointer: '/main' } },
        },
        createdAt,
        updatedAt: createdAt,
      }
      const automations = [
        { ...base, id: `automation.ai4s.refresh.${placementId}`, trigger: { kind: 'widget_intent', placementId, name: 'refresh' } },
        { ...base, id: `automation.ai4s.interval.${placementId}`, trigger: { kind: 'interval', everyMs: readIntervalSeconds * 1000 } },
      ]
      const bindings = automations.map(({ id }) => ({
        schemaVersion: 1, id: `binding.${id}`, kind: 'automation_data',
        automationId: id, enabled: true, source: '', target: '/main',
      }))
      return { capabilityPackage: widget, automations, bindings }
    },
  }
}

export async function apply({ mosaicBoard }, {
  snapshotPath, readIntervalSeconds = 600, minAttempts = 10, minSuccessRatePct = 90,
} = {}) {
  if (typeof snapshotPath !== 'string' || !isAbsolute(snapshotPath)) {
    throw new Error('AI4S snapshotPath must be an absolute path')
  }
  if (!Number.isSafeInteger(readIntervalSeconds) || readIntervalSeconds < 1 || readIntervalSeconds > 2_147_483) {
    throw new Error('AI4S readIntervalSeconds must be an integer between 1 and 2147483')
  }
  if (!Number.isSafeInteger(minAttempts) || minAttempts < 1 ||
      !Number.isFinite(minSuccessRatePct) || minSuccessRatePct < 0 || minSuccessRatePct > 100) {
    throw new Error('AI4S channel thresholds require a positive sample count and a percentage between 0 and 100')
  }
  const library = await widgetLibrary(readIntervalSeconds)
  const service = mosaicBoard.registerCapability({
    id: installationId, manifest: capability, config: {}, installedAt: new Date().toISOString(),
  }, {
    async health() { return { status: 'healthy' } },
    invoke({ input, signal }) {
      return readSnapshot(snapshotPath, input.previous, { minAttempts, minSuccessRatePct, signal })
    },
    recovery() { return 'manual' },
    async subscribe() { throw new Error('AI4S snapshot has no Capability triggers') },
  })
  try {
    const registration = await mosaicBoard.registerWidgetLibrary(library)
    return () => { registration.dispose(); service.dispose() }
  } catch (error) {
    service.dispose()
    throw error
  }
}
