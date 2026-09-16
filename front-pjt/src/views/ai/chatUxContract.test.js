import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const view = readFileSync(new URL('./AiChatView.vue', import.meta.url), 'utf8')
const guides = readFileSync(new URL('./data/stepGuideMessages.js', import.meta.url), 'utf8')
const store = readFileSync(new URL('../../stores/consultation.js', import.meta.url), 'utf8')

test('새 AI 답변은 공통 애니메이션 큐와 안전한 Markdown 렌더러를 사용한다', () => {
  assert.match(view, /function streamAiMessages/)
  assert.match(view, /animationQueue/)
  assert.match(view, /await streamAiMessages\(\[finalMessage\]/)
  assert.match(view, /renderChatMarkdown/)
  assert.match(view, /addSentenceLineBreaks/)
})

test('폼 저장은 결정론적 검증 뒤 save Error Review만 호출한다', () => {
  const submit = view.slice(view.indexOf('async function submitForm'), view.indexOf('async function confirmEmptyForm'))
  assert.ok(submit.indexOf('validateConsultationForm') < submit.indexOf('requestErrorReview'))
  assert.doesNotMatch(view, /입력값 검토|requestErrorReview\(['"]user_request/)
  assert.match(store, /reviewTrigger: 'save'/)
})

test('상담 이어하기는 세션 진행 상태와 저장 폼을 복원한다', () => {
  assert.match(view, /restoreConsultationProcess/)
  assert.match(view, /resolveResumeStep/)
  assert.match(view, /hydrateActiveForm/)
  assert.match(store, /loadActiveSession/)
  assert.match(store, /loadFormData/)
})

test('재무정보 안내는 말풍선 안에도 다음 단계 action을 제공한다', () => {
  const financialInfo = guides.slice(
    guides.indexOf("'financial-info':"),
    guides.indexOf("'financial-assets':"),
  )
  assert.match(financialInfo, /type: 'next-step'/)
  assert.match(financialInfo, /stepId: 'financial-assets'/)
})

test('중복 AI 스트림은 기존 요청을 취소하지 않고 거절하며 idle timeout을 둔다', () => {
  const stream = store.slice(
    store.indexOf('async function* streamMessageV2'),
    store.indexOf('async function reviewForm'),
  )
  assert.match(stream, /AI_STREAM_IN_PROGRESS/)
  assert.doesNotMatch(stream, /cancelActiveStream\(\)/)
  assert.match(store, /STREAM_IDLE_TIMEOUT_MS = 120_000/)
})
