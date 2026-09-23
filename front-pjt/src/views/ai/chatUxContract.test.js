import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const view = readFileSync(new URL('./AiChatView.vue', import.meta.url), 'utf8')
const guides = readFileSync(new URL('./data/stepGuideMessages.js', import.meta.url), 'utf8')
const store = readFileSync(new URL('../../stores/consultation.js', import.meta.url), 'utf8')
const validation = readFileSync(new URL('../../utils/formValidation.js', import.meta.url), 'utf8')
const consultingApi = readFileSync(new URL('../../api/consulting.js', import.meta.url), 'utf8')
const reportStore = readFileSync(new URL('../../stores/report.js', import.meta.url), 'utf8')
const addableTable = readFileSync(new URL('../../components/ui/AddableTable.vue', import.meta.url), 'utf8')

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
  assert.doesNotMatch(store, /reviewTrigger|review_trigger/)
})

test('Error Review 처리 상태와 오류를 스크롤 밖의 현재 화면에 표시한다', () => {
  const chatListEnd = view.indexOf('<div v-if="reviewLoading" class="chat-operation-status"')
  const composerStart = view.indexOf('<div v-if="hasActiveSession" class="composer">')

  assert.match(view, /role="status" aria-live="polite"/)
  assert.match(view, /입력값의 이상 여부를 검토하고 있습니다\./)
  assert.ok(chatListEnd > view.indexOf('ref="chatSpacerRef"'))
  assert.ok(chatListEnd < composerStart)
  assert.match(view.slice(chatListEnd, composerStart), /class="chat-error" role="alert"/)
})

test('서버 저장 성공 뒤 화면 snapshot 실패는 저장 실패로 처리하지 않는다', () => {
  const persist = view.slice(
    view.indexOf('async function persistFormAndAdvance'),
    view.indexOf('// ─── 프레젠테이션 슬라이드'),
  )
  const saveFailure = persist.indexOf("'입력값을 저장하지 못했습니다. 내용을 확인한 뒤 다시 시도해주세요.'")
  const snapshotCapture = persist.indexOf('captureActiveFormDraft()', saveFailure)

  assert.ok(saveFailure >= 0)
  assert.ok(snapshotCapture > saveFailure)
  assert.match(persist, /입력값은 서버에 저장됐지만 화면 임시본을 만들지 못했습니다/)
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

test('재무 만족도 안내는 다섯 영역의 구체적인 설명을 후속 말풍선으로 제공한다', () => {
  const financialSatisfaction = guides.slice(
    guides.indexOf("'financial-satisfaction':"),
    guides.indexOf("'financial-info':"),
  )

  assert.doesNotMatch(financialSatisfaction, /type: 'container'/)
  assert.match(financialSatisfaction, /각 영역의 구체적인 내용은 다음과 같습니다/)
  for (const label of ['현금흐름 관리', '위험 관리', '투자', '재무관리', '상속']) {
    assert.match(financialSatisfaction, new RegExp(`\\*\\*${label}\\*\\*:`))
  }
})

test('빈 금융자산은 명시적인 없음 확인 API로 완료 처리한다', () => {
  assert.match(validation, /보유 금융자산 없음/)
  assert.match(view, /confirmNoFinancialAssets: true/)
  assert.match(store, /async function confirmNoFinancialAssets/)
  assert.match(store, /confirmEmptyFinancialAssets/)
  assert.match(consultingApi, /data\/financial-assets\/confirm-empty\//)
})

test('행 추가형 재무 폼은 넓은 입력 패널과 내용 기반 열 너비를 사용한다', () => {
  for (const stepId of [
    'financial-assets', 'pension-assets', 'real-estate',
    'liabilities', 'income', 'expenses',
  ]) {
    assert.match(view, new RegExp(`'${stepId}'`))
  }
  assert.match(view, /TABULAR_FORM_STEP_IDS\.has\(stepId\) \? 55 : 71\.4/)
  assert.match(view, /input-panel--tabular/)
  assert.match(addableTable, /function inputWidth/)
  assert.match(addableTable, /--input-width/)
  assert.match(addableTable, /width: var\(--input-width, 100%\)/)
})

test('중복 AI 스트림은 기존 요청을 취소하지 않고 거절하며 idle timeout을 둔다', () => {
  const stream = store.slice(
    store.indexOf('async function* streamMessageV2'),
    store.indexOf('async function reviewForm'),
  )
  assert.match(stream, /AI_STREAM_IN_PROGRESS/)
  assert.doesNotMatch(stream, /cancelActiveStream\(\)/)
  assert.match(store, /STREAM_IDLE_TIMEOUT_MS = 120_000/)
  assert.match(stream, /resolvedIdempotencyKey/)
  assert.match(stream, /attempt < 2/)
  assert.match(stream, /SSE_TERMINAL_MISSING/)
  assert.match(consultingApi, /'X-AI-Progress-Events': 'v1'/)
})

test('진행 이벤트는 본문 대신 공개 단계 하나만 표시하고 heartbeat는 문구를 바꾸지 않는다', () => {
  assert.match(view, /STREAM_PROGRESS_MESSAGES/)
  assert.match(view, /event === 'progress'/)
  assert.match(view, /event === 'start' \|\| event === 'heartbeat'/)
  assert.match(view, /stream-progress-status/)
  assert.match(view, /streamProgressPhase\.value = null/)
})

test('Presentation 최초 진입은 보고서 버전별 자동 안내를 한 번만 요청한다', () => {
  const entry = view.slice(
    view.indexOf('async function maybeSendPresentationEntryGuide'),
    view.indexOf('async function sendMessage'),
  )

  assert.match(entry, /presentationGuideInFlight/)
  assert.match(entry, /wasPresentationGuideConsumed/)
  assert.match(entry, /createPresentationEntryIdempotencyKey/)
  assert.match(entry, /requestMode: 'presentation_entry'/)
  assert.match(entry, /isSuccessfulPresentationEntryMessage/)
  assert.ok(
    entry.indexOf('isSuccessfulPresentationEntryMessage')
      < entry.indexOf('rememberPresentationGuide'),
  )
  assert.match(view, /system_event === 'presentation_entry'/)
  assert.match(view, /presentationReportVersion/)
})

test('열 개 Presentation 단계는 보고서 또는 기준 시뮬레이션 버전에 연결된다', () => {
  for (const stepId of [
    'balance-sheet', 'cash-flow', 'financial-indicators',
    'financial-goal-result', 'retirement-fund', 'retirement-readiness',
    'retirement-simulation', 'pension-schedule', 'lifetime-cashflow',
    'required-savings',
  ]) {
    assert.match(reportStore, new RegExp(`['"]${stepId}['"]`))
  }
  assert.match(reportStore, /_report_meta/)
  assert.match(reportStore, /simulation\.value\?\.baselineKey/)
})
