/**
 * 금액을 한국식 단위로 포맷
 * - 1억 이상: X억 Y만원 (Y가 0이면 X억원)
 * - 1억 미만: X만원
 * - 만원 미만 절사
 */
export function fmtKRW(n) {
  const val = n ?? 0
  const abs = Math.abs(val)
  const sign = val < 0 ? '-' : ''

  if (abs >= 100_000_000) {
    const eok = Math.floor(abs / 100_000_000)
    const man = Math.floor((abs % 100_000_000) / 10_000)
    if (man === 0) return sign + eok + '억원'
    return sign + eok + '억 ' + man.toLocaleString('ko-KR') + '만원'
  }

  const man = Math.floor(abs / 10_000)
  return sign + man.toLocaleString('ko-KR') + '만원'
}
