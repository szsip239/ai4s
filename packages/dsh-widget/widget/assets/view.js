const time = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})
const integer = new Intl.NumberFormat('zh-CN')
const readErrors = {
  missing: '快照文件不存在', invalid: '快照格式有误', unreadable: '无法读取快照',
}
const entities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }
const escape = (value) => String(value).replace(/[&<>"]/g, (char) => entities[char])

export function consoleHref(value) {
  if (!value) return
  try {
    const url = new URL(value)
    if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password) return url.href
  } catch (error) {
    if (!(error instanceof TypeError)) throw error
  }
}

function mainCost(money, cost) {
  if (cost === undefined) return '未知'
  const [whole, fraction] = money.formatToParts(cost)
    .filter(({ type }) => type !== 'currency' && type !== 'literal')
    .map(({ value }) => value).join('').split('.')
  return `<small>${money.resolvedOptions().currency}</small> ${whole}${fraction ? `<small>.${fraction}</small>` : ''}`
}

function channelSection({ channels, anomalies }) {
  const flagged = new Set(anomalies.map(({ name }) => name))
  const measured = channels.filter(({ name, successRatePct }) => !flagged.has(name) && successRatePct !== undefined)
  if (!anomalies.length && !measured.length) {
    return '<section class="channels sep"><p class="muted">暂无渠道样本</p></section>'
  }
  const byName = new Map(channels.map((channel) => [channel.name, channel]))
  const abnormalRows = anomalies.map(({ name, reason }) => {
    const { disabled, successRatePct } = byName.get(name)
    const rate = successRatePct === undefined ? '' : `${successRatePct}%`
    const detail = disabled && rate ? `${rate} · ${reason}` : disabled ? reason : rate
    return `<li class="abnormal"><i class="dot" aria-hidden="true"></i><span class="name">${escape(name)}</span><span class="rate num">${escape(detail)}</span></li>`
  })
  const measuredRows = measured.map(({ name, successRatePct }) =>
    `<li class="measured"><span class="name">${escape(name)}</span><span class="rate num">${successRatePct}%</span></li>`)
  const heading = anomalies.length
    ? `<h2>异常渠道 <b class="num">${anomalies.length}</b></h2>`
    : '<h2 class="normal">渠道都正常</h2>'
  return `<section class="channels sep">${heading}<ul>${abnormalRows.join('')}${measuredRows.join('')}</ul></section>`
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
    <div><span class="muted">调用</span><span class="value num">${integer.format(data.requests)}</span></div>
    <div><span class="muted">费用</span><span class="value num">${mainCost(money, data.cost)}</span></div>
  </section>${channelSection(data)}${modelSection(data.models)}${keySection(data.keys, money, privacy)}`
}
