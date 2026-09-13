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
const consultationStoreSource = readFileSync(
  new URL('../stores/consultation.js', import.meta.url),
  'utf8',
)
const consultingApiSource = readFileSync(
  new URL('./consulting.js', import.meta.url),
  'utf8',
)
const lifecycleSource = readFileSync(
  new URL('./aiLifecycle.js', import.meta.url),
  'utf8',
)

test('Frontend는 인증과 멱등 키가 필요한 v2 SSE endpoint를 사용한다', () => {
  assert.match(contract.browser_endpoint, /^\/api\/v2\//)
  assert.equal(contract.content_type, 'text/event-stream')
  assert.ok(contract.required_browser_headers.includes('Authorization'))
  assert.ok(contract.required_browser_headers.includes('Idempotency-Key'))
  assert.equal(contract.timeouts_seconds.stream_read_idle, 120)
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
  assert.doesNotMatch(elicitAction, /consultationStore\.sendMessage/)
})

test('Error Review 저장·명시 요청은 구조화된 done을 v2 SSE로 받는다', () => {
  assert.deepEqual(contract.channel_policy.error_review, {
    save: 'sse',
    user_request: 'sse',
  })

  const reviewForm = consultationStoreSource.slice(
    consultationStoreSource.indexOf('async function reviewForm'),
    consultationStoreSource.indexOf('function cancelActiveStream'),
  )
  assert.match(reviewForm, /streamMessageV2/)
  assert.match(reviewForm, /requestMode: 'error_review'/)
  assert.match(reviewForm, /reviewTrigger: trigger/)
  assert.match(reviewForm, /event === 'done'/)
  assert.doesNotMatch(reviewForm, /poll|sendMessage/)
})

test('Frontend 제품 경로에는 callback polling helper가 남지 않는다', () => {
  assert.deepEqual(contract.channel_policy.frontend_polling_flows, [])
  assert.doesNotMatch(consultationStoreSource, /pollUntilTerminal|postAsyncMessage|getAsyncMessage/)
  assert.doesNotMatch(consultingApiSource, /postAsyncMessage|getAsyncMessage|\bpostMessage\b|\bgetMessage\b/)
  assert.doesNotMatch(consultingApiSource, /\/ai-responses\//)
  assert.doesNotMatch(lifecycleSource, /pollUntilTerminal|validatePollingEnvelope/)
})

test('AI 변경 승인·거절은 Frontend 로컬 처리이며 네트워크 요청을 만들지 않는다', () => {
  assert.deepEqual(contract.channel_policy.change_decision, {
    owner: 'frontend',
    transport: 'none',
    persists_on_server: false,
  })
  const decisionHandler = aiChatViewSource.slice(
    aiChatViewSource.indexOf('function decideFormChange'),
    aiChatViewSource.indexOf('const approveFormChange'),
  )
  assert.match(decisionHandler, /decideFormChangeLocally/)
  assert.doesNotMatch(decisionHandler, /await|consultationStore\./)
  assert.doesNotMatch(consultingApiSource, /\/ai-actions\//)
  assert.doesNotMatch(consultationStoreSource, /approveChange|rejectChange|decisionIdempotency/)
})
