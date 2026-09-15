import { simulationsApi } from './index.js'

const CHANGE_FIELDS = new Set([
  'monthly_savings',
  'retirement_age',
  'retirement_expenses',
])

export function createBaselineRequest() {
  return { mode: 'baseline' }
}

export function createWhatIfRequest(baselineKey, changes) {
  if (typeof baselineKey !== 'string' || baselineKey.length === 0) {
    throw new TypeError('baseline_key가 필요합니다.')
  }
  if (!changes || typeof changes !== 'object' || Array.isArray(changes)) {
    throw new TypeError('changes는 객체여야 합니다.')
  }

  const entries = Object.entries(changes).filter(([, value]) => value !== null && value !== undefined)
  if (entries.length === 0) throw new TypeError('변경할 시뮬레이션 조건이 필요합니다.')
  const unsupported = entries.map(([field]) => field).filter((field) => !CHANGE_FIELDS.has(field))
  if (unsupported.length > 0) {
    throw new TypeError(`지원하지 않는 시뮬레이션 조건입니다: ${unsupported.sort().join(', ')}`)
  }

  return {
    mode: 'what_if',
    baseline_key: baselineKey,
    changes: Object.fromEntries(entries),
  }
}

export function runBaselineSimulation(sessionId, { signal } = {}) {
  return simulationsApi.post(
    `/consultations/${sessionId}/simulations/`,
    createBaselineRequest(),
    { signal },
  )
}

export function runWhatIfSimulation(sessionId, baselineKey, changes, { signal } = {}) {
  return simulationsApi.post(
    `/consultations/${sessionId}/simulations/`,
    createWhatIfRequest(baselineKey, changes),
    { signal },
  )
}
