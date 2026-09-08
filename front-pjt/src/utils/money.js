// 원 단위 금액 → 억/만원 표기 (기존 StatCard·reportData 포맷과 동일 규칙)
export function formatEok(value) {
  if (value === null || value === undefined) return '—'
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (abs >= 100_000_000) return `${sign}${(abs / 100_000_000).toFixed(1)}억원`
  return `${sign}${Math.round(abs / 10_000).toLocaleString('ko-KR')}만원`
}
