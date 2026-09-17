import { consultingApi, refreshAccessToken } from '@/api'
import { toCanonicalMoney as money } from '@/utils/canonicalMoney'
import { readSSEStream, validateV2SSEEvents } from '@/api/sse'
import { mapMonthlyIncome, wrapCurrentListForm } from './consultingMapping.js'
import { createSecureUuid } from '@/utils/secureUuid'
import {
  AI_CONTRACT_VERSION,
  createIdempotencyKey,
  deriveV2BaseURL,
} from '@/api/aiLifecycle'

// ── Session ──────────────────────────────────────────────────
export const getSessions = () => consultingApi.get('/consultations/')
export const createSession = () => consultingApi.post('/consultations/')
export const getSession = (sid) => consultingApi.get(`/consultations/${sid}/`)
export const deleteSession = (sid) => consultingApi.delete(`/consultations/${sid}/`)

// ── Messages ─────────────────────────────────────────────────
export const getMessages = (sid) => consultingApi.get(`/consultations/${sid}/messages/`)
const V2_BASE_URL = deriveV2BaseURL()

export async function* streamMessagesV2(
  sid,
  body,
  { idempotencyKey = createIdempotencyKey(), signal } = {},
) {
  const token = localStorage.getItem('access_token')
  const headers = {
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
    'Idempotency-Key': idempotencyKey,
    'X-AI-Progress-Events': 'v1',
  }
  if (token) headers.Authorization = `Bearer ${token}`

  const requestURL = `${V2_BASE_URL}/consultations/${sid}/messages/stream/`
  const requestInit = {
    method: 'POST',
    headers,
    body: JSON.stringify({ ...body, contract_version: AI_CONTRACT_VERSION }),
    signal,
  }
  let response = await fetch(requestURL, requestInit)
  if (response.status === 401 && !signal?.aborted) {
    headers.Authorization = `Bearer ${await refreshAccessToken()}`
    response = await fetch(requestURL, requestInit)
  }
  if (!response.ok) {
    let publicError = null
    try {
      const payload = await response.json()
      if (payload?.success === false && typeof payload.error === 'object') {
        publicError = payload.error
      }
    } catch {
      // JSON 오류 응답이 아니면 안전한 기본 메시지를 사용한다.
    }
    const error = new Error(publicError?.message ?? 'AI 스트림 요청에 실패했습니다.')
    error.code = publicError?.code ?? 'AI_STREAM_REQUEST_FAILED'
    error.retryable = publicError?.retryable ?? true
    error.status = response.status
    throw error
  }

  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('text/event-stream')) {
    throw new Error('AI 스트림 응답 형식이 올바르지 않습니다.')
  }
  yield* validateV2SSEEvents(readSSEStream(response.body))
}

// ── 1:1 Form data ────────────────────────────────────────────
export const getBasicInfo = (sid)       => consultingApi.get(`/consultations/${sid}/data/basic-info/`)
export const putBasicInfo = (sid, data) => consultingApi.put(`/consultations/${sid}/data/basic-info/`, data)

export const getFinancialMindset = (sid)       => consultingApi.get(`/consultations/${sid}/data/financial-mindset/`)
export const putFinancialMindset = (sid, data) => consultingApi.put(`/consultations/${sid}/data/financial-mindset/`, data)

export const getFinancialSatisfaction = (sid)       => consultingApi.get(`/consultations/${sid}/data/financial-satisfaction/`)
export const putFinancialSatisfaction = (sid, data) => consultingApi.put(`/consultations/${sid}/data/financial-satisfaction/`, data)

// ── 1:N Form data (Bulk Replace) ─────────────────────────────
export const getFinancialAssets = (sid)       => consultingApi.get(`/consultations/${sid}/data/financial-assets/`)
export const putFinancialAssets = (sid, data) => consultingApi.put(`/consultations/${sid}/data/financial-assets/`, data)
export const confirmEmptyFinancialAssets = (sid) => (
  consultingApi.put(`/consultations/${sid}/data/financial-assets/confirm-empty/`)
)

export const getPensionAssets = (sid)       => consultingApi.get(`/consultations/${sid}/data/pension-assets/`)
export const putPensionAssets = (sid, data) => consultingApi.put(`/consultations/${sid}/data/pension-assets/`, data)

export const getRealEstateAssets = (sid)       => consultingApi.get(`/consultations/${sid}/data/real-estate-assets/`)
export const putRealEstateAssets = (sid, data) => consultingApi.put(`/consultations/${sid}/data/real-estate-assets/`, data)

export const getLiabilities = (sid)       => consultingApi.get(`/consultations/${sid}/data/liabilities/`)
export const putLiabilities = (sid, data) => consultingApi.put(`/consultations/${sid}/data/liabilities/`, data)

export const getIncomes = (sid)       => consultingApi.get(`/consultations/${sid}/data/incomes/`)
export const putIncomes = (sid, data) => consultingApi.put(`/consultations/${sid}/data/incomes/`, data)

export const getExpenses = (sid)       => consultingApi.get(`/consultations/${sid}/data/expenses/`)
export const putExpenses = (sid, data) => consultingApi.put(`/consultations/${sid}/data/expenses/`, data)

export const getGoalPeriods = (sid)       => consultingApi.get(`/consultations/${sid}/data/goal-periods/`)
export const putGoalPeriods = (sid, data) => consultingApi.put(`/consultations/${sid}/data/goal-periods/`, data)

export const getGoalMilestones = (sid)       => consultingApi.get(`/consultations/${sid}/data/goal-milestones/`)
export const putGoalMilestones = (sid, data) => consultingApi.put(`/consultations/${sid}/data/goal-milestones/`, data)

// ── Form Data Mappers (프론트 form → 백엔드 payload) ─────────

function num(val) {
  if (val === '' || val == null) return null
  const n = Number(String(val ?? '').replace(/[^0-9.-]/g, ''))
  return isNaN(n) ? null : n
}

export function ensureClientItemId(item) {
  if (!item.clientItemId) item.clientItemId = createSecureUuid()
  return item.clientItemId
}

function persistedIdentity(item) {
  return item.id == null ? {} : { id: item.id }
}

function str(val) {
  return val !== '' && val != null ? val : null
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
    monthly_income: mapMonthlyIncome(form.earnings),
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
    ...persistedIdentity(a),
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
    ...persistedIdentity(a),
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
    ...persistedIdentity(a),
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
    ...persistedIdentity(a),
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
    ...persistedIdentity(a),
    type:     str(a.type),
    name:     str(a.name),
    amount:   money(a.amount),
    is_fixed: a.fixed ? a.fixed === '고정' : null,
  }))
}

export function mapExpenses(form) {
  return (form.assets ?? []).map(a => ({
    ...persistedIdentity(a),
    name:      str(a.name),
    amount:    money(a.value),
    is_fixed:  a.fixed ? a.fixed === '고정' : null,
    frequency: str(a.frequency),
  }))
}

export function mapGoalPeriods(form) {
  const result = []
  for (const phase of ['before', 'after']) {
    for (const p of (form[phase]?.periods ?? [])) {
      result.push({
        ...persistedIdentity(p),
        phase,
        from_age: num(p.fromAge),
        to_age:   num(p.toAge),
        items: (p.items ?? []).map(item => ({
          ...persistedIdentity(item),
          name:     str(item.name),
          amount:   money(item.amount),
          is_fixed: item.type ? item.type === '고정' : null,
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
        ...persistedIdentity(m),
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

const CURRENT_FORM_MAPPERS = {
  'basic-info': mapBasicInfo,
  'financial-mindset': mapFinancialMindset,
  'financial-satisfaction': mapFinancialSatisfaction,
  'financial-assets': mapFinancialAssets,
  'pension-assets': mapPensionAssets,
  'real-estate': mapRealEstateAssets,
  liabilities: mapLiabilities,
  income: mapIncomes,
  expenses: mapExpenses,
}

export function mapCurrentForm(stepId, form) {
  if (stepId === 'financial-goals') {
    const goalPeriods = []
    const goalMilestones = []
    for (const phase of ['before', 'after']) {
      const sourcePeriods = form[phase]?.periods ?? []
      const mappedPeriods = mapGoalPeriods({ [phase]: { periods: sourcePeriods } })
      mappedPeriods.forEach((period, index) => {
        const source = sourcePeriods[index]
        goalPeriods.push({
          ...period,
          client_item_id: ensureClientItemId(source),
          items: period.items.map((item, itemIndex) => ({
            ...item,
            client_item_id: ensureClientItemId(source.items[itemIndex]),
          })),
        })
      })
      const sourceMilestones = (form[phase]?.milestones ?? []).filter((item) => item.age)
      const mappedMilestones = mapGoalMilestones({ [phase]: { milestones: sourceMilestones } })
      mappedMilestones.forEach((milestone, index) => {
        goalMilestones.push({
          ...milestone,
          client_item_id: ensureClientItemId(sourceMilestones[index]),
        })
      })
    }
    return { goal_periods: goalPeriods, goal_milestones: goalMilestones }
  }
  const mapper = CURRENT_FORM_MAPPERS[stepId]
  const mapped = mapper ? mapper(form) : {}
  if (!Array.isArray(mapped)) return mapped
  return wrapCurrentListForm(mapped.map((item, index) => ({
    ...item,
    client_item_id: ensureClientItemId(form.assets[index]),
  })))
}
