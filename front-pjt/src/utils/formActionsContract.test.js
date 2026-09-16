import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  applyCanonicalChanges,
  applyErrorReviewSuggestion,
  cancelErrorReviewSuggestion,
  continueSaveAfterOptionalReview,
  errorReviewConfidenceLevel,
  readCanonicalValue,
  visibleErrorReviewItems,
} from './formChanges.js'

const fixtureUrl = new URL(
  '../../../contract-fixtures/ai-lifecycle/v2/form-actions.json',
  import.meta.url,
)
const fixture = JSON.parse(readFileSync(fixtureUrl, 'utf8'))

test('every normalized AI change starts pending for a Frontend-local decision', () => {
  for (const [caseName, contractCase] of Object.entries(fixture.cases)) {
    const action = contractCase.django_normalized_action
    assert.equal(action.mode, 'proposal', caseName)
    assert.equal(action.status, 'pending', caseName)
    assert.ok(action.changes.length > 0, caseName)
    assert.ok(
      action.changes.every((change) => change.decision_status === 'pending'),
      caseName,
    )
    assert.deepEqual(
      new Set(contractCase.frontend_decisions.map((decision) => decision.change_id)),
      new Set(action.changes.map((change) => change.change_id)),
      caseName,
    )
  }
})

test('AI output metadata never contains an approval state', () => {
  for (const [caseName, contractCase] of Object.entries(fixture.cases)) {
    const action = contractCase.ai_output_action
    for (const key of ['action_id', 'change_id', 'mode', 'status', 'decision_status']) {
      assert.equal(Object.hasOwn(action, key), false, `${caseName}: ${key}`)
    }
    for (const change of action.changes) {
      assert.equal(Object.hasOwn(change, 'change_id'), false, `${caseName}: change_id`)
      assert.equal(
        Object.hasOwn(change, 'decision_status'),
        false,
        `${caseName}: decision_status`,
      )
    }
  }
})

function withoutGeneratedIds(value) {
  if (Array.isArray(value)) return value.map(withoutGeneratedIds)
  if (value == null || typeof value !== 'object') return value
  return Object.fromEntries(Object.entries(value).map(([key, child]) => {
    if (
      key === 'clientItemId'
      && typeof child === 'string'
      && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(child)
    ) {
      return [key, '<generated-uuid>']
    }
    return [key, withoutGeneratedIds(child)]
  }))
}

test('승인된 D1 fixture 변경은 실제 Frontend draft resolver 결과와 일치한다', () => {
  for (const [caseName, contractCase] of Object.entries(fixture.cases)) {
    const draft = structuredClone(contractCase.initial.frontend_draft)
    const approvedIds = new Set(
      contractCase.frontend_decisions
        .filter((decision) => decision.decision_status === 'approved')
        .map((decision) => decision.change_id),
    )
    const approvedChanges = contractCase.django_normalized_action.changes
      .filter((change) => approvedIds.has(change.change_id))

    applyCanonicalChanges(contractCase.step_id, draft, approvedChanges)

    assert.deepEqual(
      withoutGeneratedIds(draft),
      contractCase.expected.frontend_draft,
      caseName,
    )
  }
})

test('Error Review 후보는 검토 당시 canonical 값과 현재 draft가 같을 때만 식별된다', () => {
  const currentForm = {
    goal_periods: [{
      client_item_id: 'period-1',
      items: [{ client_item_id: 'item-1', amount: 20000 }],
    }],
  }
  const change = {
    section: 'goal_periods',
    path: 'items[].amount',
    client_item_id: 'item-1',
    parent_client_item_id: 'period-1',
  }

  assert.deepEqual(readCanonicalValue(currentForm, change), { found: true, value: 20000 })
  assert.deepEqual(
    readCanonicalValue(currentForm, { ...change, parent_client_item_id: 'other-period' }),
    { found: false, value: undefined },
  )
})

test('Error Review confidence는 세 등급으로 나누고 낮음은 숨긴다', () => {
  assert.equal(errorReviewConfidenceLevel(0.59), 'low')
  assert.equal(errorReviewConfidenceLevel(0.60), 'medium')
  assert.equal(errorReviewConfidenceLevel(0.849), 'medium')
  assert.equal(errorReviewConfidenceLevel(0.85), 'high')
  assert.deepEqual(
    visibleErrorReviewItems([
      { confidence: 0.59, confidence_level: 'low' },
      { confidence: 0.75, confidence_level: 'medium' },
      { confidence: 0.91, confidence_level: 'high' },
      { confidence: 0.91, confidence_level: 'medium' },
    ]).map((item) => item.confidence_level),
    ['medium', 'high'],
  )
})

test('Error Review 후보 적용과 취소는 draft만 결정론적으로 바꾼다', () => {
  const form = { earnings: 2 }
  const canonical = { monthly_income: 20000 }
  const applied = {
    section: 'basic_info', path: 'monthly_income', current_value: 20000,
    suggested_value: 2000000, reason: '단위 착오', decision: 'pending',
  }
  assert.equal(
    applyErrorReviewSuggestion('basic-info', form, canonical, applied),
    'applied',
  )
  assert.equal(form.earnings, 200)
  assert.equal(applied.decision, 'applied')

  const cancelled = { decision: 'pending' }
  assert.equal(cancelErrorReviewSuggestion(cancelled), 'cancelled')
  assert.equal(cancelled.decision, 'cancelled')
  assert.equal(form.earnings, 200)
})

test('저장 시 Error Review 실패나 무경고 결과는 기존 저장을 막지 않는다', async () => {
  let saved = 0
  const persist = async () => { saved += 1 }

  assert.equal(
    await continueSaveAfterOptionalReview(async () => { throw new Error('timeout') }, persist),
    'saved',
  )
  assert.equal(
    await continueSaveAfterOptionalReview(async () => false, persist),
    'saved',
  )
  assert.equal(
    await continueSaveAfterOptionalReview(async () => true, persist),
    'review_required',
  )
  assert.equal(saved, 2)
})

test('사용자가 취소한 Error Review 스트림은 저장으로 이어가지 않는다', async () => {
  const abortError = new Error('cancelled')
  abortError.name = 'AbortError'
  let saved = false
  await assert.rejects(
    continueSaveAfterOptionalReview(
      async () => { throw abortError },
      async () => { saved = true },
    ),
    { name: 'AbortError' },
  )
  assert.equal(saved, false)
})
