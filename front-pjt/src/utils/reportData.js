import { makeIndicator } from '@/components/charts/financialIndicatorSchema'

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
    'financial-indicators': normalizeFinancialIndicators(data['financial-indicators']),
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
  const fallbackCurrent = fallbackHistory[safeIndex] || { inflow: 0, outflow: 0 }

  const inflow = cf?.total_income ?? Number(fallbackCurrent.inflow) ?? 0
  const outflow = cf?.total_expense ?? Number(fallbackCurrent.outflow) ?? 0

  const history = cf
    ? [{ period: selectedPeriod, inflow, outflow }]
    : fallbackHistory.slice(Math.max(0, safeIndex - 11), safeIndex + 1)

  return {
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

  const assetGroups = bs.asset_groups ?? categoriesToGroups(
    bs.asset_categories,
    ASSET_KEY_MAP,
    item => ({
      label: item.name || item.asset_type || item.type || '자산',
      value: numberValue(item.current_value ?? item.value ?? item.principal),
    }),
  )

  const liabilityGroups = bs.liability_groups ?? categoriesToGroups(
    bs.liability_categories,
    LIABILITY_KEY_MAP,
    item => ({
      label: item.name || item.type || '부채',
      value: numberValue(item.balance ?? item.value),
    }),
  )

  return {
    ...bs,
    debt_ratio: numberValue(bs.debt_ratio ?? bs.debt_to_asset_ratio),
    asset_groups: assetGroups,
    liability_groups: liabilityGroups,
  }
}

function normalizeCashFlow(cf) {
  if (!cf) return null

  const incomeGroups = cf.income_groups ?? mergeCategoryGroups(
    cf.inflow_categories,
    CASH_INFLOW_KEY_MAP,
    item => ({
      label: item.type || item.name || '수입',
      value: numberValue(item.amount ?? item.value),
    }),
  )

  const expenseGroups = cf.expense_groups ?? mergeCategoryGroups(
    cf.outflow_categories,
    CASH_OUTFLOW_KEY_MAP,
    item => ({
      label: item.name || item.type || '지출',
      value: numberValue(item.amount ?? item.value),
    }),
  )

  const totalIncome = numberValue(cf.total_income ?? cf.total_cash_inflow ?? cf.monthly_income)
  const totalExpense = numberValue(cf.total_expense ?? cf.total_cash_outflow ?? cf.monthly_expense)
  const surplus = numberValue(cf.surplus ?? cf.net_cash_flow ?? cf.monthly_surplus ?? (totalIncome - totalExpense))

  return {
    ...cf,
    total_income: totalIncome,
    total_expense: totalExpense,
    surplus,
    surplus_ratio: numberValue(cf.surplus_ratio ?? ratio(surplus, totalIncome)),
    income_groups: incomeGroups,
    expense_groups: expenseGroups,
  }
}

function normalizeFinancialIndicators(fi) {
  if (!fi?.groups) return fi ?? null

  return {
    ...fi,
    groups: Object.fromEntries(
      Object.entries(fi.groups).map(([groupKey, items]) => [
        groupKey,
        items.map(item => makeIndicator(item.key ?? item.label, numberValue(item.value), fi.age)),
      ]),
    ),
  }
}

function categoriesToGroups(categories, keyMap, itemMapper) {
  return Object.entries(categories ?? {}).map(([key, category]) => ({
    key: keyMap[key] ?? key,
    label: category.label,
    total: numberValue(category.total),
    items: (category.items ?? []).map(itemMapper),
  }))
}

function mergeCategoryGroups(categories, keyMap, itemMapper) {
  const grouped = new Map()

  for (const [key, category] of Object.entries(categories ?? {})) {
    const groupKey = keyMap[key] ?? key
    const existing = grouped.get(groupKey) ?? {
      key: groupKey,
      label: groupKey === 'fixed' ? '고정 지출' : groupKey === 'variable' ? '변동 지출' : category.label,
      total: 0,
      items: [],
    }

    existing.total += numberValue(category.total)
    existing.items.push(...(category.items ?? []).map(itemMapper))
    grouped.set(groupKey, existing)
  }

  return [...grouped.values()]
}

function buildAssetBreakdown(bs, fallback = []) {
  const groups = bs?.asset_groups ?? []
  if (!groups.length) return fallback

  return groups.map(group => ({
    category: group.label,
    amount: numberValue(group.total),
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

function numberValue(value) {
  return Number(value ?? 0)
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
