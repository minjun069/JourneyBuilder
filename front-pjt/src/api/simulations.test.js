import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { createBaselineRequest, createWhatIfRequest } from './simulations.js'

const contract = JSON.parse(readFileSync(
  new URL('../../../contract-fixtures/simulation/v1/http.json', import.meta.url),
  'utf8',
))

test('Simulation client는 simulations 도메인의 v1 계약을 사용한다', () => {
  assert.equal(contract.endpoint, '/api/simulations/v1/consultations/{session_id}/simulations/')
  assert.equal(contract.error_schema_version, 'simulation-error-v1')
  assert.equal(contract.persistence, 'none')
})

test('기준 실행 요청은 서버 소유 가정을 보내지 않는다', () => {
  assert.deepEqual(createBaselineRequest(), { mode: 'baseline' })
})

test('What-if 요청은 기준 키와 지원된 변경값만 보낸다', () => {
  assert.deepEqual(
    createWhatIfRequest('baseline-key', {
      monthly_savings: 0,
      retirement_age: null,
    }),
    {
      mode: 'what_if',
      baseline_key: 'baseline-key',
      changes: { monthly_savings: 0 },
    },
  )
})

test('브라우저가 계산 가정을 요청에 추가하지 못한다', () => {
  assert.throws(
    () => createWhatIfRequest('baseline-key', { annual_return_mean: 0.05 }),
    /지원하지 않는 시뮬레이션 조건/,
  )
})

test('Client의 변경 항목은 Simulation 도메인 계약과 일치한다', () => {
  assert.deepEqual(contract.supported_changes, [
    'monthly_savings',
    'retirement_age',
    'retirement_expenses',
  ])
})
