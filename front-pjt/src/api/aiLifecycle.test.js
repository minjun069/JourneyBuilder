import assert from 'node:assert/strict'
import test from 'node:test'

import {
  AILifecycleError,
  createIdempotencyKey,
  deriveV2BaseURL,
  lifecycleErrorFromResponse,
  validateDecisionEnvelope,
} from './aiLifecycle.js'

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

test('HTTP error envelope를 안전한 lifecycle 오류로 변환한다', () => {
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
