// 시뮬레이션 결과를 "숫자" 가 아니라 "문장" 으로 바꾼다.
// 헤드라인 문구는 성공확률·경로 수·고갈 나이에서만 만들고 어떤 값도 하드코딩하지 않는다.
// 성공확률 판정 구간. 판정 색과 칩 문구가 여기서만 갈린다.
const SAFE_THRESHOLD = 0.8
const CAUTION_THRESHOLD = 0.5

export const TONES = {
  danger: 'danger',
  caution: 'caution',
  safe: 'safe',
  neutral: 'neutral',
}

export function probabilityText(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '계산 전'
  if (value > 0 && value < 0.001) return '<0.1%'
  return `${(value * 100).toFixed(1)}%`
}

export function probabilityDeltaText(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(1)}%p`
}

export function verdictTone(probability) {
  if (typeof probability !== 'number' || !Number.isFinite(probability)) return TONES.neutral
  if (probability >= SAFE_THRESHOLD) return TONES.safe
  if (probability >= CAUTION_THRESHOLD) return TONES.caution
  return TONES.danger
}

const VERDICT_LABELS = {
  [TONES.safe]: '계획대로 가능',
  [TONES.caution]: '보완 필요',
  [TONES.danger]: '자금 부족 위험',
  [TONES.neutral]: '계산 전',
}

function count(value) {
  return value.toLocaleString('ko-KR')
}

function manwon(amount) {
  const man = Math.round(Math.abs(amount) / 10_000)
  return `${man.toLocaleString('ko-KR')}만원`
}

/**
 * 기준 결과 헤드라인.
 * paths 는 analysis.metadata.num_paths, 실패 횟수는 성공확률에서 되돌려 계산한다.
 */
export function baselineHeadline({ probability, paths, horizonAge, depletionAge }) {
  const tone = verdictTone(probability)
  const known = typeof probability === 'number' && Number.isFinite(probability)
  const hasPaths = Number.isFinite(paths) && paths > 0
  const successCount = known && hasPaths ? Math.round(probability * paths) : null
  const failureCount = successCount === null ? null : paths - successCount
  const ageText = Number.isFinite(horizonAge) ? `${horizonAge}세` : '계획 기간 끝'

  let support = ''
  if (successCount !== null) {
    support = tone === TONES.safe
      ? `${count(paths)}번 중 ${count(successCount)}번 성공`
      : `${count(paths)}번 중 ${count(successCount)}번만 성공`
  }

  let title
  if (successCount === null) {
    title = '아직 계산된 결과가 없습니다.'
  } else if (tone === TONES.safe) {
    // 계획이 성공하는 쪽이면 실패 횟수가 아니라 성공 횟수를 센다.
    title = failureCount === 0
      ? `${count(paths)}번 모두 ${ageText}까지 돈이 남았습니다.`
      : `${count(paths)}번 중 ${count(successCount)}번은 ${ageText}까지 돈이 남았습니다.`
  } else {
    title = `${count(paths)}번 중 ${count(failureCount)}번은 ${ageText} 전에 돈이 바닥났습니다.`
  }

  const detail = Number.isFinite(depletionAge)
    ? `절반의 경우 ${Math.round(depletionAge)}세 무렵에 금융자산이 0원이 됩니다. 아래에서 저축액·은퇴 나이·생활비를 바꿔 가며 확률이 어떻게 움직이는지 볼 수 있습니다.`
    : `절반의 경우 ${ageText}까지 금융자산이 0원 아래로 내려가지 않습니다. 아래에서 저축액·은퇴 나이·생활비를 바꿔 가며 확률이 어떻게 움직이는지 볼 수 있습니다.`

  return {
    tone,
    chip: VERDICT_LABELS[tone],
    value: probabilityText(probability),
    support,
    title,
    detail,
    depletionMark: Number.isFinite(depletionAge) ? `${Math.round(depletionAge)}세 고갈` : '',
  }
}

// 바꾼 값과 지금 계획의 차이를 한국어 어간으로 만든다. ("저축을 월 77만원 늘리")
// 연결어미(-고 / -면)는 joinChanges 에서 마지막 구절만 "-면" 으로 맺는다.
export function changePhrases({ baselineProfile, changedProfile }) {
  if (!baselineProfile || !changedProfile) return []
  const phrases = []
  const savingsDelta = (changedProfile.monthly_savings ?? 0) - (baselineProfile.monthly_savings ?? 0)
  if (savingsDelta !== 0) {
    phrases.push(`저축을 월 ${manwon(savingsDelta)} ${savingsDelta > 0 ? '늘리' : '줄이'}`)
  }
  const ageDelta = (changedProfile.retirement_age ?? 0) - (baselineProfile.retirement_age ?? 0)
  if (ageDelta !== 0) {
    phrases.push(`${Math.abs(ageDelta)}년 ${ageDelta > 0 ? '늦게' : '일찍'} 은퇴하`)
  }
  const baseExpenses = JSON.stringify(baselineProfile.retirement_expenses ?? [])
  const nextExpenses = JSON.stringify(changedProfile.retirement_expenses ?? [])
  if (baseExpenses !== nextExpenses) phrases.push('생활비를 조정하')
  return phrases
}

export function joinChanges(phrases) {
  if (phrases.length === 0) return ''
  return phrases.map((stem, index) => `${stem}${index === phrases.length - 1 ? '면' : '고'}`).join(' ')
}

/** 비교 결과 헤드라인. 주인공은 바꾼 조건이고 지금 계획은 비교 대상이다. */
export function comparisonHeadline({
  baselineProbability,
  comparisonProbability,
  deltaPp,
  baselineProfile,
  changedProfile,
}) {
  const improved = Number.isFinite(deltaPp) && deltaPp > 0.05
  const worsened = Number.isFinite(deltaPp) && deltaPp < -0.05
  // 판정 색은 기준 화면과 같은 뜻이어야 한다 — "바꾼 조건이 얼마나 안전한가".
  // 좋아졌는지 나빠졌는지는 옆의 %p 값 색으로 따로 말한다.
  const tone = verdictTone(comparisonProbability)
  const deltaTone = improved ? 'up' : worsened ? 'down' : 'flat'

  const phrases = changePhrases({ baselineProfile, changedProfile })
  const ratio = (
    Number.isFinite(baselineProbability) && baselineProbability > 0 && Number.isFinite(comparisonProbability)
      ? comparisonProbability / baselineProbability
      : null
  )

  let outcome
  if (!Number.isFinite(deltaPp)) {
    outcome = '성공 확률 변화를 계산하지 못했습니다'
  } else if (improved && ratio !== null && ratio >= 2) {
    outcome = `성공이 ${Math.floor(ratio)}배 넘게 늘어납니다`
  } else if (improved) {
    outcome = `성공 확률이 ${probabilityDeltaText(deltaPp)} 올라갑니다`
  } else if (worsened) {
    outcome = `성공 확률이 ${Math.abs(deltaPp).toFixed(1)}%p 떨어집니다`
  } else {
    outcome = '성공 확률은 거의 그대로입니다'
  }

  const title = phrases.length
    ? `${joinChanges(phrases)}, ${outcome}.`
    : `조건을 바꾸면 ${outcome}.`

  let detail
  if (!Number.isFinite(comparisonProbability)) {
    detail = '바꾼 조건의 성공 확률을 계산하지 못했습니다.'
  } else if (comparisonProbability >= SAFE_THRESHOLD) {
    detail = '이 조건이면 계획 기간 내내 자금이 버틸 가능성이 큽니다. 아래 값을 더 바꿔 가며 확인해 보세요.'
  } else if (comparisonProbability >= CAUTION_THRESHOLD) {
    detail = '그래도 절반을 조금 넘는 결과입니다. 생활비를 함께 줄이거나 은퇴를 더 늦추면 확률이 더 올라갑니다.'
  } else {
    detail = '아직 절반에 못 미치는 결과입니다. 생활비를 함께 줄이거나 은퇴를 더 늦추면 확률이 더 올라갑니다.'
  }

  return {
    tone,
    deltaTone,
    chip: '바꾼 조건 결과',
    value: probabilityText(comparisonProbability),
    deltaText: probabilityDeltaText(deltaPp),
    support: `지금 계획 ${probabilityText(baselineProbability)} → 바꾼 조건 ${probabilityText(comparisonProbability)}`,
    title,
    detail,
  }
}

/** 각 조절 칸 아래에 붙는 "현재 계획과 얼마나 다른지" 안내. */
export function leverDiff({ current, next, kind }) {
  if (!Number.isFinite(current)) return { text: '', changed: false }
  if (!Number.isFinite(next) || next === current) {
    return {
      text: kind === 'age' ? `현재 ${current}세` : `현재 상담 기준 ${manwon(current)}`,
      changed: false,
    }
  }
  const diff = next - current
  if (kind === 'age') {
    return { text: `${current}세 → ${Math.abs(diff)}년 ${diff > 0 ? '늦춤' : '당김'}`, changed: true }
  }
  return {
    text: `현재 ${manwon(current)} → ${manwon(diff)} ${diff > 0 ? '늘림' : '줄임'}`,
    changed: true,
  }
}

/** 생활비 구간 요약 버튼에 들어갈 두 줄. */
export function expenseSummary(rows) {
  const valid = (Array.isArray(rows) ? rows : []).filter((row) => row && Number.isFinite(row.monthly_amount))
  if (valid.length === 0) return { amounts: '구간 없음', ages: '기간을 추가해 주세요', label: '구간 추가' }
  return {
    amounts: valid.map((row) => manwon(row.monthly_amount)).join(' · '),
    ages: valid.map((row) => `${row.from_age}~${row.to_age}세`).join(' · '),
    label: `${valid.length}개 구간 수정`,
  }
}

/** 화면 맨 아래 가정 한 줄. metadata.assumptions 에서만 만든다. */
export function assumptionLine({ assumptions, paths, noticeCount }) {
  const parts = []
  const rate = assumptions?.annual_return_mean
  const inflation = assumptions?.annual_inflation_rate
  if (Number.isFinite(rate) && Number.isFinite(inflation)) {
    parts.push(`연 수익률 ${(rate * 100).toFixed(1).replace(/\.0$/, '')}%·물가 ${(inflation * 100).toFixed(1).replace(/\.0$/, '')}% 가정`)
  }
  if (Number.isFinite(paths) && paths > 0) parts.push(`${count(paths)}개 경로`)
  parts.push('부동산과 부채는 반영하지 않습니다')
  if (Number.isFinite(noticeCount) && noticeCount > 0) parts.push(`계산 범위 안내 ${noticeCount}건`)
  return parts.join(' · ')
}
