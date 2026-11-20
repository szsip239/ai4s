import { readFile } from 'node:fs/promises'

const statDay = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
})
const maxAge = 2 * 600_000
const timestamp = /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/i
const object = (value) => typeof value === 'object' && value !== null && !Array.isArray(value)
const name = (value) => typeof value === 'string' && value.trim().length > 0
const count = (value) => Number.isSafeInteger(value) && value >= 0
const amount = (value) => value === null || Number.isFinite(value)

function date(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const millis = Date.parse(value)
  return Number.isFinite(millis) && new Date(millis).toISOString().slice(0, 10) === value
}

function decode(text) {
  const data = JSON.parse(text)
  if (!object(data)) return
  const { asOf, day, currency, requests, cost, models, channels, keys } = data
  if (typeof asOf !== 'string' ||
      !timestamp.test(asOf) ||
      !date(asOf.slice(0, 10)) || !Number.isFinite(Date.parse(asOf)) ||
      !date(day) || typeof currency !== 'string' || !/^[A-Z]{3}$/.test(currency) ||
      !count(requests) || !amount(cost) ||
      !Array.isArray(models) || !Array.isArray(channels) || !Array.isArray(keys)) return
  if (!models.every((row) => object(row) && name(row.name) && count(row.requests)) ||
      !channels.every((row) => object(row) && name(row.name) && typeof row.disabled === 'boolean' &&
        count(row.attempts) && (row.successRatePct === null ||
          (Number.isFinite(row.successRatePct) && row.successRatePct >= 0 && row.successRatePct <= 100))) ||
      !keys.every((row) => object(row) && name(row.name) && amount(row.cost))) return

  return {
    asOf, day, currency, requests, ...(cost === null ? {} : { cost }),
    models: models.map(({ name, requests }) => ({ name, requests }))
      .sort((a, b) => b.requests - a.requests).slice(0, 3),
    channels: channels.map(({ name, disabled, attempts, successRatePct }) => ({
      name, disabled, attempts, ...(successRatePct === null ? {} : { successRatePct }),
    })),
    keys: keys.map(({ name, cost }) => ({ name, ...(cost === null ? {} : { cost }) }))
      .sort((a, b) => (b.cost ?? -Infinity) - (a.cost ?? -Infinity)).slice(0, 3),
  }
}

function annotate(data, { minAttempts, minSuccessRatePct }) {
  const now = Date.now()
  const anomalies = data.channels
    .filter(({ disabled, attempts, successRatePct }) =>
      disabled || (attempts >= minAttempts && successRatePct !== undefined && successRatePct < minSuccessRatePct))
    .map(({ name, disabled, successRatePct }) => ({
      name, reason: disabled ? '已停用' : `成功率 ${successRatePct}%`,
    }))
  return {
    ...data, anomalies, today: data.day === statDay.format(now),
    stale: now - Date.parse(data.asOf) >= maxAge,
  }
}

function readError(previous, options, error) {
  const last = previous.asOf ? annotate(previous, options) : {}
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
