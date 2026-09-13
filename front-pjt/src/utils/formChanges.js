import { createSecureUuid } from './secureUuid.js'

export function errorReviewConfidenceLevel(confidence) {
  if (typeof confidence !== 'number' || !Number.isFinite(confidence)) return null
  if (confidence >= 0.85) return 'high'
  if (confidence >= 0.60) return 'medium'
  return 'low'
}

export function visibleErrorReviewItems(items) {
  if (!Array.isArray(items)) return []
  return items.filter((item) => {
    const level = errorReviewConfidenceLevel(item?.confidence)
    return (level === 'medium' || level === 'high') && item?.confidence_level === level
  })
}

export function errorReviewItemChange(item) {
  return {
    section: item.section,
    path: item.path,
    operation: item.item_id != null || item.client_item_id ? 'update' : 'set',
    value: item.suggested_value,
    reason: item.reason,
    ...(item.item_id != null ? { item_id: item.item_id } : {}),
    ...(item.client_item_id ? { client_item_id: item.client_item_id } : {}),
    ...(item.parent_item_id != null ? { parent_item_id: item.parent_item_id } : {}),
    ...(item.parent_client_item_id ? { parent_client_item_id: item.parent_client_item_id } : {}),
  }
}

const SINGLE_FIELDS = {
  'basic-info': {
    age: 'age',
    job: 'job',
    marital_status: 'marital',
    dependents: 'dependents',
    tendency: 'tendency',
    interest: 'interest',
    need: 'need',
    monthly_income: 'earnings',
    retirement_age: 'retirementAge',
    end_age: 'endAge',
  },
  'financial-mindset': {
    values: 'values',
    past_experience: 'pastExperience',
    current_situation: 'currentSituation',
    current_challenge: 'currentChallenge',
    invest_philosophy: 'investPhilosophy',
    future_goals: 'futureGoals',
  },
  'financial-satisfaction': {
    cashflow_score: 'cashflow',
    risk_score: 'riskManagement',
    investment_score: 'investment',
    management_score: 'management',
    inheritance_score: 'inheritance',
  },
}

const LIST_FIELDS = {
  financial_assets: {
    asset_type: 'assetType', name: 'name', principal: 'principal',
    current_value: 'value', monthly_savings: 'monthly', purpose: 'purpose',
  },
  pension_assets: {
    type: 'type', name: 'name', current_value: 'value', monthly_savings: 'monthly',
    expected_return_rate: 'rate', subscription_date: 'date',
    payment_period_years: 'paymentPeriod', receive_method: 'receiveMethod',
    pension_start_age: 'age', pension_period: 'pensionPeriod',
    expected_amount: 'expectedAmount',
  },
  real_estate_assets: {
    type: 'type', name: 'name', current_value: 'value',
    acquisition_price: 'purchase', acquisition_date: 'date',
    monthly_rental_income: 'monthly', rental_deposit: 'deposit', secured_loan: 'loan',
  },
  liabilities: {
    type: 'type', name: 'name', balance: 'value', interest_rate: 'rate',
    rate_type: 'rateType', base_rate: 'baseRate', repayment_method: 'repayment',
    loan_date: 'loan', maturity_date: 'maturity', grace_period_years: 'grace',
  },
  incomes: { type: 'type', name: 'name', amount: 'amount', is_fixed: 'fixed' },
  expenses: {
    name: 'name', amount: 'value', is_fixed: 'fixed', frequency: 'frequency',
  },
  goal_milestones: {
    phase: 'phase', age: 'age', category: 'category', content: 'content',
    amount: 'amount', note: 'note',
  },
  goal_periods: { phase: 'phase', from_age: 'fromAge', to_age: 'toAge' },
  goal_period_items: { name: 'name', amount: 'amount', is_fixed: 'type' },
}

const LIST_SECTIONS = new Set([
  'financial_assets', 'pension_assets', 'real_estate_assets',
  'liabilities', 'incomes', 'expenses',
])

function identityOf(item) {
  return {
    id: item?.id == null ? null : String(item.id),
    clientId: item?.clientItemId ?? item?.client_item_id ?? null,
  }
}

function matchesIdentity(item, change) {
  const identity = identityOf(item)
  if (change.item_id != null) return identity.id === String(change.item_id)
  if (change.client_item_id) return identity.clientId === change.client_item_id
  return false
}

function canonicalIdentityMatches(item, change, prefix = '') {
  const idKey = `${prefix}item_id`
  const clientIdKey = `${prefix}client_item_id`
  if (change[idKey] != null) return String(item?.id) === String(change[idKey])
  if (change[clientIdKey]) return item?.client_item_id === change[clientIdKey]
  return false
}

export function readCanonicalValue(currentForm, change) {
  if (!currentForm || typeof currentForm !== 'object') return { found: false, value: undefined }
  if (change.item_id == null && !change.client_item_id) {
    return {
      found: Object.hasOwn(currentForm, change.path),
      value: currentForm[change.path],
    }
  }

  const rows = change.section === 'goal_periods' || change.section === 'goal_milestones'
    ? currentForm[change.section]
    : currentForm.assets
  if (!Array.isArray(rows)) return { found: false, value: undefined }
  if (change.section === 'goal_periods' && change.path.startsWith('items[].')) {
    const parent = rows.find((row) => canonicalIdentityMatches(row, change, 'parent_'))
    const item = parent?.items?.find((row) => canonicalIdentityMatches(row, change))
    const itemPath = change.path.slice('items[].'.length)
    return {
      found: Boolean(item && Object.hasOwn(item, itemPath)),
      value: item?.[itemPath],
    }
  }
  const item = rows.find((row) => canonicalIdentityMatches(row, change))
  return {
    found: Boolean(item && Object.hasOwn(item, change.path)),
    value: item?.[change.path],
  }
}

function uiValue(field, value) {
  if (field === 'is_fixed' && typeof value === 'boolean') return value ? '고정' : '변동'
  if (field === 'monthly_income' && typeof value === 'number') return value / 10000
  return value
}

function mapObject(value, fields) {
  if (value == null || typeof value !== 'object' || Array.isArray(value)) return {}
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => fields[key])
      .map(([key, fieldValue]) => [fields[key], uiValue(key, fieldValue)]),
  )
}

function applySingle(stepId, form, change) {
  const field = SINGLE_FIELDS[stepId]?.[change.path]
  if (!field) throw new Error(`지원하지 않는 단일 필드입니다: ${change.path}`)
  if (change.operation === 'remove') form[field] = null
  else form[field] = uiValue(change.path, change.value)
}

function applyList(form, change) {
  const rows = form.assets ?? (form.assets = [])
  const fields = LIST_FIELDS[change.section]
  const field = fields?.[change.path]
  if (!fields || !field) throw new Error(`지원하지 않는 반복 필드입니다: ${change.section}.${change.path}`)

  if (change.operation === 'add') {
    rows.push({
      clientItemId: createSecureUuid(),
      ...mapObject(change.value, fields),
      ...(
        change.value == null || typeof change.value !== 'object'
          ? { [field]: uiValue(change.path, change.value) }
          : {}
      ),
    })
    return
  }

  const index = rows.findIndex((row) => matchesIdentity(row, change))
  if (index < 0) throw new Error('수정할 반복 항목을 찾을 수 없습니다.')
  if (change.operation === 'remove') rows.splice(index, 1)
  else rows[index][field] = uiValue(change.path, change.value)
}

function goalCollections(form, section) {
  const key = section === 'goal_milestones' ? 'milestones' : 'periods'
  return ['before', 'after'].flatMap((phase) => form[phase]?.[key] ?? [])
}

function applyGoal(form, change) {
  const phase = change.value?.phase ?? 'before'
  if (!form[phase]) throw new Error('지원하지 않는 재무목표 phase입니다.')

  if (change.operation === 'add') {
    if (change.section === 'goal_periods' && (change.path === 'items' || change.path.startsWith('items[].'))) {
      const parentChange = {
        item_id: change.parent_item_id,
        client_item_id: change.parent_client_item_id,
      }
      const parent = goalCollections(form, 'goal_periods').find((period) => matchesIdentity(period, parentChange))
      if (!parent) throw new Error('추가할 재무목표 항목의 부모 기간을 찾을 수 없습니다.')
      const itemField = change.path.startsWith('items[].')
        ? LIST_FIELDS.goal_period_items[change.path.slice('items[].'.length)]
        : null
      parent.items ??= []
      parent.items.push({
        clientItemId: createSecureUuid(),
        ...mapObject(change.value, LIST_FIELDS.goal_period_items),
        ...(
          itemField && (change.value == null || typeof change.value !== 'object')
            ? { [itemField]: uiValue(change.path, change.value) }
            : {}
        ),
      })
      return
    }

    const key = change.section === 'goal_milestones' ? 'milestones' : 'periods'
    const fields = LIST_FIELDS[change.section]
    const field = fields?.[change.path]
    form[phase][key] ??= []
    form[phase][key].push({
      clientItemId: createSecureUuid(),
      ...mapObject(change.value, fields),
      ...(field && (change.value == null || typeof change.value !== 'object')
        ? { [field]: uiValue(change.path, change.value) }
        : {}),
      ...(key === 'periods' ? { items: [] } : {}),
    })
    return
  }

  if (change.section === 'goal_periods' && change.path.startsWith('items[].')) {
    const field = LIST_FIELDS.goal_period_items[change.path.slice('items[].'.length)]
    for (const period of goalCollections(form, 'goal_periods')) {
      const items = period.items ?? []
      const index = items.findIndex((item) => matchesIdentity(item, change))
      if (index < 0) continue
      if (change.operation === 'remove') items.splice(index, 1)
      else items[index][field] = uiValue(change.path, change.value)
      return
    }
    throw new Error('수정할 재무목표 항목을 찾을 수 없습니다.')
  }

  const fields = LIST_FIELDS[change.section]
  for (const currentPhase of ['before', 'after']) {
    const key = change.section === 'goal_milestones' ? 'milestones' : 'periods'
    const rows = form[currentPhase]?.[key] ?? []
    const index = rows.findIndex((row) => matchesIdentity(row, change))
    if (index < 0) continue
    if (change.operation === 'remove') rows.splice(index, 1)
    else rows[index][fields[change.path]] = uiValue(change.path, change.value)
    return
  }
  throw new Error('수정할 재무목표를 찾을 수 없습니다.')
}

export function applyCanonicalChanges(stepId, form, changes) {
  for (const change of changes ?? []) {
    if (SINGLE_FIELDS[stepId]) applySingle(stepId, form, change)
    else if (LIST_SECTIONS.has(change.section)) applyList(form, change)
    else applyGoal(form, change)
  }
}

export function applyErrorReviewSuggestion(stepId, form, canonicalForm, item) {
  if (item?.suggested_value == null || !form) return 'unavailable'
  const change = errorReviewItemChange(item)
  const current = readCanonicalValue(canonicalForm, change)
  if (!current.found || JSON.stringify(current.value) !== JSON.stringify(item.current_value)) {
    return 'stale'
  }
  applyCanonicalChanges(stepId, form, [change])
  item.decision = 'applied'
  return 'applied'
}

export function cancelErrorReviewSuggestion(item) {
  item.decision = 'cancelled'
  return 'cancelled'
}

export async function continueSaveAfterOptionalReview(requestReview, persist) {
  let needsDecision
  try {
    needsDecision = await requestReview()
  } catch {
    needsDecision = false
  }
  if (needsDecision) return 'review_required'
  await persist()
  return 'saved'
}
