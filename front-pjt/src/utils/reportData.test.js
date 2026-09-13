import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildDashboardCashflow,
  normalizePresentationData,
  ReportDataError,
} from './reportData.js'
import { MOCK_PRESENTATION_DATA } from '../mock/aiData.js'

function cashFlow(overrides = {}) {
  return {
    total_income: 4_200_000,
    total_expense: 2_320_000,
    surplus: 1_880_000,
    surplus_ratio: 44.8,
    income_groups: [],
    expense_groups: [],
    ...overrides,
  }
}

test('보고서 숫자 문자열은 유한한 숫자로 정규화한다', () => {
  const reports = normalizePresentationData({
    cash_flow: cashFlow({ total_income: '4200000' }),
  })

  assert.equal(reports.cash_flow.total_income, 4_200_000)
})

test('개발용 전체 보고서 fixture를 데이터 오류 없이 정규화한다', () => {
  const reports = normalizePresentationData(MOCK_PRESENTATION_DATA)

  assert.equal(reports.asset_statement.total_assets, 499_871_099)
  assert.equal(reports.cash_flow.total_income, 4_200_000)
})

test('잘못된 보고서 숫자는 0으로 바꾸지 않고 데이터 오류로 분류한다', () => {
  assert.throws(
    () => normalizePresentationData({ cash_flow: cashFlow({ total_income: '잘못된 값' }) }),
    (error) => {
      assert.ok(error instanceof ReportDataError)
      assert.equal(error.code, 'INVALID_REPORT_DATA')
      assert.equal(error.path, 'cash_flow.total_income')
      return true
    },
  )
})

test('숫자로 강제 변환될 수 있는 boolean도 보고서 숫자로 허용하지 않는다', () => {
  assert.throws(
    () => normalizePresentationData({ cash_flow: cashFlow({ total_income: false }) }),
    (error) => error instanceof ReportDataError && error.path === 'cash_flow.total_income',
  )
})

test('보고서가 없으면 검증된 fallback 현금흐름을 사용한다', () => {
  const result = buildDashboardCashflow(
    null,
    ['2025년 5월', '2025년 6월'],
    [
      { period: '2025년 5월', inflow: '4000000', outflow: 2_000_000 },
      { period: '2025년 6월', inflow: 4_200_000, outflow: 2_320_000 },
    ],
    '2025년 6월',
  )

  assert.equal(result.dataSource, 'fallback')
  assert.deepEqual(result.summary, {
    inflow: 4_200_000,
    outflow: 2_320_000,
    net: 1_880_000,
  })
  assert.equal(result.history[0].inflow, 4_000_000)
})

test('fallback 숫자도 잘못되면 데이터 오류로 분류한다', () => {
  assert.throws(
    () => buildDashboardCashflow(
      null,
      ['2025년 6월'],
      [{ period: '2025년 6월', inflow: '', outflow: 2_320_000 }],
      '2025년 6월',
    ),
    (error) => {
      assert.ok(error instanceof ReportDataError)
      assert.equal(error.code, 'INVALID_REPORT_DATA')
      assert.equal(error.path, 'dashboard.cashflow.fallback.inflow')
      return true
    },
  )
})
