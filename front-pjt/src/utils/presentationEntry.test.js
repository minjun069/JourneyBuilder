import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createPresentationEntryVisit,
  isSuccessfulPresentationEntryMessage,
} from './presentationEntry.js'


test('같은 뷰 방문의 중복 trigger는 진행 중과 성공 후 모두 막는다', () => {
  const visit = createPresentationEntryVisit({
    sessionId: 12,
    stepId: 'lifetime-cashflow',
    visitId: 'first-visit',
  })
  assert.equal(visit.begin('report:7:v2'), true)
  assert.equal(visit.begin('report:7:v2'), false)
  visit.finish('report:7:v2', { extracted_data: { presentation_entry_status: 'succeeded' } })
  assert.equal(visit.begin('report:7:v2'), false)
})

test('같은 차트를 재방문하면 같은 보고서도 새 안내를 시작한다', () => {
  const input = { sessionId: 12, stepId: 'balance-sheet' }
  const previous = createPresentationEntryVisit({ ...input, visitId: 'first-visit' })
  previous.begin('report:9:v1')
  previous.finish('report:9:v1', { extracted_data: { presentation_entry_status: 'succeeded' } })
  const current = createPresentationEntryVisit({ ...input, visitId: 'second-visit' })
  assert.equal(current.begin('report:9:v1'), true)
  assert.notEqual(current.visitId, previous.visitId)
})

test('검증된 Presentation 성공 응답만 완료 receipt 대상이다', () => {
  assert.equal(isSuccessfulPresentationEntryMessage({
    extracted_data: { presentation_entry_status: 'succeeded' },
  }), true)
  assert.equal(isSuccessfulPresentationEntryMessage({
    extracted_data: { presentation_entry_status: 'failed' },
  }), false)
  assert.equal(isSuccessfulPresentationEntryMessage({ extracted_data: {} }), false)
})

test('같은 방문에서 보고서 갱신과 실패 후 재시도를 허용한다', () => {
  const visit = createPresentationEntryVisit({ sessionId: 12, stepId: 'cash-flow', visitId: 'visit' })
  assert.equal(visit.begin('report:8:v1'), true)
  visit.finish('report:8:v1', { extracted_data: { presentation_entry_status: 'failed' } })
  assert.equal(visit.begin('report:8:v1'), true)
  visit.finish('report:8:v1', { extracted_data: { presentation_entry_status: 'succeeded' } })
  assert.equal(visit.begin('report:8:v2'), true)
})
