import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { applyCanonicalChanges } from './formChanges.js'

const fixtureUrl = new URL(
  '../../../contract-fixtures/ai-lifecycle/v2/form-actions.json',
  import.meta.url,
)
const fixture = JSON.parse(readFileSync(fixtureUrl, 'utf8'))

test('every normalized AI change stays pending until a separate decision', () => {
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
      new Set(contractCase.decisions.map((decision) => decision.change_id)),
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
      contractCase.decisions
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
