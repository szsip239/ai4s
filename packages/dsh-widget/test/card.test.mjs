import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderCard } from '../widget/assets/view.js'

const current = {
  status: 'ok', asOf: '2026-11-20T15:55:00Z', day: '2026-11-20', today: true, stale: false,
  requests: 32, currency: 'CNY', cost: 4.625,
  channels: [
    { name: 'demo-healthy', attempts: 20, successRatePct: 95.5 },
    { name: 'demo-off', disabled: true, attempts: 10, successRatePct: 90 },
  ],
  channelCount: 2, availableChannelCount: 1, failureRatePct: 6.3333333,
  failingChannels: [{ name: 'demo-off', failureRatePct: 10 }, { name: 'demo-healthy', failureRatePct: 4.5 }],
  anomalies: [{ name: 'demo-off', reason: '已停用' }],
  models: [],
  keys: [{ name: 'demo-key<&', cost: 3 }],
}

test('the card displays the channel inventory, recent and per-channel failure rates, calls and cost', () => {
  const html = renderCard(current, false)
  assert.match(html, /今日 · 23:55/)
  assert.match(html, /调用<\/span>.*32/)
  assert.match(html, /费用<\/span>.*CNY.*4.*\.63/)
  assert.match(html, /渠道可用 \/ 总数<\/span>.*1 \/ 2/)
  assert.match(html, /近期失败率<\/span>.*6\.3%/)
  assert.match(html, /demo-healthy.*4\.5%/)
  assert.match(html, /demo-off.*10%.*已停用/)
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

test('the card distinguishes zero failures from no samples and from small nonzero rates', () => {
  const data = { ...current, channels: [], anomalies: [], failingChannels: [] }
  assert.match(renderCard({ ...data, failureRatePct: undefined }), /近期失败率<\/span>.*—/)
  assert.match(renderCard({ ...data, failureRatePct: undefined }), /暂无渠道样本/)
  assert.match(renderCard({ ...data, failureRatePct: 0 }), /暂无失败渠道/)
  assert.match(renderCard({ ...data, failureRatePct: 0.02 }), /近期失败率<\/span>.*&lt;0\.1%/)
})

test('idle disabled channels are counted but do not appear in the failure list', () => {
  const html = renderCard({
    ...current, channelCount: 3,
    channels: [...current.channels, { name: 'demo-idle-disabled', disabled: true, attempts: 0 }],
  })
  assert.match(html, /1 \/ 3/)
  assert.doesNotMatch(html, /demo-idle-disabled/)
})
