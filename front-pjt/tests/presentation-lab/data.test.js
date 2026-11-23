import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { normalizePresentationData, ReportDataError } from '../../src/utils/reportData.js'
import { normalizeSimulationResponse } from '../../src/utils/simulationData.js'

// Deliberately consume freshly calculated results, never copied output fixtures.
const bundle = JSON.parse(readFileSync(new URL('../../.dev/presentation.json', import.meta.url), 'utf8'))
const reportsOf = (scenario) => Object.fromEntries(scenario.reports.map((row) => [row.report_type, row.content]))

test('실제 계산 결과 9종과 시뮬레이션 결과를 모든 가상 입력에서 정규화한다', () => {
  assert.equal(bundle.schema_version, 'presentation-lab-v1')
  for (const scenario of bundle.scenarios) {
    assert.equal(scenario.reports.length, 9)
    const normalized = normalizePresentationData(reportsOf(scenario))
    assert.equal(normalized['retirement-fund'].total_required_fund, reportsOf(scenario)['retirement-fund'].total_required_fund)
    assert.equal(normalized['required-savings'].target_monthly_savings,
      normalized['required-savings'].current_monthly_savings + normalized['required-savings'].additional_required_savings)
    if (scenario.simulation.success) {
      assert.equal(normalizeSimulationResponse(scenario.simulation).analysis.mode, 'baseline')
    } else {
      assert.equal(scenario.id, 'no-assets')
      assert.equal(scenario.simulation.error.code, 'INPUT_NOT_READY')
    }
  }
})

test('새로 연결한 4개 보고서도 빈 값·비수치·boolean을 0으로 숨기지 않는다', () => {
  const fields = [
    ['retirement-fund', 'total_required_fund'],
    ['retirement-readiness', 'total_retirement_assets'],
    ['pension-schedule', 'results', 'final_monthly_est'],
    ['required-savings', 'target_monthly_savings'],
  ]
  for (const path of fields) {
    for (const invalid of [null, '', false, 'invalid', Infinity]) {
      const reports = structuredClone(reportsOf(bundle.scenarios[0]))
      const target = path.slice(0, -1).reduce((value, key) => value[key], reports)
      target[path.at(-1)] = invalid
      assert.throws(() => normalizePresentationData(reports), ReportDataError)
    }
  }
})

test('0원 자산과 음수 현금흐름·순자산을 보존한다', () => {
  const byId = Object.fromEntries(bundle.scenarios.map((scenario) => [scenario.id, normalizePresentationData(reportsOf(scenario))]))
  assert.equal(byId['no-assets'].asset_statement.total_assets, 0)
  assert.ok(byId.deficit.cash_flow.surplus < 0)
  assert.ok(byId['high-debt'].asset_statement.net_worth < 0)
})

test('Backend의 지출률·저축비중 정의와 계산 불가 null을 게이지에 보존한다', () => {
  const reports = reportsOf(bundle.scenarios[0])
  const groups = normalizePresentationData(reports)['financial-indicators'].groups
  assert.equal(groups.cashflow[0].value, reports['financial-indicators'].household_balance)
  assert.equal(groups.cashflow[0].description, '총지출 / 총소득')
  assert.equal(groups.assets[2].description, '노후대비저축 / 총저축')
  const empty = normalizePresentationData(reportsOf(bundle.scenarios.find((scenario) => scenario.id === 'no-assets')))
  assert.equal(empty['financial-indicators'].groups.assets[0].value, null)
  assert.equal(empty['financial-indicators'].groups.assets[0].status, '판정 불가')
})

test('DSR 기준은 공용 정책의 60~64세·65세 이상 구간을 따른다', () => {
  const reports = structuredClone(reportsOf(bundle.scenarios[0]))
  reports['retirement-readiness'].current_age = 60
  reports['financial-indicators'].debt_service_ratio = 10
  assert.equal(normalizePresentationData(reports)['financial-indicators'].groups.assets[1].status, '참고 기준 충족')
  reports['retirement-readiness'].current_age = 65
  assert.equal(normalizePresentationData(reports)['financial-indicators'].groups.assets[1].status, '참고 기준 밖')
  reports['financial-indicators'].debt_service_ratio = 0
  assert.equal(normalizePresentationData(reports)['financial-indicators'].groups.assets[1].status, '참고 기준 충족')
})
