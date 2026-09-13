import assert from 'node:assert/strict'
import test from 'node:test'

import { decideFormChangeLocally } from './formDecision.js'

function proposal() {
  const changes = [
    { change_id: 'chg_income', decision_status: 'pending', value: 3_000_000 },
    { change_id: 'chg_job', decision_status: 'pending', value: '직장인' },
  ]
  return { status: 'pending', changes }
}

test('승인은 AI 응답의 change를 즉시 FE draft에 적용하고 로컬 상태만 바꾼다', () => {
  const action = proposal()
  const draft = { monthlyIncome: 2_500_000 }

  const changed = decideFormChangeLocally(
    action,
    action.changes[0],
    'approved',
    (change) => { draft.monthlyIncome = change.value },
  )

  assert.equal(changed, true)
  assert.equal(draft.monthlyIncome, 3_000_000)
  assert.equal(action.changes[0].decision_status, 'approved')
  assert.equal(action.status, 'partially_resolved')
})

test('거절은 draft 적용 없이 제안 상태만 로컬에서 종료한다', () => {
  const action = proposal()
  let applied = false

  decideFormChangeLocally(
    action,
    action.changes[0],
    'rejected',
    () => { applied = true },
  )
  decideFormChangeLocally(
    action,
    action.changes[1],
    'rejected',
    () => { applied = true },
  )

  assert.equal(applied, false)
  assert.equal(action.changes[0].decision_status, 'rejected')
  assert.equal(action.status, 'resolved')
})

test('draft 적용 실패 시 승인을 종료하지 않아 사용자가 다시 결정할 수 있다', () => {
  const action = proposal()

  assert.throws(() => decideFormChangeLocally(
    action,
    action.changes[0],
    'approved',
    () => { throw new Error('invalid target') },
  ))
  assert.equal(action.changes[0].decision_status, 'pending')
  assert.equal(action.status, 'pending')
})
