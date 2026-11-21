const time = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})
const integer = new Intl.NumberFormat('zh-CN')
const decimal = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 })
const readErrors = {
  missing: '快照文件不存在', invalid: '快照格式有误', unreadable: '无法读取快照',
}
const entities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }
const escape = (value) => String(value).replace(/[&<>"]/g, (char) => entities[char])

function formatRate(rate) {
  if (rate === undefined) return '—'
  if (rate > 0 && rate < 0.1) return '&lt;0.1%'
  return `${decimal.format(rate)}%`
}

function mainCost(money, cost) {
  if (cost === undefined) return '未知'
  const [whole, fraction] = money.formatToParts(cost)
    .filter(({ type }) => type !== 'currency' && type !== 'literal')
    .map(({ value }) => value).join('').split('.')
  return `<small>${money.resolvedOptions().currency}</small> ${whole}${fraction ? `<small>.${fraction}</small>` : ''}`
}

function channelSection({ channels, anomalies, failureRatePct, failingChannels }) {
  if (!failingChannels.length) {
    const message = failureRatePct === 0 ? '<p class="normal muted">暂无失败渠道</p>' : '<p class="muted">暂无渠道样本</p>'
    return `<section class="channels sep">${message}</section>`
  }
  const flagged = new Set(anomalies.map(({ name }) => name))
  const disabled = new Set(channels.filter((channel) => channel.disabled).map(({ name }) => name))
  const items = failingChannels.map(({ name, failureRatePct }) => {
    const detail = `${formatRate(failureRatePct)}${disabled.has(name) ? ' · 已停用' : ''}`
    const style = flagged.has(name) ? ' class="abnormal"' : ''
    return `<li${style}><span class="name" title="${escape(name)}">${escape(name)}</span><span class="rate num">${detail}</span></li>`
  })
  return `<section class="channels sep"><h2>渠道失败率</h2><ul>${items.join('')}<li class="more muted num" hidden></li></ul></section>`
}

function modelSection(models) {
  if (!models.length) return ''
  const top = models[0].requests || 1
  const rows = models.map(({ name, requests }) =>
    `<li><span class="name">${escape(name)}</span>
      <span class="bar" aria-hidden="true"><i style="width:${requests / top * 100}%"></i></span>
      <span class="n num">${integer.format(requests)}</span></li>`)
  return `<section class="models sep"><h2>常用模型</h2><ol class="rows">${rows.join('')}</ol></section>`
}

function keySection(keys, money, privacy) {
  if (!keys.length) return ''
  const rows = keys.map(({ name, cost }) => {
    const label = privacy ? '<span aria-hidden="true">＊＊＊</span><span class="sr-only">已隐藏</span>' : escape(name)
    return `<li><span class="name">${label}</span><span class="n num">${cost === undefined ? '未知' : money.format(cost)}</span></li>`
  })
  return `<section class="keys sep"><h2>用量前三 Key</h2><ol class="rows">${rows.join('')}</ol></section>`
}

export function renderCard(data, privacy) {
  const { status, asOf, day, today, stale, error } = data
  const stamp = asOf ? `<time class="muted num" datetime="${escape(asOf)}">${today ? '今日' : escape(day)} · ${time.format(new Date(asOf))}</time>` : ''
  const heading = `<header><h1>AI4S</h1>${stamp}</header>`
  if (status === 'empty') {
    return heading + '<div class="state"><strong>还没有读到统计</strong></div>'
  }
  if (status === 'error' && !asOf) {
    return heading + `<div class="state"><strong class="error">读不到统计</strong><span class="muted">${readErrors[error]}</span></div>`
  }

  const failed = status === 'error' ? `<span class="error">读取失败（${readErrors[error]}），显示上次数据</span>` : ''
  const delayed = stale ? '<span class="muted">数据未更新</span>' : ''
  const notice = failed || delayed ? `<div class="notice">${failed}${delayed}</div>` : ''
  const money = new Intl.NumberFormat('zh-CN', { style: 'currency', currency: data.currency, currencyDisplay: 'code' })
  return `${heading}${notice}<section class="metrics">
    <div class="usage"><span class="muted">调用</span><span class="value num">${integer.format(data.requests)}</span></div>
    <div class="usage"><span class="muted">费用</span><span class="value num">${mainCost(money, data.cost)}</span></div>
    <div class="health"><span class="muted">渠道可用 / 总数</span><span class="value num">${integer.format(data.availableChannelCount)} / ${integer.format(data.channelCount)}</span></div>
    <div class="health"><span class="muted">近期失败率</span><span class="value num">${formatRate(data.failureRatePct)}</span></div>
  </section>${channelSection(data)}${modelSection(data.models)}${keySection(data.keys, money, privacy)}`
}
