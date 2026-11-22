import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderCard } from '../widget/assets/view.js'

const current = {
  asOf: '2026-11-20T12:00:00+08:00', day: '2026-11-20', status: 'ok', today: true, stale: false,
  periods: {
    day: { requests: 32, attempts: 40, failed: 2, input: 4800, output: 612, cached: 2900 },
    week: { requests: 1262, attempts: 1376, failed: 18, input: 4800000, output: 612400, cached: 2900000 },
    month: { requests: 18400, attempts: 22998, failed: 1300, input: 1200000000, output: 2000000, cached: 49800000 },
  },
  channels: [
    { name: 'demo-low<&', disabled: false, attempts: 20, successRatePct: 85 },
    { name: 'demo-off', disabled: true, attempts: 10, successRatePct: 50 },
    { name: 'demo-off-idle', disabled: true, attempts: 0 },
    { name: 'demo-idle', disabled: false, attempts: 0 },
  ],
  anomalies: [{ name: 'demo-low<&', reason: '成功率 85%' }],
}

test('the card displays successful calls, tokens and failed attempts across the three periods', () => {
  const html = renderCard(current)
  assert.match(html, /今日.*失败 5%/s)
  assert.match(html, /本周.*失败 1\.3%/s)
  assert.match(html, /本月.*失败 5\.7%/s)
  assert.match(html, /title="32"[^>]*>32</)
  assert.match(html, /title="1,262"[^>]*>1\.3K</)
  assert.match(html, /title="1,200,000,000">1\.2B</)
  assert.match(html, /title="4,800,000">4\.8M</)
  assert.match(html, /title="612">612</)
  assert.match(html, /失败率按全部尝试计算；调用只计成功请求，与 AI4S 后台一致/)
  assert.doesNotMatch(html, /费用|常用模型|Key/)
})

test('no snapshot, read failure with saved data and a previous date have distinct states', () => {
  assert.match(renderCard({ status: 'empty' }), /还没有读到统计/)
  assert.match(renderCard({ status: 'error', error: 'missing' }), /读不到统计.*快照文件不存在/s)
  assert.match(renderCard({ ...current, status: 'error', error: 'invalid' }), /读取失败，显示上次 · 12:00/)
  const stale = renderCard({ ...current, today: false, stale: false })
  assert.match(stale, /未更新 · 11-20 12:00/)
  assert.match(stale, /11-20调用/)
})

test('the card distinguishes zero attempts, zero failures and a small nonzero percentage', () => {
  const render = (attempts, failed) => renderCard({
    ...current, channels: [], anomalies: [],
    periods: { ...current.periods, day: { ...current.periods.day, requests: 0, attempts, failed } },
  })
  assert.match(render(0, 0), /今日还没有渠道调用.*失败 —/s)
  assert.match(render(1000, 0), /失败 0%/)
  assert.match(render(2000, 1), /失败 &lt;0\.1%/)
})

test('active channels show attempts and low-rate details, while zero-use channels are grouped', () => {
  const html = renderCard(current)
  assert.match(html, /demo-low&lt;&amp;.*title="20".*成功率 85%/s)
  assert.match(html, /demo-off.*停用.*title="10"/s)
  assert.match(html, /1 空闲 · 1 停用/)
  assert.match(html, /空闲：demo-idle/)
  assert.match(html, /停用：demo-off-idle/)
  assert.doesNotMatch(html, /成功率 50%/)
})
