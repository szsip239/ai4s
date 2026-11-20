import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderCard, consoleHref } from '../widget/assets/view.js'

const current = {
  status: 'ok', asOf: '2026-11-20T15:55:00Z', day: '2026-11-20', today: true, stale: false,
  requests: 32, currency: 'CNY', cost: 4.625,
  channels: [
    { name: 'demo-healthy', attempts: 20, successRatePct: 95.5 },
    { name: 'demo-off', disabled: true, attempts: 10, successRatePct: 100 },
  ],
  anomalies: [{ name: 'demo-off', reason: '已停用' }],
  models: [],
  keys: [{ name: 'demo-key<&', cost: 3 }],
}

test('the card displays measured channel rates, actual currency and the two main numbers', () => {
  const html = renderCard(current, false)
  assert.match(html, /今日 · 23:55/)
  assert.match(html, /调用<\/span>.*32/)
  assert.match(html, /费用<\/span>.*CNY.*4.*\.63/)
  assert.match(html, /demo-healthy.*95\.5%/)
  assert.match(html, /demo-off.*100%.*已停用/)
  assert.doesNotMatch(html, /今日失败|失败率/)
})

test('privacy masks the Key name without hiding the cost or exposing the name in attributes', () => {
  assert.match(renderCard(current, false), /demo-key&lt;&amp;/)
  const html = renderCard(current, true)
  assert.match(html, /＊＊＊.*已隐藏.*CNY.*3\.00/)
  assert.doesNotMatch(html, /demo-key/)
})

test('no snapshot, unknown cost, previous date and expired data have distinct card states', () => {
  assert.match(renderCard({ status: 'empty' }), /还没有读到统计/)
  assert.match(renderCard({ status: 'error', error: 'missing' }), /读不到统计.*快照文件不存在/)
  const html = renderCard({
    ...current, cost: undefined, today: false, stale: true, status: 'error', error: 'invalid',
  })
  assert.match(html, /2026-11-20 · 23:55/)
  assert.match(html, /读取失败（快照格式有误），显示上次数据/)
  assert.match(html, /数据未更新/)
  assert.match(html, /费用<\/span>.*未知/)
  assert.doesNotMatch(html, /今日/)
})

test('only configured HTTP(S) addresses without credentials become card links', () => {
  assert.equal(consoleHref('https://console.example.invalid/overview'), 'https://console.example.invalid/overview')
  assert.equal(consoleHref('javascript:alert(1)'), undefined)
  assert.equal(consoleHref('https://user:password@console.example.invalid/'), undefined)
  assert.equal(consoleHref(), undefined)
})
