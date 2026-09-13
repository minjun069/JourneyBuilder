const DECISION_STATUSES = new Set(['approved', 'rejected'])

function actionStatus(changes) {
  const pending = changes.filter((change) => change.decision_status === 'pending').length
  if (pending === 0) return 'resolved'
  if (pending === changes.length) return 'pending'
  return 'partially_resolved'
}

export function decideFormChangeLocally(action, change, decision, applyApprovedChange) {
  if (!action || !Array.isArray(action.changes) || !action.changes.includes(change)) {
    throw new TypeError('AI 변경 제안에 포함된 change가 아닙니다.')
  }
  if (!DECISION_STATUSES.has(decision)) {
    throw new TypeError('지원하지 않는 AI 변경 결정입니다.')
  }
  if (change.decision_status !== 'pending') return false

  if (decision === 'approved') {
    if (typeof applyApprovedChange !== 'function') {
      throw new TypeError('승인된 변경을 draft에 적용할 함수가 필요합니다.')
    }
    applyApprovedChange(change)
  }

  change.decision_status = decision
  action.status = actionStatus(action.changes)
  return true
}
