export class SimulationDataError extends Error {
  constructor(path, message = '시뮬레이션 응답 형식이 올바르지 않습니다.') {
    super(message)
    this.name = 'SimulationDataError'
    this.code = 'INVALID_SIMULATION_DATA'
    this.path = path
    this.retryable = false
  }
}

function objectAt(value, path) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new SimulationDataError(path)
  }
  return value
}

function finiteAt(value, path) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new SimulationDataError(path)
  }
  return value
}

function integerAt(value, path) {
  const number = finiteAt(value, path)
  if (!Number.isInteger(number)) throw new SimulationDataError(path)
  return number
}

function validatePercentiles(value, path) {
  const percentiles = objectAt(value, path)
  for (const key of ['p10', 'p50', 'p90']) finiteAt(percentiles[key], `${path}.${key}`)
}

function validateResult(value, path) {
  const result = objectAt(value, path)
  const probability = finiteAt(result.success_probability, `${path}.success_probability`)
  if (probability < 0 || probability > 1) throw new SimulationDataError(`${path}.success_probability`)
  validatePercentiles(result.final_balance_percentiles, `${path}.final_balance_percentiles`)

  if (!Array.isArray(result.asset_percentiles)) {
    throw new SimulationDataError(`${path}.asset_percentiles`)
  }
  for (const [index, point] of result.asset_percentiles.entries()) {
    const row = objectAt(point, `${path}.asset_percentiles[${index}]`)
    integerAt(row.age, `${path}.asset_percentiles[${index}].age`)
    validatePercentiles(row.balance, `${path}.asset_percentiles[${index}].balance`)
  }
}

function validateProfile(value, path) {
  const profile = objectAt(value, path)
  for (const field of ['current_age', 'retirement_age', 'end_age', 'monthly_savings']) {
    integerAt(profile[field], `${path}.${field}`)
  }
  if (!Array.isArray(profile.retirement_expenses) || profile.retirement_expenses.length === 0) {
    throw new SimulationDataError(`${path}.retirement_expenses`)
  }
  for (const [index, expense] of profile.retirement_expenses.entries()) {
    const rowPath = `${path}.retirement_expenses[${index}]`
    const row = objectAt(expense, rowPath)
    const fromAge = integerAt(row.from_age, `${rowPath}.from_age`)
    const toAge = integerAt(row.to_age, `${rowPath}.to_age`)
    const monthlyAmount = integerAt(row.monthly_amount, `${rowPath}.monthly_amount`)
    if (fromAge < 1 || toAge < fromAge || monthlyAmount < 0) {
      throw new SimulationDataError(rowPath)
    }
  }
}

export function normalizeSimulationResponse(payload) {
  const envelope = objectAt(payload, 'response')
  if (envelope.success !== true) throw new SimulationDataError('response.success')
  const data = objectAt(envelope.data, 'response.data')
  if (typeof data.baseline_key !== 'string' || data.baseline_key.length === 0) {
    throw new SimulationDataError('response.data.baseline_key')
  }

  const analysis = objectAt(data.analysis, 'response.data.analysis')
  if (analysis.contract_version !== 'retirement-analysis-v1') {
    throw new SimulationDataError('response.data.analysis.contract_version')
  }
  if (!['baseline', 'what_if'].includes(analysis.mode)) {
    throw new SimulationDataError('response.data.analysis.mode')
  }
  if (!Array.isArray(analysis.notices)) throw new SimulationDataError('response.data.analysis.notices')
  const units = objectAt(analysis.units, 'response.data.analysis.units')
  if (
    units.amount !== 'KRW'
    || units.probability !== 'fraction'
    || units.probability_delta !== 'pp'
  ) {
    throw new SimulationDataError('response.data.analysis.units')
  }
  validateProfile(analysis.baseline_profile, 'response.data.analysis.baseline_profile')
  const metadata = objectAt(analysis.metadata, 'response.data.analysis.metadata')
  if (integerAt(metadata.num_paths, 'response.data.analysis.metadata.num_paths') < 1) {
    throw new SimulationDataError('response.data.analysis.metadata.num_paths')
  }
  validateResult(analysis.baseline, 'response.data.analysis.baseline')
  if (!Array.isArray(analysis.scenarios)) throw new SimulationDataError('response.data.analysis.scenarios')
  if (analysis.mode === 'baseline' && analysis.scenarios.length !== 0) {
    throw new SimulationDataError('response.data.analysis.scenarios')
  }
  if (analysis.mode === 'what_if' && analysis.scenarios.length !== 1) {
    throw new SimulationDataError('response.data.analysis.scenarios')
  }

  if (analysis.mode === 'what_if') {
    const scenario = objectAt(analysis.scenarios[0], 'response.data.analysis.scenarios[0]')
    if (scenario.scenario_id !== 'comparison') {
      throw new SimulationDataError('response.data.analysis.scenarios[0].scenario_id')
    }
    objectAt(scenario.changes, 'response.data.analysis.scenarios[0].changes')
    validateProfile(scenario.profile, 'response.data.analysis.scenarios[0].profile')
    validateResult(scenario.result, 'response.data.analysis.scenarios[0].result')
    const delta = objectAt(scenario.delta, 'response.data.analysis.scenarios[0].delta')
    finiteAt(delta.success_probability_delta_pp, 'response.data.analysis.scenarios[0].delta.success_probability_delta_pp')
    validatePercentiles(delta.final_balance_delta, 'response.data.analysis.scenarios[0].delta.final_balance_delta')
  }

  const meta = objectAt(envelope.meta, 'response.meta')
  if (typeof meta.simulation_policy_version !== 'string') {
    throw new SimulationDataError('response.meta.simulation_policy_version')
  }

  return {
    baselineKey: data.baseline_key,
    analysis,
    meta,
  }
}

export function normalizeSimulationError(error) {
  if (error instanceof SimulationDataError) return error
  const status = error?.response?.status ?? null
  const payload = error?.response?.data?.error
  const normalized = new Error(
    typeof payload?.message === 'string'
      ? payload.message
      : '시뮬레이션을 계산하지 못했습니다. 잠시 후 다시 시도해 주세요.',
  )
  normalized.name = 'SimulationRequestError'
  normalized.code = typeof payload?.code === 'string' ? payload.code : 'SIMULATION_REQUEST_FAILED'
  normalized.retryable = typeof payload?.retryable === 'boolean' ? payload.retryable : status === null || status >= 500
  normalized.status = status
  if (payload?.details && typeof payload.details === 'object') normalized.details = payload.details
  return normalized
}
