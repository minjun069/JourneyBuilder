const COMPLETION_TO_STEP = {
  basic_info: 'basic-info',
  financial_mindset: 'financial-mindset',
  financial_satisfaction: 'financial-satisfaction',
  financial_assets: 'financial-assets',
  pension_assets: 'pension-assets',
  real_estate_assets: 'real-estate',
  incomes: 'income',
  expenses: 'expenses',
  financial_goals: 'financial-goals',
}

const RESUME_SEQUENCE = Object.entries(COMPLETION_TO_STEP)

function fixedLabel(value) {
  if (value === true) return '고정'
  if (value === false) return '변동'
  return ''
}

function identity(item) {
  return item?.id == null ? {} : { id: item.id }
}

const LIST_MAPPERS = {
  'financial-assets': (item) => ({
    ...identity(item), assetType: item.asset_type ?? '', name: item.name ?? '',
    principal: item.principal ?? '', value: item.current_value ?? '',
    monthly: item.monthly_savings ?? '', purpose: item.purpose ?? '',
  }),
  'pension-assets': (item) => ({
    ...identity(item), type: item.type ?? '', name: item.name ?? '',
    value: item.current_value ?? '', monthly: item.monthly_savings ?? '',
    rate: item.expected_return_rate ?? '', date: item.subscription_date ?? '',
    paymentPeriod: item.payment_period_years ?? '', receiveMethod: item.receive_method ?? '',
    age: item.pension_start_age ?? '', pensionPeriod: item.pension_period ?? '',
    expectedAmount: item.expected_amount ?? '',
  }),
  'real-estate': (item) => ({
    ...identity(item), type: item.type ?? '', name: item.name ?? '',
    value: item.current_value ?? '', purchase: item.acquisition_price ?? '',
    date: item.acquisition_date ?? '', monthly: item.monthly_rental_income ?? '',
    deposit: item.rental_deposit ?? '', loan: item.secured_loan ?? '',
  }),
  liabilities: (item) => ({
    ...identity(item), type: item.type ?? '', name: item.name ?? '', value: item.balance ?? '',
    rate: item.interest_rate ?? '', rateType: item.rate_type ?? '', baseRate: item.base_rate ?? '',
    repayment: item.repayment_method ?? '', loan: item.loan_date ?? '',
    maturity: item.maturity_date ?? '', grace: item.grace_period_years ?? '',
  }),
  income: (item) => ({
    ...identity(item), type: item.type ?? '', name: item.name ?? '',
    amount: item.amount ?? '', fixed: fixedLabel(item.is_fixed),
  }),
  expenses: (item) => ({
    ...identity(item), name: item.name ?? '', value: item.amount ?? '',
    fixed: fixedLabel(item.is_fixed), frequency: item.frequency ?? '',
  }),
}

function emptyGoals() {
  return {
    before: { periods: [], milestones: [] },
    after: { periods: [], milestones: [] },
  }
}

function mapGoals(payload) {
  const result = emptyGoals()
  for (const period of payload?.periods ?? []) {
    const phase = period.phase === 'after' ? 'after' : 'before'
    result[phase].periods.push({
      ...identity(period),
      fromAge: period.from_age ?? '',
      toAge: period.to_age ?? '',
      items: (period.items ?? []).map((item) => ({
        ...identity(item), name: item.name ?? '', amount: item.amount ?? '',
        type: fixedLabel(item.is_fixed),
      })),
    })
  }
  for (const milestone of payload?.milestones ?? []) {
    const phase = milestone.phase === 'after' ? 'after' : 'before'
    result[phase].milestones.push({
      ...identity(milestone), age: milestone.age ?? '', category: milestone.category ?? '',
      content: milestone.content ?? '', amount: milestone.amount ?? '', note: milestone.note ?? '',
      committed: true,
    })
  }
  return result
}

export function mapStoredForm(stepId, payload) {
  if (stepId === 'basic-info') {
    return {
      age: payload?.age ?? '', job: payload?.job ?? '', marital: payload?.marital_status ?? '',
      dependents: payload?.dependents ?? { children: [], parents: [], others: [] },
      tendency: payload?.tendency ?? [], interest: payload?.interest ?? '', need: payload?.need ?? '',
      earnings: typeof payload?.monthly_income === 'number' ? payload.monthly_income / 10000 : 0,
      retirementAge: payload?.retirement_age ?? '', endAge: payload?.end_age ?? '',
    }
  }
  if (stepId === 'financial-mindset') {
    return {
      values: payload?.values ?? '', pastExperience: payload?.past_experience ?? '',
      currentSituation: payload?.current_situation ?? '', currentChallenge: payload?.current_challenge ?? '',
      investPhilosophy: payload?.invest_philosophy ?? '', futureGoals: payload?.future_goals ?? '',
    }
  }
  if (stepId === 'financial-satisfaction') {
    return {
      cashflow: payload?.cashflow_score ?? null, riskManagement: payload?.risk_score ?? null,
      investment: payload?.investment_score ?? null, management: payload?.management_score ?? null,
      inheritance: payload?.inheritance_score ?? null,
    }
  }
  if (stepId === 'financial-goals') return mapGoals(payload)
  const mapper = LIST_MAPPERS[stepId]
  if (mapper) return { assets: (Array.isArray(payload) ? payload : []).map(mapper) }
  return null
}

export function completedStepsForSession(session) {
  const completion = session?.section_completion ?? {}
  const result = new Set(['intro'])
  for (const [section, stepId] of Object.entries(COMPLETION_TO_STEP)) {
    if (completion[section]) result.add(stepId)
  }
  if (Object.values(completion).some(Boolean)) result.add('collection')
  if (completion.financial_assets || completion.pension_assets || completion.real_estate_assets
    || completion.incomes || completion.expenses || completion.financial_goals) {
    result.add('financial-info')
  }
  if (completion.incomes || completion.expenses || completion.financial_goals) result.add('liabilities')
  return result
}

export function resolveResumeStep(session) {
  if (session?.status === 'onboarding' && Number(session?.progress_pct ?? 0) === 0) return 'intro'
  if (session?.status === 'completed' || session?.is_ready_for_reports) return 'balance-sheet'
  const completion = session?.section_completion ?? {}
  for (const [section, stepId] of RESUME_SEQUENCE) {
    if (!completion[section]) return stepId
  }
  return 'balance-sheet'
}

export function replaceFormState(target, source) {
  if (!target || !source) return
  for (const key of Object.keys(target)) delete target[key]
  Object.assign(target, structuredClone(source))
}
