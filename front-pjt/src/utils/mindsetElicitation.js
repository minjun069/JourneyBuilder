export const MINDSET_FIELDS = [
  { field: 'values', formKey: 'values', label: '가치관' },
  { field: 'past_experience', formKey: 'pastExperience', label: '과거 경험' },
  { field: 'current_situation', formKey: 'currentSituation', label: '현재 상황' },
  { field: 'current_challenge', formKey: 'currentChallenge', label: '당면 과제' },
  { field: 'invest_philosophy', formKey: 'investPhilosophy', label: '투자 철학' },
  { field: 'future_goals', formKey: 'futureGoals', label: '미래 목표' },
]

export function createMindsetState() {
  return {
    started: false,
    activeField: MINDSET_FIELDS[0].field,
    completedFields: [],
    records: Object.fromEntries(MINDSET_FIELDS.map(({ field }) => [field, {
      answerCount: 0,
      status: 'pending',
      completionReason: null,
    }])),
  }
}

export function mindsetRequestState(state, event = 'answer') {
  if (!state.activeField) return null
  return {
    event,
    field: state.activeField,
    answer_count: state.records[state.activeField].answerCount,
    completed_fields: [...state.completedFields],
  }
}

export function isMindsetResult(result) {
  if (!result || typeof result !== 'object' || Array.isArray(result)) return false
  const expected = ['field', 'summary', 'answer_count', 'status', 'completion_reason', 'next_field']
  if (Object.keys(result).length !== expected.length || expected.some((key) => !(key in result))) return false
  const index = MINDSET_FIELDS.findIndex(({ field }) => field === result.field)
  if (index < 0 || typeof result.summary !== 'string') return false
  if (!Number.isInteger(result.answer_count) || result.answer_count < 0 || result.answer_count > 3) return false
  if (result.status === 'in_progress') {
    return result.answer_count < 3 && result.completion_reason === null && result.next_field === null
  }
  if (result.status !== 'completed') return false
  if (!['sufficient', 'limit', 'skipped'].includes(result.completion_reason)) return false
  if (result.completion_reason === 'skipped' && result.answer_count > 2) return false
  if (['sufficient', 'limit'].includes(result.completion_reason)
    && (!result.summary.trim() || result.answer_count === 0)) return false
  if (result.completion_reason === 'limit' && result.answer_count !== 3) return false
  return result.next_field === (MINDSET_FIELDS[index + 1]?.field ?? null)
}

// Keep dialogue progress separate from the editable form. Editing a completed
// summary never reopens that topic, and a replay never reapplies an old summary.
export function applyMindsetResult(state, form, result, requestId, receipts) {
  if (receipts.has(requestId)) return false
  if (!requestId || !isMindsetResult(result) || result.field !== state.activeField) {
    throw new Error('경제 관념 응답이 현재 상담 항목과 일치하지 않습니다.')
  }
  const record = state.records[result.field]
  if (result.answer_count < record.answerCount || result.answer_count > record.answerCount + 1) {
    throw new Error('경제 관념 응답의 답변 횟수가 올바르지 않습니다.')
  }
  const { formKey } = MINDSET_FIELDS.find(({ field }) => field === result.field)
  form[formKey] = result.summary
  record.answerCount = result.answer_count
  record.status = result.status
  record.completionReason = result.completion_reason
  state.started = true
  if (result.status === 'completed') {
    state.completedFields.push(result.field)
    state.activeField = result.next_field
    if (state.activeField) state.records[state.activeField].status = 'in_progress'
  }
  receipts.add(requestId)
  return true
}
