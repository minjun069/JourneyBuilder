import assert from 'node:assert/strict'
import test from 'node:test'

import {
  SimulationDataError,
  normalizeSimulationError,
  normalizeSimulationResponse,
} from './simulationData.js'

function result(successProbability = 0.42) {
  return {
    success_probability: successProbability,
    final_balance_percentiles: { p10: -10, p50: 20, p90: 30 },
    asset_percentiles: [
      { age: 60, balance: { p10: 1, p50: 2, p90: 3 } },
    ],
  }
}

function response(mode = 'baseline') {
  const baseline = result()
  return {
    success: true,
    data: {
      baseline_key: 'baseline-key',
      analysis: {
        contract_version: 'retirement-analysis-v1',
        mode,
        units: { amount: 'KRW', probability: 'fraction', probability_delta: 'pp' },
        notices: [],
        baseline_profile: {
          current_age: 40,
          retirement_age: 60,
          end_age: 90,
          monthly_savings: 1_000_000,
          retirement_expenses: [{ from_age: 60, to_age: 90, monthly_amount: 2_000_000 }],
        },
        metadata: { num_paths: 1_000 },
        baseline,
        scenarios: mode === 'baseline' ? [] : [{
          scenario_id: 'comparison',
          changes: { monthly_savings: 1_000_000 },
          profile: {
            current_age: 40,
            retirement_age: 60,
            end_age: 90,
            monthly_savings: 1_000_000,
            retirement_expenses: [{ from_age: 60, to_age: 90, monthly_amount: 2_000_000 }],
          },
          result: result(0.5),
          delta: {
            success_probability_delta_pp: 8,
            final_balance_delta: { p10: 1, p50: 2, p90: 3 },
          },
        }],
      },
    },
    meta: { simulation_policy_version: 'simulation-policy-v1' },
  }
}

test('기준·What-if 응답을 검증하고 key와 meta를 보존한다', () => {
  assert.equal(normalizeSimulationResponse(response()).baselineKey, 'baseline-key')
  assert.equal(normalizeSimulationResponse(response('what_if')).analysis.scenarios.length, 1)
})

test('비유한 숫자나 확률 범위를 0으로 바꾸지 않고 거절한다', () => {
  const invalid = response()
  invalid.data.analysis.baseline.success_probability = Number.NaN
  assert.throws(() => normalizeSimulationResponse(invalid), SimulationDataError)

  const outOfRange = response()
  outOfRange.data.analysis.baseline.success_probability = 1.1
  assert.throws(() => normalizeSimulationResponse(outOfRange), SimulationDataError)
})

test('도메인 오류 객체만 안전한 화면 오류로 정규화한다', () => {
  const error = normalizeSimulationError({
    response: {
      status: 409,
      data: { error: { code: 'BASELINE_STALE', message: '다시 실행해 주세요.', retryable: false } },
    },
  })
  assert.equal(error.code, 'BASELINE_STALE')
  assert.equal(error.message, '다시 실행해 주세요.')
  assert.equal(error.status, 409)
})
