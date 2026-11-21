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

const task = await taskSpace('codex ai4s 151 narrow layout')
const page = task.page('p1')
console.log('Task space', task.spaceId)

function fixture(count) {
  const failingChannels = Array.from({ length: count }, (_, index) => ({
    name: `provider-west-dashboard-${index + 1}`,
    failureRatePct: index ? 15 : 100,
  }))
  return {
    status: 'ok', stale: true, today: true, asOf: '2026-11-21T01:16:00Z', day: '2026-11-21',
    channelCount: count + 4, availableChannelCount: count + 2, failureRatePct: 10.9,
    channels: failingChannels.map(({ name }, index) => ({ name, disabled: index === 1 })),
    anomalies: [], failingChannels, requests: 47, currency: 'CNY', cost: 4.625,
    models: [26, 12, 7].map((requests, index) => ({ name: `demo-model-${index + 1}`, requests })),
    keys: [],
  }
}

const failures = []
try {
  for (const [label, width, height, count] of [
    ['390 default card', 114, 476, 3],
    ['short card with more failures', 114, 340, 12],
    ['above model breakpoint', 101, 520, 3],
    ['extremely narrow card', 100, 520, 3],
  ]) {
    await page.cdp('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false })
    const file = join(temp, 'card.html')
    const bridge = `{ data: { main: ${JSON.stringify(fixture(count))} }, presentation: {},
      onDataChange() {}, onPresentationChange() {}, emitIntent() {} }`
    await writeFile(file, cardMarkup.replace('window.MosaicWidget', () => bridge))
    await page.goto(pathToFileURL(file).href + '?' + encodeURIComponent(label))
    await page.waitForSelector('.channels li')
    const layout = await page.evaluate(() => {
      const card = document.querySelector('.card')
      const content = document.querySelector('.content')
      const frame = card.getBoundingClientRect()
      const body = content.getBoundingClientRect()
      const inside = (element, area) => {
        if (!element?.getClientRects().length) return false
        const rect = element.getBoundingClientRect()
        return rect.top >= area.top && rect.bottom <= area.bottom && rect.left >= area.left && rect.right <= area.right
      }
      const names = [...document.querySelectorAll('.channels li .name')].filter((name) => name.getClientRects().length)
      const list = document.querySelector('.channels ul')
      const more = [...list.children].find((row) => /^\+\d+$/.test(row.textContent.trim()) && row.getClientRects().length)
      const metric = (label) => [...document.querySelectorAll('.metrics .muted')]
        .find((node) => node.textContent === label)?.parentElement
      return {
        button: inside(document.querySelector('.open-page'), frame),
        oneLine: names.every((name) => getComputedStyle(name).whiteSpace === 'nowrap'),
        ellipsis: names.some((name) => getComputedStyle(name).textOverflow === 'ellipsis' && name.scrollWidth > name.clientWidth),
        rows: names.length,
        visibleRows: names.filter((name) => inside(name, body) && inside(name, list.getBoundingClientRect())).length,
        hiddenCount: more ? Number(more.textContent.trim().slice(1)) : 0,
        moreVisible: more ? inside(more, body) && inside(more, list.getBoundingClientRect()) : false,
        health: ['渠道可用 / 总数', '近期失败率'].every((label) => inside(metric(label), body)),
        usage: ['调用', '费用'].every((label) => inside(metric(label), body)),
        healthFirst: metric('近期失败率').getBoundingClientRect().bottom <= list.getBoundingClientRect().top &&
          list.getBoundingClientRect().bottom <= metric('调用').getBoundingClientRect().top,
        models: inside(document.querySelector('.models'), body),
      }
    })
    console.log(label, JSON.stringify(layout))
    const expect = (condition, issue) => { if (!condition) failures.push(`${label}: ${issue}`) }
    expect(layout.button, 'the full application button must stay inside the card')
    expect(layout.health, 'both channel metrics must be visible')
    expect(layout.healthFirst, 'channel health and failures must precede calls and cost')
    expect(layout.oneLine && layout.ellipsis, 'long channel names must truncate on a single line')
    expect(layout.rows === layout.visibleRows && layout.rows >= 1, 'all listed failures must be visible')
    if (label === '390 default card') expect(layout.rows === 3, 'all three reported failures must fit on the default card')
    if (count === 3) expect(layout.rows === 3 || layout.moreVisible, 'show all failures or an overflow count')
    if (count === 12) expect(layout.moreVisible && layout.rows + layout.hiddenCount === count, 'show the exact +N count for hidden rows')
    if (height >= 476) {
      expect(layout.usage, 'calls and cost must fit at the default height')
      expect(layout.models === (width > 100), 'model ranking must stay visible above 100px')
    }
  }
  assert.deepEqual(failures, [])
} finally {
  await rm(temp, { recursive: true })
}
