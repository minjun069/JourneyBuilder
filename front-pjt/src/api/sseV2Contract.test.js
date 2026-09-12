import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'


const contract = JSON.parse(readFileSync(
  new URL('../../../contract-fixtures/ai-lifecycle/v2/sse.json', import.meta.url),
  'utf8',
))
const aiChatViewSource = readFileSync(
  new URL('../views/ai/AiChatView.vue', import.meta.url),
  'utf8',
)
const aiStoreSource = readFileSync(
  new URL('../stores/ai.js', import.meta.url),
  'utf8',
)

test('Frontend는 인증과 멱등 키가 필요한 v2 SSE endpoint를 사용한다', () => {
  assert.match(contract.browser_endpoint, /^\/api\/v2\//)
  assert.equal(contract.content_type, 'text/event-stream')
  assert.ok(contract.required_browser_headers.includes('Authorization'))
  assert.ok(contract.required_browser_headers.includes('Idempotency-Key'))
})

test('Frontend 정상 이벤트는 start, delta, done 순서다', () => {
  assert.deepEqual(
    contract.browser_events.map(({ event }) => event),
    ['start', 'delta', 'done'],
  )
})

test('모든 Browser 이벤트는 동일 v2 request identity를 유지한다', () => {
  for (const { data } of contract.browser_events) {
    assert.equal(data.contract_version, '2.0')
    assert.equal(data.request_id, 'ai_req_fixture')
  }
})

test('처리 중 중복은 새 요청을 만들지 않고 완료 결과만 재생한다', () => {
  assert.equal(contract.idempotency.same_key_processing.http_status, 409)
  assert.deepEqual(
    contract.idempotency.same_key_completed.replay_events,
    ['start', 'done'],
  )
})

test('Elicit start, resume, continuation은 모두 v2 SSE를 사용한다', () => {
  assert.deepEqual(contract.channel_policy.elicit, {
    start: 'sse',
    resume: 'sse',
    continuation: 'sse',
  })

  const elicitAction = aiChatViewSource.slice(
    aiChatViewSource.indexOf('async function runElicitAction'),
    aiChatViewSource.indexOf('function dismissAction'),
  )
  assert.match(elicitAction, /streamAssistantMessage\(text/)
  assert.doesNotMatch(elicitAction, /aiStore\.sendMessage/)

  const pollingFallback = aiStoreSource.slice(
    aiStoreSource.indexOf('async function sendMessage'),
    aiStoreSource.indexOf('async function* streamMessageV2'),
  )
  assert.doesNotMatch(pollingFallback, /elicitAction|elicit_action/)
})
