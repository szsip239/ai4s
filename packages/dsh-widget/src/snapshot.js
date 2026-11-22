import { readFile } from 'node:fs/promises'

const statDay = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
})
const maxAge = 2 * 600_000
const timestamp = /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/i
const object = (value) => typeof value === 'object' && value !== null && !Array.isArray(value)
const name = (value) => typeof value === 'string' && value.trim().length > 0
const count = (value) => Number.isSafeInteger(value) && value >= 0
const periodNames = ['day', 'week', 'month']
// Host reserves field names containing "token" in persisted data.
const periodFields = {
  requests: 'requests', attempts: 'attempts', failed: 'failed',
  inputTokens: 'input', outputTokens: 'output', cachedTokens: 'cached',
}

function date(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const millis = Date.parse(value)
  return Number.isFinite(millis) && new Date(millis).toISOString().slice(0, 10) === value
}

function decode(text) {
  const data = JSON.parse(text)
  if (!object(data)) return
  const { asOf, day, periods, channels } = data
  if (typeof asOf !== 'string' ||
      !timestamp.test(asOf) ||
      !date(asOf.slice(0, 10)) || !Number.isFinite(Date.parse(asOf)) ||
      !date(day) || !object(periods) || !Array.isArray(channels)) return
  if (!periodNames.every((key) => object(periods[key]) &&
      Object.keys(periodFields).every((field) => count(periods[key][field])))) return
  if (!channels.every((row) => object(row) && name(row.name) && typeof row.disabled === 'boolean' &&
      count(row.attempts) && (row.successRatePct === null ||
        (Number.isFinite(row.successRatePct) && row.successRatePct >= 0 && row.successRatePct <= 100)))) return

  return {
    asOf, day,
    periods: Object.fromEntries(periodNames.map((key) => [
      key, Object.fromEntries(Object.entries(periodFields).map(([field, target]) => [target, periods[key][field]])),
    ])),
    channels: channels.map(({ name, disabled, attempts, successRatePct }) => ({
      name, disabled, attempts, ...(successRatePct === null ? {} : { successRatePct }),
    })),
  }
}

function annotate({ asOf, day, periods, channels }, { minAttempts, minSuccessRatePct }) {
  const now = Date.now()
  const anomalies = channels
    .filter(({ disabled, attempts, successRatePct }) =>
      !disabled && attempts >= minAttempts && successRatePct < minSuccessRatePct)
    .map(({ name, successRatePct }) => ({ name, reason: `成功率 ${Math.round(successRatePct * 10) / 10}%` }))
  return {
    asOf, day, periods, channels, anomalies,
    today: day === statDay.format(now),
    stale: now - Date.parse(asOf) >= maxAge,
  }
}

function readError(previous, options, error) {
  const last = previous.periods ? annotate(previous, options) : {}
  return { ...last, status: 'error', error }
}

export async function readSnapshot(path, previous, options) {
  let text
  try {
    text = await readFile(path, { encoding: 'utf8', signal: options.signal })
  } catch (cause) {
    if (typeof cause.code !== 'string' || cause.code === 'ABORT_ERR') throw cause
    return readError(previous, options, cause.code === 'ENOENT' ? 'missing' : 'unreadable')
  }
  let data
  try {
    data = decode(text)
  } catch (cause) {
    if (!(cause instanceof SyntaxError)) throw cause
  }
  return data ? { ...annotate(data, options), status: 'ok' } : readError(previous, options, 'invalid')
}
