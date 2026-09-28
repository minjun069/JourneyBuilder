import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createIdempotencyKey,
  createPresentationEntryIdempotencyKey,
  deriveV2BaseURL,
} from './aiLifecycle.js'

test('gateway root를 consulting 도메인의 frozen v2 경로로 바꾼다', () => {
  assert.equal(deriveV2BaseURL('/api'), '/api/consulting/v2')
  assert.equal(deriveV2BaseURL('https://example.test/api/'), 'https://example.test/api/consulting/v2')
  assert.equal(deriveV2BaseURL(undefined), '/api/consulting/v2')
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

test('같은 차트 방문 재시도는 같은 key, 재방문은 새 key를 만든다', async () => {
  const input = { sessionId: 12, stepId: 'lifetime-cashflow', reportVersion: 'report:7:v2', visitId: 'visit-1' }
  const first = await createPresentationEntryIdempotencyKey(input)
  const second = await createPresentationEntryIdempotencyKey(input)
  const changed = await createPresentationEntryIdempotencyKey({
    ...input,
    reportVersion: 'report:7:v3',
  })
  const revisited = await createPresentationEntryIdempotencyKey({ ...input, visitId: 'visit-2' })

  assert.equal(first, second)
  assert.notEqual(first, changed)
  assert.notEqual(first, revisited)
  assert.match(first, /^presentation-entry-v3-[0-9a-f]{64}$/)
})
