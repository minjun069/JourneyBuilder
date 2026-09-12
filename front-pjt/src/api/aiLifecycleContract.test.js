import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  AI_CONTRACT_VERSION,
  lifecycleErrorFromResponse,
  validateDecisionEnvelope,
} from './aiLifecycle.js'


const fixtureUrl = new URL(
  '../../../contract-fixtures/ai-lifecycle/v2/lifecycle.json',
  import.meta.url,
)
const fixture = JSON.parse(readFileSync(fixtureUrl, 'utf8'))


test('공용 lifecycle fixture는 모든 필수 범주와 v2 version을 유지한다', () => {
  const categories = new Set(Object.values(fixture.cases).map(({ category }) => category))
  for (const required of fixture.required_case_categories) {
    assert.equal(categories.has(required), true, required)
  }
  assert.equal(AI_CONTRACT_VERSION, fixture.contract_version)
})

test('개별 승인과 거절 응답이 action/change 식별자에 결합된다', () => {
  for (const [caseName, decision] of [
    ['change_approve_first', 'approve'],
    ['change_reject_second', 'reject'],
  ]) {
    const body = fixture.cases[caseName].response.body
    assert.deepEqual(validateDecisionEnvelope(body, {
      actionId: body.data.action_id,
      changeId: body.data.change_id,
      decision,
    }), body.data)
  }
})

test('공용 오류 envelope의 code/message/retryable을 손실 없이 노출한다', () => {
  const errorCases = Object.entries(fixture.cases)
    .filter(([, contractCase]) => contractCase.response?.body?.success === false)
  assert.ok(errorCases.length > 0)

  for (const [caseName, contractCase] of errorCases) {
    const converted = lifecycleErrorFromResponse({
      response: {
        status: contractCase.response.status,
        data: contractCase.response.body,
      },
    })
    assert.equal(converted.code, contractCase.response.body.error.code, caseName)
    assert.equal(converted.message, contractCase.response.body.error.message, caseName)
    assert.equal(converted.retryable, contractCase.response.body.error.retryable, caseName)
  }
})
