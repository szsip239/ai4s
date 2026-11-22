const { default: assert } = await import('node:assert/strict')
const { mkdtemp, rm, writeFile } = await import('node:fs/promises')
const { tmpdir } = await import('node:os')
const { join } = await import('node:path')
const { pathToFileURL } = await import('node:url')
const { apply } = await import(pathToFileURL(join(packageRoot, 'src/index.js')).href)

const temp = await mkdtemp(join(tmpdir(), 'ai4s-layout-'))
let cardMarkup
const stop = await apply({ mosaicBoard: {
  registerCapability: () => ({ dispose() {} }),
  registerExternalApp({ launcher }) {
    cardMarkup = Buffer.from(launcher.source.files.find(({ path }) => path === 'index.html').bytes).toString()
    return { dispose() {} }
  },
  onChange: () => () => {},
  onAuthoringChange: () => () => {},
} }, { snapshotPath: join(temp, 'unused.json') })
stop()

const task = await taskSpace('codex ai4s 153 layout')
const page = task.page('p1')
console.log('Task space', task.spaceId)

const period = { requests: 987654, attempts: 999999, failed: 123,
  input: 987600000, output: 876500000, cached: 1234500000 }
const fixture = {
  status: 'ok', today: true, asOf: '2026-11-22T06:20:00Z', day: '2026-11-22',
  periods: { day: period, week: period, month: period },
  channels: [
    ...Array.from({ length: 9 }, (_, index) => ({
      name: `a-very-long-virtual-channel-name-for-layout-review-${index}`,
      disabled: index === 1, attempts: 123456 - index * 1000,
    })),
    { name: 'idle-channel', disabled: false, attempts: 0 },
    { name: 'paused-channel', disabled: true, attempts: 0 },
  ],
  anomalies: [{ name: 'a-very-long-virtual-channel-name-for-layout-review-0', reason: '成功率 87%' }],
}

try {
  const file = join(temp, 'card.html')
  const bridge = `{ data: { main: ${JSON.stringify(fixture)} }, onDataChange() { return () => {} } }`
  await writeFile(file, cardMarkup.replace('window.MosaicWidget', () => bridge))
  const resize = (width, height) => page.cdp('Emulation.setDeviceMetricsOverride',
    { width, height, deviceScaleFactor: 1, mobile: false })
  await resize(392, 388)
  await page.goto(pathToFileURL(file).href)

  for (const [width, height, tiles, rows, note] of [
    [392, 388, 3, 4, '+5 在跑 · 1 空闲 · 1 停用'],
    [290, 388, 3, 4, '+5 在跑 · 1 空闲 · 1 停用'],
    [190, 318, 1, 2, '另 9 个渠道'],
    [114, 354, 1, 2, '另 9 个渠道'],
    [271, 388, 1, 4, '另 7 个渠道'],
    [272, 388, 3, 4, '+5 在跑 · 1 空闲 · 1 停用'],
  ]) {
    await resize(width, height)
    await page.waitForFunction(([tiles, rows]) =>
      document.querySelectorAll('.ai4s-period').length === tiles &&
      document.querySelectorAll('.ai4s-lane').length === rows, [tiles, rows], { timeout: 5000 })
    const layout = await page.evaluate(() => {
      const lanes = document.querySelector('.ai4s-lanes').getBoundingClientRect()
      const periods = document.querySelector('.ai4s-periods').getBoundingClientRect()
      const buttons = [...document.querySelectorAll('.ai4s-note > button')]
      const names = [...document.querySelectorAll('.ai4s-lane-name')]
      return {
        note: document.querySelector('.ai4s-channel-note > button').textContent,
        fullyVisible: buttons.every((button) => {
          const rect = button.getBoundingClientRect()
          return rect.left >= 0 && rect.right <= innerWidth &&
            rect.bottom <= (button.closest('.ai4s-periods') ? periods.bottom : lanes.bottom)
        }),
        ellipsis: names.some((name) => name.scrollWidth > name.clientWidth &&
          getComputedStyle(name).textOverflow === 'ellipsis'),
        ordered: lanes.bottom < periods.top && periods.bottom === innerHeight - 16,
      }
    })
    assert.deepEqual(layout, { note, fullyVisible: true, ellipsis: true, ordered: true }, `${width} × ${height}`)
    console.log(`${width} × ${height}: ${tiles} periods, ${rows} channel rows`)
  }

  await resize(114, 354)
  await page.waitForFunction(() => document.querySelectorAll('.ai4s-period').length === 1)
  for (const [trigger, text] of [
    ['.ai4s-channel-note > button', '在跑：'],
    ['.ai4s-rate button', '失败率按全部尝试计算'],
    ['.ai4s-lane-danger > button', '成功率 87%'],
  ]) {
    const tooltip = await page.evaluate((trigger) => document.querySelector(trigger).getAttribute('aria-describedby'), trigger)
    await page.hover(trigger)
    await page.waitForSelector('#' + tooltip, { state: 'visible' })
    const positioned = await page.evaluate((tooltip) => {
      const pop = document.getElementById(tooltip)
      const rect = pop.getBoundingClientRect()
      return rect.left === 12 && rect.right === innerWidth - 12 && rect.bottom === innerHeight - 12
        ? pop.textContent : ''
    }, tooltip)
    assert.ok(positioned.startsWith(text), `${tooltip} fits the narrow card`)
    await page.mouse.move(0, 0)
    await page.focus(trigger)
    await page.keyboard.press('Enter')
    await page.waitForSelector('#' + tooltip, { state: 'visible' })
    await page.keyboard.press('Escape')
    await page.waitForSelector('#' + tooltip, { state: 'hidden' })
  }

  const glyphs = await page.evaluate(async () => {
    await Promise.all([...document.fonts].map((face) => face.load()))
    return [...document.fonts].map((face) => [face.family, face.weight, face.status])
  })
  assert.deepEqual(glyphs, [
    ['Geist', '400', 'loaded'], ['Geist', '500', 'loaded'], ['Geist', '600', 'loaded'],
    ['Geist Mono', '400', 'loaded'], ['Geist Mono', '500', 'loaded'],
  ])
  console.log('Three annotation triggers and five embedded Latin font faces verified')
} finally {
  await rm(temp, { recursive: true })
  await task.finish({ keep: [] })
}
