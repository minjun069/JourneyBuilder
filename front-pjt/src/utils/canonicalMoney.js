function numberValue(value) {
  const number = Number(String(value ?? '').replace(/[^0-9.-]/g, ''))
  return Number.isNaN(number) ? null : number
}

const UNIT_MULTIPLIER = { won: 1, man: 10000, eok: 100000000 }

// 저장·조회·AI 계약이 모두 원(KRW) 정수다(ai-pjt/core/field_registry.py).
// 화면 입력도 같은 단위로 읽는다 — 숫자만 넣으면 그대로 원이다.
// 만/억 접미사는 계속 인정한다: '150만' → 1,500,000, '4억8000만' → 480,000,000.
export function toCanonicalMoney(value, defaultUnit = 'won') {
  const defaultMultiplier = UNIT_MULTIPLIER[defaultUnit] ?? 1

  if (value === '' || value == null) return null

  // Number values already crossed the Frontend adapter boundary and are KRW.
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
