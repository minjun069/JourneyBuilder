import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  AI_CONTRACT_VERSION,
  AI_POLLING_INTERVAL_MS,
  AI_PROCESSING_TIMEOUT_MS,
  lifecycleErrorFromResponse,
  pollUntilTerminal,
  validateAcceptedEnvelope,
  validateDecisionEnvelope,
  validatePollingEnvelope,
} from './aiLifecycle.js'


const fixtureUrl = new URL(
  '../../../contract-fixtures/ai-lifecycle/v2/lifecycle.json',
  import.meta.url,
)
const fixture = JSON.parse(readFileSync(fixtureUrl, 'utf8'))


test('Frontend consumer는 frozen lifecycle의 모든 필수 범주를 읽는다', () => {
  const categories = new Set(Object.values(fixture.cases).map(({ category }) => category))
  for (const required of fixture.required_case_categories) {
    assert.equal(categories.has(required), true, required)
  }
  assert.equal(AI_CONTRACT_VERSION, fixture.contract_version)
})

test('접수와 네 polling 상태가 공용 fixture 그대로 해석된다', () => {
  const accepted = fixture.cases.browser_message_accepted.response.body
  assert.deepEqual(validateAcceptedEnvelope(accepted), accepted.data)

  for (const caseName of [
    'polling_processing',
    'polling_completed_proposal',
    'polling_failed_retryable',
    'polling_expired',
  ]) {
    const body = fixture.cases[caseName].response.body
    assert.deepEqual(
      validatePollingEnvelope(body, body.data.request_id),
      body.data,
      caseName,
    )
  }
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

test('polling 간격·최대 횟수와 terminal 중단이 fixture timing과 일치한다', async () => {
  assert.equal(AI_POLLING_INTERVAL_MS, fixture.timing.polling_interval_seconds * 1_000)
  assert.equal(AI_PROCESSING_TIMEOUT_MS, fixture.timing.processing_deadline_seconds * 1_000)
  const responses = [
    fixture.cases.polling_processing.response.body,
    fixture.cases.polling_completed_proposal.response.body,
  ]
  const delays = []

  const terminal = await pollUntilTerminal({
    requestId: responses[0].data.request_id,
    poll: async () => responses.shift(),
    delay: async (milliseconds) => delays.push(milliseconds),
  })

  assert.equal(terminal.status, 'completed')
  assert.deepEqual(delays, [3_000, 3_000])
})
