import policy from '../../../contract-fixtures/financial-calculation/v1/policy.json' with { type: 'json' }

export const REPORT_INDICATORS = [
  ['household_balance', '가계수지지표', '총지출 / 총소득', 'cashflow', 'household_balance_max', 'max'],
  ['total_savings_propensity', '총저축성향지표', '총저축 / 총소득', 'cashflow', 'total_savings_propensity_min', 'min'],
  ['financial_asset_ratio', '금융자산비중지표', '금융자산 / 총자산', 'assets', 'financial_asset_ratio_min', 'min'],
  ['debt_service_ratio', '총부채상환지표', '원리금 상환액 / 총소득 (DSR)', 'assets', 'debt_service_ratio_max', 'max'],
  ['retirement_savings', '노후대비저축지표', '노후대비저축 / 총저축', 'assets', 'retirement_savings_share_min', 'min'],
  ['emergency_reserve', '비상예비금지표', '유동성 자산 / 월 총지출', 'risk', 'emergency_reserve_min', 'min'],
  ['insurance_preparation', '보장성보험준비지표', '보장성 보험료 / 총소득', 'risk', 'insurance_preparation_range_min', 'range'],
]

function reference(id, age) {
  if (!Number.isFinite(age)) return undefined
  return policy.reference_values.find((row) => row.reference_id === id
    && age >= row.age_min && (row.age_max == null || age <= row.age_max))?.value
}

export function makeReportIndicator(definition, value, age) {
  const [metric, label, description, , referenceId, direction] = definition
  const threshold = reference(referenceId, age)
  const upper = direction === 'range' ? reference('insurance_preparation_range_max', age) : threshold
  const available = value != null && threshold != null && upper != null
  const meetsReference = available && (direction === 'min' ? value >= threshold
    : direction === 'max' ? value <= threshold : value >= threshold && value <= upper)
  const color = !available ? '#94a3b8' : meetsReference ? '#22a06b' : '#d97706'
  const status = !available ? '판정 불가' : meetsReference ? '참고 기준 충족' : '참고 기준 밖'
  const max = Math.max(metric === 'emergency_reserve' ? 12 : 100, value ?? 0)
  return {
    key: label, metric, label, description, value, min: 0, max,
    unit: metric === 'emergency_reserve' ? '개월' : '%',
    status, color,
    // Use the approved reference directly; do not invent warning/risk thresholds.
    zones: [{ from: 0, to: max, color, status }],
  }
}
