import { readFile } from 'node:fs/promises'
import { isAbsolute } from 'node:path'
import pkg from '../package.json' with { type: 'json' }
import manifest from '../widget/manifest.json' with { type: 'json' }
import { readSnapshot } from './snapshot.js'
import { bindSnapshot, installationId, actionName } from './delivery.js'

export const name = 'ai4s-dashboard'
export const inject = ['mosaicBoard']

const widget = { id: manifest.id, version: manifest.version }
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

async function launcherSource() {
  const [description, markup, styles, view] = await Promise.all(
    ['manifest.json', 'index.html', 'assets/card.css', 'assets/view.js'].map(
      (path) => readFile(new URL(`../widget/${path}`, import.meta.url), 'utf8'),
    ),
  )
  const html = markup.replace('/* card styles */', () => styles).replace('/* card view */', () => view)
  return {
    label: manifest.title,
    files: [
      { path: 'manifest.json', bytes: Array.from(Buffer.from(description)) },
      { path: 'index.html', bytes: Array.from(Buffer.from(html)) },
    ],
  }
}

export async function apply({ mosaicBoard, logger }, {
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
  const source = await launcherSource()
  const target = { id: 'main', kind: 'mosaic.application-page', payload: { pageId: 'ai4s.dashboard' } }
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
  let launcher
  try {
    launcher = await mosaicBoard.registerExternalApp({
      plugin: { packageName: pkg.name, version: pkg.version },
      launcher: { source, target },
      activate() {},
    })
    const stopDelivery = bindSnapshot(mosaicBoard, widget, readIntervalSeconds, (error) =>
      logger.warn('AI4S 统计投递设置失败: %s', error.message))
    return () => { stopDelivery(); launcher.dispose(); service.dispose() }
  } catch (error) {
    launcher?.dispose()
    service.dispose()
    throw error
  }
}
