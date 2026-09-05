function numberValue(value) {
  const number = Number(String(value ?? '').replace(/[^0-9.-]/g, ''))
  return Number.isNaN(number) ? null : number
}

export function toCanonicalMoney(value, defaultUnit = 'man') {
  const defaultMultiplier = defaultUnit === 'eok' ? 100000000 : 10000

  if (value === '' || value == null) return null

  // Number values already crossed the Frontend adapter boundary and are KRW.
  // Only raw user-entered strings may use the screen's implicit 만/억 unit.
  if (typeof value === 'number') {
    return Number.isFinite(value) ? Math.round(value) : null
  }

  const raw = String(value).replace(/,/g, '').replace(/\s+/g, '')
  if (!raw) return null

  const explicitWon = raw.endsWith('원') && !/[억만]/.test(raw)
  if (explicitWon) {
    const number = numberValue(raw.slice(0, -1))
    return number == null ? null : Math.round(number)
  }

  if (!/[억만]/.test(raw)) {
    const number = numberValue(raw)
    return number == null || number === 0
      ? number
      : Math.round(number * defaultMultiplier)
  }

  let total = 0
  let matched = false
  const unitPattern = /(-?\d+(?:\.\d+)?)(억|만)/g
  for (const match of raw.matchAll(unitPattern)) {
    matched = true
    const amount = Number(match[1])
    if (Number.isNaN(amount)) continue
    total += amount * (match[2] === '억' ? 100000000 : 10000)
  }

  if (!matched) return numberValue(raw)
  const remainder = raw.replace(unitPattern, '').replace(/원$/, '')
  const extra = Number(remainder)
  if (remainder && !Number.isNaN(extra)) {
    total += raw.includes('억') && !raw.includes('만') && extra > 0 && extra < 10000
      ? extra * 10000
      : extra
  }
  return Math.round(total)
}
