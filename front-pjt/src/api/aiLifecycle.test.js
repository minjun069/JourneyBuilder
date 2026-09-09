import assert from 'node:assert/strict'
import test from 'node:test'

import {
  AILifecycleError,
  abortableDelay,
  createIdempotencyKey,
  deriveV2BaseURL,
  lifecycleErrorFromResponse,
  pollUntilTerminal,
  terminalLifecycleError,
  validateAcceptedEnvelope,
  validateDecisionEnvelope,
  validatePollingEnvelope,
} from './aiLifecycle.js'


const accepted = {
  contract_version: '2.0',
  success: true,
  data: {
    user_message_id: 10,
    request_id: 'ai_req_test',
    status: 'processing',
    processing_deadline_at: '2026-09-02T06:01:30Z',
  },
}

test('v1 또는 gateway base URL을 frozen v2 경로로 바꾼다', () => {
  assert.equal(deriveV2BaseURL('/api/v1'), '/api/v2')
  assert.equal(deriveV2BaseURL('/api'), '/api/v2')
  assert.equal(deriveV2BaseURL('/api/v2'), '/api/v2')
  assert.equal(deriveV2BaseURL('https://example.test/api/v1/'), 'https://example.test/api/v2')
  assert.equal(deriveV2BaseURL(undefined), '/api/v2')
})

test('브라우저 요청마다 UUID idempotency key를 만든다', () => {
  assert.match(
    createIdempotencyKey(),
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
  )
})

test('HTTP 개발 origin에서도 getRandomValues로 안전한 UUID를 만든다', () => {
  const bytes = Uint8Array.from({ length: 16 }, (_, index) => index)
  const cryptoProvider = {
    getRandomValues(target) {
      target.set(bytes)
      return target
    },
  }

  assert.equal(
    createIdempotencyKey(cryptoProvider),
    '00010203-0405-4607-8809-0a0b0c0d0e0f',
  )
})

test('접수 응답은 v2 envelope와 processing 상태를 모두 검사한다', () => {
  assert.deepEqual(validateAcceptedEnvelope(accepted), accepted.data)
  assert.throws(
    () => validateAcceptedEnvelope({ ...accepted, contract_version: '1.0' }),
    AILifecycleError,
  )
  assert.throws(
    () => validateAcceptedEnvelope({ ...accepted, data: { ...accepted.data, status: 'completed' } }),
    AILifecycleError,
  )
})

test('3초 간격 polling이 completed 메시지에서 멈춘다', async () => {
  const delays = []
  const states = [
    accepted,
    {
      contract_version: '2.0',
      success: true,
      data: {
        request_id: 'ai_req_test',
        status: 'completed',
        message: {
          id: 11,
          role: 'assistant',
          content: '확인해주세요.',
          extracted_data: null,
        },
      },
    },
  ]

  const terminal = await pollUntilTerminal({
    requestId: 'ai_req_test',
    poll: async () => states.shift(),
    delay: async (milliseconds) => delays.push(milliseconds),
  })

  assert.equal(terminal.status, 'completed')
  assert.equal(terminal.message.id, 11)
  assert.deepEqual(delays, [3_000, 3_000])
})

test('polling은 request_id 변경과 알 수 없는 상태를 거절한다', () => {
  assert.throws(() => validatePollingEnvelope({
    ...accepted,
    data: { ...accepted.data, request_id: 'ai_req_other' },
  }, 'ai_req_test'), AILifecycleError)

  assert.throws(() => validatePollingEnvelope({
    ...accepted,
    data: { ...accepted.data, status: 'queued' },
  }, 'ai_req_test'), AILifecycleError)
})

test('90초 동안 processing이면 retryable expired 오류가 된다', async () => {
  let attempts = 0
  await assert.rejects(
    pollUntilTerminal({
      requestId: 'ai_req_test',
      poll: async () => {
        attempts += 1
        return accepted
      },
      delay: async () => {},
    }),
    (error) => {
      assert.equal(error.code, 'AI_REQUEST_EXPIRED')
      assert.equal(error.retryable, true)
      return true
    },
  )
  assert.equal(attempts, 30)
})

test('화면 이탈로 취소된 polling은 즉시 AbortError를 반환한다', async () => {
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(
    abortableDelay(3_000, controller.signal),
    (error) => error.name === 'AbortError',
  )
})

test('failed terminal과 HTTP error envelope를 안전한 오류로 변환한다', () => {
  const terminal = {
    request_id: 'ai_req_test',
    status: 'failed',
    error: { code: 'LLM_TIMEOUT', message: '시간이 초과되었습니다.', retryable: true },
  }
  const terminalError = terminalLifecycleError(terminal)
  assert.equal(terminalError.code, 'LLM_TIMEOUT')
  assert.equal(terminalError.retryable, true)

  const responseError = lifecycleErrorFromResponse({
    response: {
      status: 409,
      data: {
        success: false,
        error: {
          code: 'IDEMPOTENCY_CONFLICT',
          message: '요청 내용이 다릅니다.',
          retryable: false,
        },
      },
    },
  })
  assert.equal(responseError.status, 409)
  assert.equal(responseError.code, 'IDEMPOTENCY_CONFLICT')
})

test('개별 변경 결정 응답은 action과 change 식별자를 다시 확인한다', () => {
  const payload = {
    contract_version: '2.0',
    success: true,
    data: {
      action_id: 'act_test',
      change_id: 'chg_test',
      decision_status: 'approved',
      action_status: 'partially_resolved',
      change: {
        section: 'basic_info',
        path: 'monthly_income',
        operation: 'set',
        value: 3_000_000,
      },
    },
  }
  assert.deepEqual(validateDecisionEnvelope(payload, {
    actionId: 'act_test',
    changeId: 'chg_test',
    decision: 'approve',
  }), payload.data)
  assert.throws(() => validateDecisionEnvelope(payload, {
    actionId: 'act_other',
    changeId: 'chg_test',
    decision: 'approve',
  }), AILifecycleError)
})
