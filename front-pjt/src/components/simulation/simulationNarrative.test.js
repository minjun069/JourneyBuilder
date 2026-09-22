import assert from 'node:assert/strict'
import test from 'node:test'

import {
  assumptionLine,
  baselineHeadline,
  comparisonHeadline,
  expenseSummary,
  leverDiff,
  probabilityDeltaText,
  probabilityText,
  verdictTone,
} from './simulationNarrative.js'

test('실패 횟수는 성공확률과 경로 수에서만 나온다', () => {
  const headline = baselineHeadline({ probability: 0.142, paths: 1000, horizonAge: 90, depletionAge: 80.4 })
  assert.equal(headline.value, '14.2%')
  assert.equal(headline.support, '1,000번 중 142번만 성공')
  assert.equal(headline.title, '1,000번 중 858번은 90세 전에 돈이 바닥났습니다.')
  assert.match(headline.detail, /80세 무렵에 금융자산이 0원/)
  assert.equal(headline.tone, 'danger')
})

test('계획이 성공하는 쪽이면 실패가 아니라 성공을 센다', () => {
  const headline = baselineHeadline({ probability: 0.93, paths: 500, horizonAge: 95, depletionAge: null })
  assert.equal(headline.tone, 'safe')
  assert.equal(headline.chip, '계획대로 가능')
  assert.equal(headline.title, '500번 중 465번은 95세까지 돈이 남았습니다.')
  assert.match(headline.detail, /0원 아래로 내려가지 않습니다/)
})

test('모든 경로가 성공하면 "모두" 로 말한다', () => {
  const headline = baselineHeadline({ probability: 1, paths: 1000, horizonAge: 90, depletionAge: null })
  assert.equal(headline.title, '1,000번 모두 90세까지 돈이 남았습니다.')
})

test('경로 수를 모르면 숫자를 지어내지 않는다', () => {
  const headline = baselineHeadline({ probability: 0.4, paths: null, horizonAge: 90, depletionAge: null })
  assert.equal(headline.support, '')
  assert.equal(headline.title, '아직 계산된 결과가 없습니다.')
})

test('비교 헤드라인은 무엇을 바꿨는지와 몇 배 좋아졌는지를 문장으로 만든다', () => {
  const headline = comparisonHeadline({
    baselineProbability: 0.142,
    comparisonProbability: 0.476,
    deltaPp: 33.4,
    baselineProfile: { monthly_savings: 1_230_000, retirement_age: 60, retirement_expenses: [] },
    changedProfile: { monthly_savings: 2_000_000, retirement_age: 65, retirement_expenses: [] },
  })
  // 판정 색은 바꾼 조건 자체의 안전도(47.6% 는 아직 danger), 개선 여부는 %p 색으로 따로 말한다.
  assert.equal(headline.tone, 'danger')
  assert.equal(headline.deltaTone, 'up')
  assert.equal(headline.value, '47.6%')
  assert.equal(headline.deltaText, '+33.4%p')
  assert.equal(headline.support, '지금 계획 14.2% → 바꾼 조건 47.6%')
  assert.equal(headline.title, '저축을 월 77만원 늘리고 5년 늦게 은퇴하면, 성공이 3배 넘게 늘어납니다.')
})

test('비교 결과가 나빠지면 떨어진 폭을 말한다', () => {
  const headline = comparisonHeadline({
    baselineProbability: 0.5,
    comparisonProbability: 0.3,
    deltaPp: -20,
    baselineProfile: { monthly_savings: 1_000_000, retirement_age: 60, retirement_expenses: [] },
    changedProfile: { monthly_savings: 1_000_000, retirement_age: 55, retirement_expenses: [] },
  })
  assert.equal(headline.tone, 'danger')
  assert.equal(headline.deltaTone, 'down')
  assert.equal(headline.title, '5년 일찍 은퇴하면, 성공 확률이 20.0%p 떨어집니다.')
})

test('바꾼 값이 없으면 중립 문장을 쓴다', () => {
  const profile = { monthly_savings: 1_000_000, retirement_age: 60, retirement_expenses: [] }
  const headline = comparisonHeadline({
    baselineProbability: 0.5,
    comparisonProbability: 0.5,
    deltaPp: 0,
    baselineProfile: profile,
    changedProfile: { ...profile },
  })
  assert.equal(headline.title, '조건을 바꾸면 성공 확률은 거의 그대로입니다.')
  assert.equal(headline.deltaTone, 'flat')
})

test('세 가지를 한꺼번에 바꿔도 연결어미가 한 번만 "-면" 으로 맺힌다', () => {
  const headline = comparisonHeadline({
    baselineProbability: 1,
    comparisonProbability: 0.879,
    deltaPp: -12.1,
    baselineProfile: {
      monthly_savings: 908_152,
      retirement_age: 65,
      retirement_expenses: [{ from_age: 65, to_age: 90, monthly_amount: 1_800_000 }],
    },
    changedProfile: {
      monthly_savings: 200_000,
      retirement_age: 55,
      retirement_expenses: [{ from_age: 55, to_age: 90, monthly_amount: 1_800_000 }],
    },
  })
  assert.equal(
    headline.title,
    '저축을 월 71만원 줄이고 10년 일찍 은퇴하고 생활비를 조정하면, 성공 확률이 12.1%p 떨어집니다.',
  )
  // 87.9% 는 여전히 안전한 축이므로 판정은 safe, 하락은 %p 색으로만 말한다.
  assert.equal(headline.tone, 'safe')
  assert.equal(headline.deltaTone, 'down')
})

test('조절 칸 아래에는 지금 계획과의 차이가 붙는다', () => {
  assert.deepEqual(leverDiff({ current: 1_230_000, next: 1_230_000, kind: 'amount' }), {
    text: '현재 상담 기준 123만원',
    changed: false,
  })
  assert.deepEqual(leverDiff({ current: 1_230_000, next: 2_000_000, kind: 'amount' }), {
    text: '현재 123만원 → 77만원 늘림',
    changed: true,
  })
  assert.deepEqual(leverDiff({ current: 60, next: 65, kind: 'age' }), { text: '60세 → 5년 늦춤', changed: true })
  assert.deepEqual(leverDiff({ current: 60, next: null, kind: 'age' }), { text: '현재 60세', changed: false })
})

test('생활비 요약은 금액과 구간을 각각 한 줄로 만든다', () => {
  const summary = expenseSummary([
    { from_age: 60, to_age: 70, monthly_amount: 3_800_000 },
    { from_age: 71, to_age: 80, monthly_amount: 3_200_000 },
  ])
  assert.equal(summary.amounts, '380만원 · 320만원')
  assert.equal(summary.ages, '60~70세 · 71~80세')
  assert.equal(summary.label, '2개 구간 수정')
  assert.equal(expenseSummary([]).amounts, '구간 없음')
})

test('가정 한 줄은 metadata.assumptions 에서만 만든다', () => {
  assert.equal(
    assumptionLine({
      assumptions: { annual_return_mean: 0.04, annual_inflation_rate: 0.02 },
      paths: 1000,
      noticeCount: 3,
    }),
    '연 수익률 4%·물가 2% 가정 · 1,000개 경로 · 부동산과 부채는 반영하지 않습니다 · 계산 범위 안내 3건',
  )
  assert.equal(
    assumptionLine({ assumptions: null, paths: null, noticeCount: 0 }),
    '부동산과 부채는 반영하지 않습니다',
  )
})

test('확률 표기는 기존 규칙을 그대로 지킨다', () => {
  assert.equal(probabilityText(undefined), '계산 전')
  assert.equal(probabilityText(0.0005), '<0.1%')
  assert.equal(probabilityText(0), '0.0%')
  assert.equal(probabilityDeltaText(12.3), '+12.3%p')
  assert.equal(probabilityDeltaText(null), '—')
  assert.equal(verdictTone(0.8), 'safe')
  assert.equal(verdictTone(0.5), 'caution')
  assert.equal(verdictTone(0.49), 'danger')
})
