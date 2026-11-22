const clock = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})
const exact = new Intl.NumberFormat('zh-CN')
const readErrors = {
  missing: '快照文件不存在', invalid: '快照格式有误', unreadable: '无法读取快照',
}
const entities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }
const escape = (value) => String(value).replace(/[&<>"]/g, (char) => entities[char])
const periods = [['day', '今日'], ['week', '本周'], ['month', '本月']]
const tokens = [['input', '入'], ['output', '出'], ['cached', '缓']]
const rateNote = '失败率按全部尝试计算；调用只计成功请求，与 AI4S 后台一致'

function formatNumber(count) {
  for (const [divisor, suffix] of [[1e9, 'B'], [1e6, 'M'], [1e3, 'K']]) {
    if (count >= divisor) return (count / divisor).toFixed(1).replace(/\.0$/, '') + suffix
  }
  return String(count)
}

function formatRate(rate) {
  if (rate === null) return '—'
  if (rate > 0 && rate < 0.1) return '&lt;0.1%'
  return rate.toFixed(1).replace(/\.0$/, '') + '%'
}

function number(count, tag = 'span', attributes = '') {
  return `<${tag} title="${exact.format(count)}"${attributes}>${formatNumber(count)}</${tag}>`
}

function note(id, label, lines, style = '', title = '') {
  return `<span class="ai4s-note ${style}"><button type="button" aria-describedby="${id}"${title}>${label}</button>
    <span id="${id}" class="ai4s-note-pop" role="tooltip">${lines.map((line) => `<span>${line}</span>`).join('')}</span>
  </span>`
}

function channelNote(rest, idle, off, compact, anomalies) {
  const count = rest.length + idle.length + off.length
  if (!count) return ''
  const groups = [
    [rest, `+${rest.length} 在跑`, '在跑', ({ name, attempts }) => {
      const reason = anomalies.get(name)
      return `${escape(name)} ${formatNumber(attempts)}${reason ? `（${escape(reason)}）` : ''}`
    }],
    [idle, `${idle.length} 空闲`, '空闲', ({ name }) => escape(name)],
    [off, `${off.length} 停用`, '停用', ({ name }) => escape(name)],
  ].filter(([channels]) => channels.length)
  const label = compact ? `另 ${count} 个渠道` : groups.map(([, text]) => text).join(' · ')
  const lines = groups.map(([channels, , title, format]) => `${title}：${channels.map(format).join('、')}`)
  return note('ai4s-channel-note', label, lines, 'ai4s-channel-note')
}

function lanes({ channels, anomalies }, day, compact, limit) {
  const active = channels.filter(({ attempts }) => attempts > 0).sort((a, b) => b.attempts - a.attempts)
  const idle = channels.filter(({ attempts, disabled }) => attempts === 0 && !disabled)
  const off = channels.filter(({ attempts, disabled }) => attempts === 0 && disabled)
  const flagged = new Map(anomalies.map(({ name, reason }) => [name, reason]))
  const rows = active.slice(0, limit).map(({ name, disabled, attempts }, index) => {
    const paused = disabled ? '<span class="ai4s-lane-off">停用</span>' : ''
    const reason = flagged.get(name)
    const calls = reason
      ? note(`ai4s-lane-${index}`, formatNumber(attempts), [escape(reason)],
        'ai4s-lane-calls ai4s-lane-danger', ` title="${exact.format(attempts)}"`)
      : number(attempts, 'span', ' class="ai4s-lane-calls"')
    return `<li class="ai4s-lane">
      <div class="ai4s-lane-row"><span class="ai4s-lane-name" title="${escape(name)}">${escape(name)}${paused}</span>${calls}</div>
      <div class="ai4s-bar" aria-hidden="true"><i style="width: ${attempts / active[0].attempts * 100}%"></i></div>
    </li>`
  })
  const body = rows.length
    ? `<div class="ai4s-lanes-head"><span>渠道</span><span>${day}调用</span></div><ul class="ai4s-lane-list">${rows.join('')}</ul>`
    : `<p class="ai4s-lanes-empty">${day}还没有渠道调用</p>`
  return `<section class="ai4s-lanes">${body}${channelNote(active.slice(limit), idle, off, compact, flagged)}</section>`
}

function periodTiles(stats, day, compact) {
  const tiles = (compact ? periods.slice(0, 1) : periods).map(([key, label]) => {
    const { requests, attempts, failed } = stats[key]
    const rate = attempts ? (failed / attempts) * 100 : null
    const badge = note(`ai4s-rate-${key}`, `失败 ${formatRate(rate)}`, [rateNote])
    return `<div class="ai4s-period">
      <div class="ai4s-period-head"><span class="ai4s-period-label">${key === 'day' ? day : label}</span>
        <span class="ai4s-rate"${rate >= 5 ? ' data-high' : ''}>${badge}</span>
      </div>
      ${number(requests, 'span', ' class="ai4s-period-num"')}
      <dl class="ai4s-tokens">${tokens.map(([field, name]) =>
        `<div><dt>${name}</dt>${number(stats[key][field], 'dd')}</div>`).join('')}</dl>
    </div>`
  })
  return `<section class="ai4s-periods"${compact ? ' data-compact' : ''}>${tiles.join('')}</section>`
}

function header({ status, asOf, stale }, past, hasData) {
  const warning = status === 'error' && hasData
  const notice = warning ? '读取失败，显示上次 · ' : (stale || past) && hasData ? '未更新 · ' : ''
  const stamp = asOf ? `<time class="ai4s-stamp" datetime="${escape(asOf)}"${warning ? ' data-tone="danger"' : ''}>${notice}${past ? past + ' ' : ''}${clock.format(new Date(asOf))}</time>` : ''
  return `<header class="ai4s-head">
    <span class="ai4s-brand">
      <svg class="ai4s-logo" viewBox="0 0 64 64" aria-hidden="true">
        <defs><linearGradient id="ai4s-shield" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#ea580c"/><stop offset="1" stop-color="#c2410c"/>
        </linearGradient></defs>
        <path d="M32 4 L55 11.5 V32 C55 46.5 45.5 54.5 32 59.5 C18.5 54.5 9 46.5 9 32 V11.5 Z" fill="url(#ai4s-shield)"/>
        <path d="M32 9.5 L49.5 15.5 V32 C49.5 43.5 42 50.5 32 54.5 C22 50.5 14.5 43.5 14.5 32 V15.5 Z" fill="none" stroke="#fff" stroke-opacity="0.28" stroke-width="1.6"/>
        <text x="32" y="41.5" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-size="25" font-weight="800" letter-spacing="-0.5" fill="#fff">4S</text>
      </svg>
      <span class="ai4s-title">AI4S</span>
    </span>
    ${stamp}
  </header>`
}

export function renderCard(data, { compact = false, laneLimit = Infinity } = {}) {
  const past = data.today === false && data.day ? escape(data.day.slice(5)) : ''
  const hasData = Boolean(data.periods)
  const heading = header(data, past, hasData)
  const day = past || '今日'
  let body
  if (data.status === 'empty') {
    body = '<div class="ai4s-state"><strong>还没有读到统计</strong></div>'
  } else if (!hasData) {
    body = `<div class="ai4s-state" data-tone="danger"><strong>读不到统计</strong><span>${readErrors[data.error ?? 'unreadable']}</span></div>`
  } else {
    body = lanes(data, day, compact, laneLimit) + periodTiles(data.periods, day, compact)
  }
  return heading + `<div class="ai4s-body">${body}</div>`
}
