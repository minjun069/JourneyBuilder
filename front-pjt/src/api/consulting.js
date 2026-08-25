import api from '@/api'

// ── Session ──────────────────────────────────────────────────
export const getSessions   = ()              => api.get('/consultations/')
export const createSession = ()              => api.post('/consultations/')
export const getSession    = (sid)           => api.get(`/consultations/${sid}/`)
export const deleteSession = (sid)           => api.delete(`/consultations/${sid}/`)

// ── Messages ─────────────────────────────────────────────────
export const getMessages = (sid)           => api.get(`/consultations/${sid}/messages/`)
export const postMessage = (sid, body)     => api.post(`/consultations/${sid}/messages/`, body)
export const getMessage = (sid, rid)       => api.get(`/consultations/${sid}/ai-responses/${rid}/`)

// ── 1:1 Form data ────────────────────────────────────────────
export const getBasicInfo = (sid)       => api.get(`/consultations/${sid}/data/basic-info/`)
export const putBasicInfo = (sid, data) => api.put(`/consultations/${sid}/data/basic-info/`, data)

export const getFinancialMindset = (sid)       => api.get(`/consultations/${sid}/data/financial-mindset/`)
export const putFinancialMindset = (sid, data) => api.put(`/consultations/${sid}/data/financial-mindset/`, data)

export const getFinancialSatisfaction = (sid)       => api.get(`/consultations/${sid}/data/financial-satisfaction/`)
export const putFinancialSatisfaction = (sid, data) => api.put(`/consultations/${sid}/data/financial-satisfaction/`, data)

// ── 1:N Form data (Bulk Replace) ─────────────────────────────
export const getFinancialAssets = (sid)       => api.get(`/consultations/${sid}/data/financial-assets/`)
export const putFinancialAssets = (sid, data) => api.put(`/consultations/${sid}/data/financial-assets/`, data)

export const getPensionAssets = (sid)       => api.get(`/consultations/${sid}/data/pension-assets/`)
export const putPensionAssets = (sid, data) => api.put(`/consultations/${sid}/data/pension-assets/`, data)

export const getRealEstateAssets = (sid)       => api.get(`/consultations/${sid}/data/real-estate-assets/`)
export const putRealEstateAssets = (sid, data) => api.put(`/consultations/${sid}/data/real-estate-assets/`, data)

export const getLiabilities = (sid)       => api.get(`/consultations/${sid}/data/liabilities/`)
export const putLiabilities = (sid, data) => api.put(`/consultations/${sid}/data/liabilities/`, data)

export const getIncomes = (sid)       => api.get(`/consultations/${sid}/data/incomes/`)
export const putIncomes = (sid, data) => api.put(`/consultations/${sid}/data/incomes/`, data)

export const getExpenses = (sid)       => api.get(`/consultations/${sid}/data/expenses/`)
export const putExpenses = (sid, data) => api.put(`/consultations/${sid}/data/expenses/`, data)

export const getGoalPeriods = (sid)       => api.get(`/consultations/${sid}/data/goal-periods/`)
export const putGoalPeriods = (sid, data) => api.put(`/consultations/${sid}/data/goal-periods/`, data)

export const getGoalMilestones = (sid)       => api.get(`/consultations/${sid}/data/goal-milestones/`)
export const putGoalMilestones = (sid, data) => api.put(`/consultations/${sid}/data/goal-milestones/`, data)

// ── Form Data Mappers (프론트 form → 백엔드 payload) ─────────

function num(val) {
  const n = Number(String(val ?? '').replace(/[^0-9.-]/g, ''))
  return isNaN(n) || n === 0 ? null : n
}

function money(val, defaultUnit = 'man') {
  const smallUnitMultiplier = defaultUnit === 'eok' ? 100000000 : 10000

  if (val === '' || val == null) return null
  if (typeof val === 'number') {
    if (val === 0) return null
    return val > 0 && val < 10000 ? val * smallUnitMultiplier : val
  }

  const raw = String(val).replace(/,/g, '').replace(/\s+/g, '')
  if (!raw) return null
  if (!/[억만]/.test(raw)) {
    const n = num(raw)
    return n != null && n > 0 && n < 10000 ? n * smallUnitMultiplier : n
  }

  let total = 0
  let matched = false
  const unitPattern = /(-?\d+(?:\.\d+)?)(억|만)/g
  for (const match of raw.matchAll(unitPattern)) {
    matched = true
    const amount = Number(match[1])
    if (Number.isNaN(amount)) continue
    total += amount * (match[2] === '억' ? 100000000 : 10000)
  }

  if (!matched) return num(raw)
  const remainder = raw.replace(unitPattern, '')
  const extra = Number(remainder)
  if (remainder && !Number.isNaN(extra)) {
    total += raw.includes('억') && !raw.includes('만') && extra > 0 && extra < 10000
      ? extra * smallUnitMultiplier
      : extra
  }
  return total === 0 ? null : Math.round(total)
}

function str(val) {
  return val !== '' && val != null ? val : null
}

function manWon(val) {
  const n = num(val)
  return n == null ? null : n * 10000
}

export function mapBasicInfo(form) {
  return {
    age:            num(form.age),
    job:            str(form.job),
    marital_status: str(form.marital),
    dependents:     form.dependents,
    tendency:       form.tendency,
    interest:       str(form.interest),
    need:           str(form.need),
    monthly_income: manWon(form.earnings),
    retirement_age: num(form.retirementAge),
    end_age:        num(form.endAge),
  }
}

export function mapFinancialMindset(form) {
  return {
    values:            str(form.values),
    past_experience:   str(form.pastExperience),
    current_situation: str(form.currentSituation),
    current_challenge: str(form.currentChallenge),
    invest_philosophy: str(form.investPhilosophy),
    future_goals:      str(form.futureGoals),
  }
}

export function mapFinancialSatisfaction(form) {
  return {
    cashflow_score:    form.cashflow       ?? null,
    risk_score:        form.riskManagement ?? null,
    investment_score:  form.investment     ?? null,
    management_score:  form.management     ?? null,
    inheritance_score: form.inheritance    ?? null,
  }
}

export function mapFinancialAssets(form) {
  return (form.assets ?? []).map(a => ({
    asset_type:      str(a.assetType),
    name:            str(a.name),
    principal:       money(a.principal),
    current_value:   money(a.value),
    monthly_savings: money(a.monthly),
    purpose:         str(a.purpose),
  }))
}

export function mapPensionAssets(form) {
  return (form.assets ?? []).map(a => ({
    type:                 str(a.type),
    name:                 str(a.name),
    current_value:        money(a.value),
    monthly_savings:      money(a.monthly),
    expected_return_rate: num(a.rate),
    subscription_date:    str(a.date),
    payment_period_years: num(a.paymentPeriod),
    receive_method:       str(a.receiveMethod),
    pension_start_age:    num(a.age),
    pension_period:       str(a.pensionPeriod),
    expected_amount:      money(a.expectedAmount),
  }))
}

export function mapRealEstateAssets(form) {
  return (form.assets ?? []).map(a => ({
    type:                 str(a.type),
    name:                 str(a.name),
    current_value:        money(a.value, 'eok'),
    acquisition_price:    money(a.purchase, 'eok'),
    acquisition_date:     str(a.date),
    monthly_rental_income:money(a.monthly),
    rental_deposit:       money(a.deposit),
    secured_loan:         money(a.loan, 'eok'),
  }))
}

export function mapLiabilities(form) {
  return (form.assets ?? []).map(a => ({
    type:              str(a.type),
    name:              str(a.name),
    balance:           money(a.value, 'eok'),
    interest_rate:     num(a.rate),
    rate_type:         str(a.rateType),
    base_rate:         str(a.baseRate),
    repayment_method:  str(a.repayment),
    loan_date:         str(a.loan),
    maturity_date:     str(a.maturity),
    grace_period_years:num(a.grace),
  }))
}

export function mapIncomes(form) {
  return (form.assets ?? []).map(a => ({
    type:     str(a.type),
    name:     str(a.name),
    amount:   money(a.amount),
    is_fixed: a.fixed === '고정',
  }))
}

export function mapExpenses(form) {
  return (form.assets ?? []).map(a => ({
    name:      str(a.name),
    amount:    money(a.value),
    is_fixed:  a.fixed === '고정',
    frequency: str(a.frequency),
  }))
}

export function mapGoalPeriods(form) {
  const result = []
  for (const phase of ['before', 'after']) {
    for (const p of (form[phase]?.periods ?? [])) {
      result.push({
        phase,
        from_age: num(p.fromAge),
        to_age:   num(p.toAge),
        items: (p.items ?? []).map(item => ({
          name:     str(item.name),
          amount:   money(item.amount),
          is_fixed: item.type === '고정',
        })),
      })
    }
  }
  return result
}

export function mapGoalMilestones(form) {
  const result = []
  for (const phase of ['before', 'after']) {
    for (const m of (form[phase]?.milestones ?? [])) {
      if (!m.age) continue
      result.push({
        phase,
        age:      num(m.age),
        category: str(m.category),
        content:  str(m.content),
        amount:   money(m.amount, 'eok'),
        note:     str(m.note),
      })
    }
  }
  return result
}
