import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { applyMindsetResult, createMindsetState, isMindsetResult, MINDSET_FIELDS, mindsetRequestState } from './mindsetElicitation.js'
import { SSEProtocolError, validateV2SSEEvents } from '../api/sse.js'
import { validateConsultationForm } from './formValidation.js'

const contract = JSON.parse(readFileSync(new URL('../../../contract-fixtures/ai-lifecycle/v2/mindset-elicitation.json', import.meta.url), 'utf8'))
const result = (overrides = {}) => ({
  field: 'values', summary: '생활의 안정을 중시함.', answer_count: 1,
  status: 'in_progress', completion_reason: null, next_field: null, ...overrides,
})
const form = () => Object.fromEntries(MINDSET_FIELDS.map(({ formKey }) => [formKey, '']))

test('경제관념 공용 fixture의 항목 순서와 결과를 검증한다', () => {
  assert.deepEqual(MINDSET_FIELDS.map(({ field }) => field), contract.fields)
  for (const item of contract.result_cases) assert.equal(isMindsetResult(item.result), item.valid, item.id)
})

test('현재 항목 요약만 즉시 반영하고 다른 항목의 사용자 입력은 보존한다', () => {
  const state = createMindsetState()
  const draft = { ...form(), futureGoals: '사용자가 직접 작성한 목표' }
  applyMindsetResult(state, draft, result(), 'request-1', new Set())
  assert.equal(draft.values, '생활의 안정을 중시함.')
  assert.equal(draft.futureGoals, '사용자가 직접 작성한 목표')
  assert.deepEqual(mindsetRequestState(state), { event: 'answer', field: 'values', answer_count: 1, completed_fields: [] })
})

test('QA 명확화는 답변 횟수를 소비하지 않고 서버 결과로 다음 항목을 연다', () => {
  const state = createMindsetState()
  const draft = form()
  const receipts = new Set()
  applyMindsetResult(state, draft, result({ summary: '', answer_count: 0 }), 'opened', receipts)
  applyMindsetResult(state, draft, result({ summary: '', answer_count: 0 }), 'clarification', receipts)
  assert.equal(state.records.values.answerCount, 0)
  applyMindsetResult(state, draft, result({ status: 'completed', completion_reason: 'sufficient', next_field: 'past_experience' }), 'complete', receipts)
  assert.deepEqual(mindsetRequestState(state), { event: 'answer', field: 'past_experience', answer_count: 0, completed_fields: ['values'] })
  assert.equal(state.started, true)
})

test('재생 응답은 편집한 요약을 덮어쓰거나 완료 항목을 재시작하지 않는다', () => {
  const state = createMindsetState()
  const draft = form()
  const receipts = new Set()
  const completed = result({ status: 'completed', completion_reason: 'sufficient', next_field: 'past_experience' })
  applyMindsetResult(state, draft, completed, 'complete', receipts)
  draft.values = '사용자 수정'
  assert.equal(applyMindsetResult(state, draft, completed, 'complete', receipts), false)
  assert.equal(draft.values, '사용자 수정')
  assert.equal(state.activeField, 'past_experience')
  assert.deepEqual(state.completedFields, ['values'])
})

test('세 번째 답변에서 마치고 모름·거절은 답변 없이도 다음 항목으로 이동한다', () => {
  const state = createMindsetState()
  const draft = form()
  const receipts = new Set()
  state.records.values.answerCount = 2
  applyMindsetResult(state, draft, result({ answer_count: 3, status: 'completed', completion_reason: 'limit', next_field: 'past_experience' }), 'limit', receipts)
  applyMindsetResult(state, draft, result({ field: 'past_experience', summary: '', answer_count: 0, status: 'completed', completion_reason: 'skipped', next_field: 'current_situation' }), 'skip', receipts)
  assert.equal(state.activeField, 'current_situation')
  assert.deepEqual(state.completedFields, ['values', 'past_experience'])
})

test('전 항목 완료는 일반 채팅으로 돌아가고 새 화면 상태는 처음부터 시작한다', () => {
  const state = createMindsetState()
  const draft = form()
  const receipts = new Set()
  for (const [index, { field }] of MINDSET_FIELDS.entries()) {
    applyMindsetResult(state, draft, result({ field, status: 'completed', completion_reason: 'sufficient', next_field: MINDSET_FIELDS[index + 1]?.field ?? null }), `complete-${field}`, receipts)
  }
  assert.equal(mindsetRequestState(state), null)
  assert.equal(state.completedFields.length, 6)
  assert.deepEqual(mindsetRequestState(createMindsetState(), 'start'), contract.request_cases.find(({ id }) => id === 'start').state)
})

test('빈 폼에서 모든 답변을 보류해도 사실 기록으로 기존 저장 검증을 통과한다', () => {
  const state = createMindsetState()
  const draft = form()
  const receipts = new Set()
  assert.equal(validateConsultationForm('financial-mindset', draft).valid, false)
  for (const [index, { field }] of MINDSET_FIELDS.entries()) {
    applyMindsetResult(state, draft, result({
      field, summary: '이 항목에 대한 답변을 보류함.', answer_count: 0,
      status: 'completed', completion_reason: 'skipped', next_field: MINDSET_FIELDS[index + 1]?.field ?? null,
    }), `skip-${field}`, receipts)
  }
  assert.equal(mindsetRequestState(state), null)
  assert.deepEqual(Object.values(draft), Array(6).fill('이 항목에 대한 답변을 보류함.'))
  assert.equal(validateConsultationForm('financial-mindset', draft).valid, true)
})

test('다른 항목이나 역행한 응답은 폼 변경 전에 거부한다', () => {
  const state = createMindsetState()
  const draft = form()
  state.records.values.answerCount = 1
  for (const invalid of [result({ field: 'past_experience' }), result({ answer_count: 0 })]) {
    assert.throws(() => applyMindsetResult(state, draft, invalid, 'bad', new Set()))
    assert.deepEqual(draft, form())
  }
})

test('SSE는 잘못된 자동반영 결과를 표시 전에 차단한다', async () => {
  async function* events(elicitation) {
    yield { event: 'start', data: { contract_version: '2.0', request_id: 'req' } }
    yield { event: 'done', data: {
      contract_version: '2.0', request_id: 'req', status: 'completed',
      message: { id: 1, role: 'assistant', content: '응답', extracted_data: { elicitation } },
    } }
  }
  for (const item of contract.result_cases) {
    const consume = async () => { for await (const event of validateV2SSEEvents(events(item.result))) void event }
    if (item.valid) await assert.doesNotReject(consume, item.id)
    else await assert.rejects(consume, SSEProtocolError, item.id)
  }
  for (const absent of [null, undefined]) {
    await assert.doesNotReject(async () => {
      for await (const event of validateV2SSEEvents(events(absent))) void event
    })
  }
})
