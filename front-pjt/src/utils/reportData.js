import { makeIndicator } from '../components/charts/financialIndicatorSchema.js'
import { makeReportIndicator, REPORT_INDICATORS } from './reportIndicators.js'

export class ReportDataError extends Error {
  constructor(path) {
    super(`보고서 숫자 데이터가 올바르지 않습니다: ${path}`)
    this.name = 'ReportDataError'
    this.code = 'INVALID_REPORT_DATA'
    this.path = path
  }
}

const ASSET_KEY_MAP = {
  cash_savings: 'cash',
  tangible_assets: 'tangible',
  investment_assets: 'invest',
  real_estate: 'estate',
  pension_assets: 'pension',
}

const LIABILITY_KEY_MAP = {
  short_term: 'short',
  long_term: 'long',
}

const CASH_INFLOW_KEY_MAP = {
  earned_income: '근로소득',
  business_income: '사업소득',
  capital_gain_income: '기타',
  financial_income: '기타',
  rental_income: '기타',
  pension_income: '연금소득',
  other_income: '기타',
}

const CASH_OUTFLOW_KEY_MAP = {
  savings: 'fixed',
  investments: 'fixed',
  debt_repayment: 'fixed',
  fixed_consumption: 'fixed',
  variable_consumption: 'variable',
}

const ASSET_COLORS = {
  cash: '#5e5ce6',
  tangible: '#ff9f40',
  invest: '#4bc0c0',
  estate: '#ff6384',
  pension: '#36a2eb',
}

export function normalizePresentationData(data) {
  if (!data) return null

  return {
    ...data,
    asset_statement: normalizeAssetStatement(data.asset_statement),
    cash_flow: normalizeCashFlow(data.cash_flow),
    'financial-indicators': normalizeFinancialIndicators(data['financial-indicators'], data['retirement-readiness']?.current_age),
    'retirement-fund': normalizeRetirementFund(data['retirement-fund']),
    'retirement-readiness': normalizeRetirementReadiness(data['retirement-readiness']),
    'pension-schedule': normalizePensionSchedule(data['pension-schedule']),
    'required-savings': normalizeRequiredSavings(data['required-savings']),
  }
}

export function buildDashboardOverview(data, fallback) {
  const reports = normalizePresentationData(data)
  if (!reports) return fallback

  const bs = reports.asset_statement
  const cf = reports.cash_flow
  const fi = reports['financial-indicators']

  return {
    ...fallback,
    retirementScore: fallback?.retirementScore ?? { score: 0, grade: '-' },
    aiComment: buildAiComment(bs, cf, fallback?.aiComment),
    assets: {
      total: bs?.total_assets ?? fallback?.assets?.total ?? 0,
      debt: bs?.total_liabilities ?? fallback?.assets?.debt ?? 0,
      net: bs?.net_worth ?? fallback?.assets?.net ?? 0,
      breakdown: buildAssetBreakdown(bs, fallback?.assets?.breakdown),
    },
    cashflow: {
      inflow: cf?.total_income ?? fallback?.cashflow?.inflow ?? 0,
      outflow: cf?.total_expense ?? fallback?.cashflow?.outflow ?? 0,
    },
    financialIndicators: buildDashboardIndicators(fi, fallback?.financialIndicators),
  }
}

export function buildDashboardCashflow(data, fallbackPeriods, fallbackHistory, selectedPeriod) {
  const cf = normalizeCashFlow(data?.cash_flow)
  const selectedIndex = fallbackPeriods.indexOf(selectedPeriod)
  const safeIndex = selectedIndex >= 0 ? selectedIndex : fallbackPeriods.length - 1
  let inflow
  let outflow
  let history

  if (cf) {
    inflow = cf.total_income
    outflow = cf.total_expense
    history = [{ period: selectedPeriod, inflow, outflow }]
  } else {
    const fallbackCurrent = fallbackHistory[safeIndex]
    if (!fallbackCurrent) throw new ReportDataError('dashboard.cashflow.fallback_period')

    inflow = reportNumber(fallbackCurrent.inflow, 'dashboard.cashflow.fallback.inflow')
    outflow = reportNumber(fallbackCurrent.outflow, 'dashboard.cashflow.fallback.outflow')
    history = fallbackHistory
      .slice(Math.max(0, safeIndex - 11), safeIndex + 1)
      .map((entry, index) => ({
        ...entry,
        inflow: reportNumber(entry.inflow, `dashboard.cashflow.fallback_history[${index}].inflow`),
        outflow: reportNumber(entry.outflow, `dashboard.cashflow.fallback_history[${index}].outflow`),
      }))
  }

  return {
    dataSource: cf ? 'report' : 'fallback',
    summary: {
      inflow,
      outflow,
      net: inflow - outflow,
    },
    history,
  }
}

function normalizeAssetStatement(bs) {
  if (!bs) return null

  const assetGroups = bs.asset_groups
    ? normalizeExistingGroups(bs.asset_groups, 'asset_statement.asset_groups')
    : categoriesToGroups(
      bs.asset_categories,
      ASSET_KEY_MAP,
      'asset_statement.asset_categories',
      item => ({
        label: item.name || item.asset_type || item.type || '자산',
        value: reportNumber(
          item.current_value ?? item.value ?? item.principal,
          'asset_statement.asset_categories.items.value',
        ),
      }),
    )

  const liabilityGroups = bs.liability_groups
    ? normalizeExistingGroups(bs.liability_groups, 'asset_statement.liability_groups')
    : categoriesToGroups(
      bs.liability_categories,
      LIABILITY_KEY_MAP,
      'asset_statement.liability_categories',
      item => ({
        label: item.name || item.type || '부채',
        value: reportNumber(
          item.balance ?? item.value,
          'asset_statement.liability_categories.items.value',
        ),
      }),
    )

  return {
    ...bs,
    total_assets: reportNumber(bs.total_assets, 'asset_statement.total_assets'),
    total_liabilities: reportNumber(
      bs.total_liabilities ?? bs.total_debts,
      'asset_statement.total_liabilities',
    ),
    net_worth: reportNumber(bs.net_worth, 'asset_statement.net_worth'),
    debt_ratio: reportNumber(
      bs.debt_ratio ?? bs.debt_to_asset_ratio,
      'asset_statement.debt_ratio',
    ),
    asset_groups: assetGroups,
    liability_groups: liabilityGroups,
  }
}

function normalizeCashFlow(cf) {
  if (!cf) return null

  const incomeGroups = cf.income_groups
    ? normalizeExistingGroups(cf.income_groups, 'cash_flow.income_groups')
    : mergeCategoryGroups(
      cf.inflow_categories,
      CASH_INFLOW_KEY_MAP,
      'cash_flow.inflow_categories',
      item => ({
        label: item.type || item.name || '수입',
        value: reportNumber(item.amount ?? item.value, 'cash_flow.inflow_categories.items.value'),
      }),
    )

  const expenseGroups = cf.expense_groups
    ? normalizeExistingGroups(cf.expense_groups, 'cash_flow.expense_groups')
    : mergeCategoryGroups(
      cf.outflow_categories,
      CASH_OUTFLOW_KEY_MAP,
      'cash_flow.outflow_categories',
      item => ({
        label: item.name || item.type || '지출',
        value: reportNumber(item.amount ?? item.value, 'cash_flow.outflow_categories.items.value'),
      }),
    )

  const totalIncome = reportNumber(
    cf.total_income ?? cf.total_cash_inflow ?? cf.monthly_income,
    'cash_flow.total_income',
  )
  const totalExpense = reportNumber(
    cf.total_expense ?? cf.total_cash_outflow ?? cf.monthly_expense,
    'cash_flow.total_expense',
  )
  const surplus = reportNumber(
    cf.surplus ?? cf.net_cash_flow ?? cf.monthly_surplus ?? (totalIncome - totalExpense),
    'cash_flow.surplus',
  )

  return {
    ...cf,
    total_income: totalIncome,
    total_expense: totalExpense,
    surplus,
    surplus_ratio: reportNumber(
      cf.surplus_ratio ?? ratio(surplus, totalIncome),
      'cash_flow.surplus_ratio',
    ),
    income_groups: incomeGroups,
    expense_groups: expenseGroups,
  }
}

function normalizeFinancialIndicators(fi, currentAge) {
  if (fi && Object.hasOwn(fi, 'household_balance')) {
    const rawAge = fi.age ?? currentAge
    const age = rawAge == null ? undefined : reportNumber(rawAge, 'financial-indicators.age')
    const groups = { cashflow: [], assets: [], risk: [] }
    for (const definition of REPORT_INDICATORS) {
      const field = definition[0]
      // A null denominator result is explicitly undefined, not a zero score.
      const value = fi[field] === null ? null : reportNumber(fi[field], `financial-indicators.${field}`)
      groups[definition[3]].push(makeReportIndicator(definition, value, age))
    }
    return { ...fi, age, groups }
  }
  if (!fi?.groups) return fi ?? null

  return {
    ...fi,
    groups: Object.fromEntries(
      Object.entries(fi.groups).map(([groupKey, items]) => [
        groupKey,
        items.map(item => makeIndicator(
          item.key ?? item.label,
          reportNumber(item.value, `financial-indicators.groups.${groupKey}.value`),
          fi.age,
        )),
      ]),
    ),
  }
}

function categoriesToGroups(categories, keyMap, path, itemMapper) {
  return Object.entries(categories ?? {}).map(([key, category]) => ({
    key: keyMap[key] ?? key,
    label: category.label,
    total: reportNumber(category.total, `${path}.${key}.total`),
    items: (category.items ?? []).map(itemMapper),
  }))
}

function mergeCategoryGroups(categories, keyMap, path, itemMapper) {
  const grouped = new Map()

  for (const [key, category] of Object.entries(categories ?? {})) {
    const groupKey = keyMap[key] ?? key
    const existing = grouped.get(groupKey) ?? {
      key: groupKey,
      label: groupKey === 'fixed' ? '고정 지출' : groupKey === 'variable' ? '변동 지출' : category.label,
      total: 0,
      items: [],
    }

    existing.total += reportNumber(category.total, `${path}.${key}.total`)
    existing.items.push(...(category.items ?? []).map(itemMapper))
    grouped.set(groupKey, existing)
  }

  return [...grouped.values()]
}

function normalizeExistingGroups(groups, path) {
  return groups.map((group, groupIndex) => ({
    ...group,
    total: reportNumber(group.total, `${path}[${groupIndex}].total`),
    items: (group.items ?? []).map((item, itemIndex) => ({
      ...item,
      value: reportNumber(item.value, `${path}[${groupIndex}].items[${itemIndex}].value`),
    })),
  }))
}

function buildAssetBreakdown(bs, fallback = []) {
  const groups = bs?.asset_groups ?? []
  if (!groups.length) return fallback

  return groups.map(group => ({
    category: group.label,
    amount: reportNumber(group.total, `asset_statement.asset_groups.${group.key}.total`),
    color: ASSET_COLORS[group.key] ?? '#9ca3af',
  }))
}

function buildDashboardIndicators(fi, fallback) {
  if (!fi?.groups) return fallback

  return {
    cashflow: fi.groups.cashflow ?? [],
    assets: fi.groups.assets ?? [],
    risk: fi.groups.risk ?? [],
  }
}

function buildAiComment(bs, cf, fallback) {
  if (!bs && !cf) return fallback ?? ''

  const debtRatio = bs?.debt_ratio ?? 0
  const surplusRatio = cf?.surplus_ratio ?? 0

  return `AI 상담 결과 기준 순자산은 ${formatEok(bs?.net_worth ?? 0)}이며, 부채비율은 ${debtRatio}%입니다. 월 잉여현금흐름은 ${formatEok(cf?.surplus ?? 0)}(잉여율 ${surplusRatio}%) 수준입니다.`
}

function numericFields(value, fields, path) {
  const result = { ...value }
  for (const field of fields) result[field] = reportNumber(value?.[field], `${path}.${field}`)
  return result
}

function reportRows(value, path, normalize) {
  if (!Array.isArray(value)) throw new ReportDataError(path)
  return value.map((row, index) => normalize(row, `${path}[${index}]`))
}

function normalizeRetirementFund(report) {
  if (!report) return report
  const path = 'retirement-fund'
  return {
    ...numericFields(report, ['prep_period_years', 'retirement_period_years', 'total_required_fund', 'total_prepared_fund', 'shortfall'], path),
    assumptions: numericFields(report.assumptions, ['inflation_rate'], `${path}.assumptions`),
    phase_details: reportRows(report.phase_details, `${path}.phase_details`, (row, rowPath) =>
      numericFields(row, ['duration_years', 'monthly_required', 'annual_required', 'total_required'], rowPath)),
  }
}

function normalizeRetirementReadiness(report) {
  if (!report) return report
  const path = 'retirement-readiness'
  return {
    ...numericFields(report, ['current_age', 'retirement_age', 'total_retirement_assets'], path),
    retirement_composition: numericFields(report.retirement_composition,
      ['financial', 'real_estate', 'pension_accumulation'], `${path}.retirement_composition`),
    retirement_income_sources: numericFields(report.retirement_income_sources,
      ['public_pension_monthly', 'severance_pay_estimated', 'rental_income_annual'], `${path}.retirement_income_sources`),
  }
}

function normalizePensionSchedule(report) {
  if (!report) return report
  const path = 'pension-schedule'
  return {
    ...report,
    results: numericFields(report.results, ['pension_start_age', 'final_monthly_est', 'total_months'], `${path}.results`),
    yearly_schedule: reportRows(report.yearly_schedule, `${path}.yearly_schedule`, (row, rowPath) =>
      numericFields(row, ['age', 'year', 'monthly_amount', 'annual_amount'], rowPath)),
  }
}

function normalizeRequiredSavings(report) {
  if (!report) return report
  return numericFields(report, ['current_monthly_savings', 'target_monthly_savings', 'additional_required_savings'], 'required-savings')
}

function reportNumber(value, path) {
  if (
    value == null
    || !['number', 'string'].includes(typeof value)
    || (typeof value === 'string' && value.trim() === '')
  ) {
    throw new ReportDataError(path)
  }

  const number = Number(value)
  if (!Number.isFinite(number)) throw new ReportDataError(path)
  return number
}

function ratio(value, total) {
  return total ? Number(((value / total) * 100).toFixed(1)) : 0
}

function formatEok(value) {
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (abs >= 100000000) return `${sign}${(abs / 100000000).toFixed(1)}억원`
  return `${sign}${Math.round(abs / 10000).toLocaleString('ko-KR')}만원`
}
