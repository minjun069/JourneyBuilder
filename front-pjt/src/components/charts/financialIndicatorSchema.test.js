import assert from 'node:assert/strict'
import test from 'node:test'

import { INDICATOR_SCHEMA, makeIndicator, toIndicatorGroups } from './financialIndicatorSchema.js'

// 백엔드 calculate_financial_indicators 가 실제로 내려주는 모양(평면 키 + 부가 정보)
function liveIndicators(overrides = {}) {
  return {
    assumptions: { inflation_rate: 0.02 },
    _details: { total_savings: 1_900_000 },
    household_balance: 81.1,
    total_savings_propensity: 22.5,
    financial_asset_ratio: 8.4,
    debt_service_ratio: 15.4,
    retirement_savings: 36.8,
    emergency_reserve: 4.55,
    insurance_preparation: 4.1,
    ...overrides,
  }
}

test('평면 지표 응답을 현금흐름·자산·리스크 그룹으로 옮긴다', () => {
  const { groups } = toIndicatorGroups(liveIndicators())

  assert.deepEqual(groups.cashflow, [
    { key: '가계수지지표', value: 18.9 },      // 100 - 81.1 (지출률 → 저축률)
    { key: '총저축성향지표', value: 22.5 },
  ])
  assert.deepEqual(groups.assets, [
    { key: '금융자산비중지표', value: 8.4 },
    { key: '총부채상환지표', value: 15.4 },
    { key: '노후대비저축지표', value: 36.8 },
  ])
  assert.deepEqual(groups.risk, [
    { key: '비상예비금지표', value: 4.55 },
    { key: '보장성보험준비지표', value: 4.1 },
  ])
})

test('옮긴 키는 모두 게이지 스키마가 아는 7종이다', () => {
  const { groups } = toIndicatorGroups(liveIndicators())
  const keys = Object.values(groups).flat().map((item) => item.key)

  assert.equal(keys.length, 7)
  for (const key of keys) assert.ok(INDICATOR_SCHEMA[key], `스키마에 없는 키: ${key}`)
  assert.doesNotThrow(() => keys.forEach((key) => makeIndicator(key, 0, 35)))
})

test('계산 불가(null)로 내려온 지표는 게이지를 만들지 않고 건너뛴다', () => {
  const { groups } = toIndicatorGroups(liveIndicators({
    total_savings_propensity: null,
    emergency_reserve: null,
  }))

  assert.deepEqual(groups.cashflow.map((item) => item.key), ['가계수지지표'])
  assert.deepEqual(groups.risk.map((item) => item.key), ['보장성보험준비지표'])
})

test('이미 groups 를 가진 스냅샷은 그대로 둔다', () => {
  const seeded = { age: 35, groups: { cashflow: [{ key: '가계수지지표', value: 14 }] } }

  assert.equal(toIndicatorGroups(seeded), seeded)
})

test('쓸 값이 하나도 없으면 groups 를 만들지 않는다', () => {
  const empty = { assumptions: {}, household_balance: null }

  assert.equal(toIndicatorGroups(empty), empty)
  assert.equal(toIndicatorGroups(null), null)
})
