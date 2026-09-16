const SATISFACTION_FIELDS = [
  'cashflow', 'riskManagement', 'investment', 'management', 'inheritance',
]
const MINDSET_FIELDS = [
  'values', 'pastExperience', 'currentSituation', 'currentChallenge',
  'investPhilosophy', 'futureGoals',
]
const LIST_STEPS = new Set([
  'financial-assets', 'pension-assets', 'real-estate', 'liabilities', 'income', 'expenses',
])

function blank(value) {
  return value == null || (typeof value === 'string' && value.trim() === '')
}

function numberInRange(value, min, max) {
  if (blank(value)) return false
  const number = Number(value)
  return Number.isFinite(number) && number >= min && number <= max
}

function numericValue(value) {
  if (blank(value)) return null
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  const match = String(value).replaceAll(',', '').match(/-?\d+(?:\.\d+)?/)
  return match ? Number(match[0]) : null
}

function hasMeaningfulValue(value, ignoredKeys = new Set()) {
  if (Array.isArray(value)) return value.some((item) => hasMeaningfulValue(item, ignoredKeys))
  if (value && typeof value === 'object') {
    return Object.entries(value).some(([key, child]) => (
      !ignoredKeys.has(key) && hasMeaningfulValue(child, ignoredKeys)
    ))
  }
  if (typeof value === 'boolean') return true
  if (typeof value === 'number') return Number.isFinite(value) && value !== 0
  return !blank(value)
}

function emptyFormResult(label, canConfirmEmpty = false) {
  return {
    valid: false,
    code: 'empty_form',
    canConfirmEmpty,
    errors: [`${label}에 입력된 내용이 없습니다.`],
  }
}

export function validateConsultationForm(stepId, form, { allowEmpty = false } = {}) {
  const value = form ?? {}

  if (stepId === 'basic-info') {
    const errors = []
    if (!numberInRange(value.age, 18, 100)) errors.push('나이는 18세 이상 100세 이하로 입력해주세요.')
    if (blank(value.job)) errors.push('직업을 선택해주세요.')
    if (blank(value.marital)) errors.push('기혼 여부를 선택해주세요.')
    if (!numberInRange(value.retirementAge, 18, 100)) errors.push('예상 은퇴 나이는 18세 이상 100세 이하로 입력해주세요.')
    if (!numberInRange(value.endAge, 18, 120)) errors.push('예상 사망 나이는 18세 이상 120세 이하로 입력해주세요.')
    if (!errors.length && Number(value.retirementAge) < Number(value.age)) {
      errors.push('예상 은퇴 나이는 현재 나이보다 빠를 수 없습니다.')
    }
    if (!errors.length && Number(value.endAge) <= Number(value.retirementAge)) {
      errors.push('예상 사망 나이는 예상 은퇴 나이보다 커야 합니다.')
    }
    if (Number(value.earnings) < 0) errors.push('소득은 0원 이상이어야 합니다.')
    return { valid: errors.length === 0, code: errors.length ? 'invalid_fields' : null, canConfirmEmpty: false, errors }
  }

  if (stepId === 'financial-mindset') {
    const filled = MINDSET_FIELDS.filter((field) => !blank(value[field]))
    const errors = filled.length ? [] : ['경제 관념 항목을 하나 이상 작성해주세요.']
    return { valid: errors.length === 0, code: errors.length ? 'empty_form' : null, canConfirmEmpty: false, errors }
  }

  if (stepId === 'financial-satisfaction') {
    const missing = SATISFACTION_FIELDS.filter((field) => !numberInRange(value[field], 1, 5))
    const errors = missing.length ? ['재무 만족도 5개 항목을 모두 1~5점으로 선택해주세요.'] : []
    return { valid: errors.length === 0, code: errors.length ? 'invalid_fields' : null, canConfirmEmpty: false, errors }
  }

  if (LIST_STEPS.has(stepId)) {
    const rows = Array.isArray(value.assets) ? value.assets : []
    const hasInput = rows.some((row) => hasMeaningfulValue(row, new Set(['id', 'clientItemId'])))
    if (!hasInput && !allowEmpty) return emptyFormResult('현재 단계', stepId === 'liabilities')
    const amountFields = {
      'financial-assets': ['principal', 'value', 'monthly'],
      'pension-assets': ['value', 'monthly', 'expectedAmount'],
      'real-estate': ['value', 'purchase', 'monthly', 'deposit', 'loan'],
      liabilities: ['value'],
      income: ['amount'],
      expenses: ['value'],
    }[stepId]
    const errors = []
    rows.forEach((row, index) => {
      if (amountFields.some((field) => numericValue(row[field]) < 0)) {
        errors.push(`${index + 1}번째 항목의 금액은 0원 이상이어야 합니다.`)
      }
      if (stepId === 'pension-assets') {
        const rate = numericValue(row.rate)
        const age = numericValue(row.age)
        if (rate != null && (rate < 0 || rate > 20)) errors.push(`${index + 1}번째 기대수익률은 0~20%로 입력해주세요.`)
        if (age != null && (age < 55 || age > 80)) errors.push(`${index + 1}번째 연금 개시 연령은 55~80세로 입력해주세요.`)
      }
      if (stepId === 'liabilities') {
        const rate = numericValue(row.rate)
        if (rate != null && (rate < 0 || rate > 100)) errors.push(`${index + 1}번째 대출금리는 0~100%로 입력해주세요.`)
        if (row.loan && row.maturity && row.maturity < row.loan) {
          errors.push(`${index + 1}번째 만기일은 대출일보다 빠를 수 없습니다.`)
        }
      }
    })
    return { valid: errors.length === 0, code: errors.length ? 'invalid_fields' : null, canConfirmEmpty: false, errors }
  }

  if (stepId === 'financial-goals') {
    const phases = ['before', 'after']
    const hasGoal = phases.some((phase) => (
      (value[phase]?.periods ?? []).length > 0 || (value[phase]?.milestones ?? []).some((item) => item.age)
    ))
    if (!hasGoal && !allowEmpty) return emptyFormResult('재무목표 단계')
    const errors = []
    for (const phase of phases) {
      for (const period of value[phase]?.periods ?? []) {
        const from = numericValue(period.fromAge)
        const to = numericValue(period.toAge)
        if (from == null || to == null || from >= to) {
          errors.push('재무목표 기간의 시작 나이는 종료 나이보다 작아야 합니다.')
        }
        if ((period.items ?? []).some((item) => numericValue(item.amount) < 0)) {
          errors.push('재무목표 기간의 금액은 0원 이상이어야 합니다.')
        }
      }
      if ((value[phase]?.milestones ?? []).some((item) => numericValue(item.amount) < 0)) {
        errors.push('재무 이벤트 금액은 0원 이상이어야 합니다.')
      }
    }
    return { valid: errors.length === 0, code: errors.length ? 'invalid_fields' : null, canConfirmEmpty: false, errors }
  }

  return { valid: true, code: null, canConfirmEmpty: false, errors: [] }
}
