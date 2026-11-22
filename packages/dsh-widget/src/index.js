import { readFile } from 'node:fs/promises'
import { isAbsolute } from 'node:path'
import pkg from '../package.json' with { type: 'json' }
import manifest from '../widget/manifest.json' with { type: 'json' }
import { readSnapshot } from './snapshot.js'
import { bindSnapshot, installationId, actionName } from './delivery.js'

export const name = 'ai4s-dashboard'
export const inject = ['mosaicBoard']

const widget = { id: manifest.id, version: manifest.version }
const upgrade = {
  packageId: widget.id, fromPackageVersion: '1.0.0', toPackageVersion: widget.version,
  toInstanceRevisionVersion: '2.0.0',
}
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

async function cardFonts() {
  const latin = 'U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD'
  const faces = [
    ['Geist', 'geist', 400], ['Geist', 'geist', 500], ['Geist', 'geist', 600],
    ['Geist Mono', 'geist-mono', 400], ['Geist Mono', 'geist-mono', 500],
  ]
  return (await Promise.all(faces.map(async ([family, file, weight]) => {
    const woff = await readFile(new URL(`../widget/assets/fonts/${file}-latin-${weight}-normal.woff2`, import.meta.url), 'base64')
    return `@font-face { font-family: '${family}'; font-weight: ${weight}; font-display: swap;
  src: url(data:font/woff2;base64,${woff}) format('woff2'); unicode-range: ${latin}; }`
  }))).join('\n')
}

async function launcherSource() {
  const [description, markup, styles, view, fonts] = await Promise.all([
    ...['manifest.json', 'index.html', 'assets/card.css', 'assets/view.js'].map(
      (path) => readFile(new URL(`../widget/${path}`, import.meta.url), 'utf8'),
    ),
    cardFonts(),
  ])
  const html = markup.replace('/* card fonts */', () => fonts)
    .replace('/* card styles */', () => styles).replace('/* card view */', () => view)
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
    const stopDelivery = bindSnapshot(mosaicBoard, upgrade, readIntervalSeconds, (error) =>
      logger.warn('AI4S 统计投递设置失败: %s', error.message))
    return () => { stopDelivery(); launcher.dispose(); service.dispose() }
  } catch (error) {
    launcher?.dispose()
    service.dispose()
    throw error
  }
}
