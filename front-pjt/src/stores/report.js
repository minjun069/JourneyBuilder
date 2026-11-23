import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { getSessions } from '@/api/consulting'
import { createReports, getReports } from '@/api/reports'
import {
  runBaselineSimulation as requestBaselineSimulation,
  runWhatIfSimulation as requestWhatIfSimulation,
} from '@/api/simulations'
import { PERIODS, periodData, monthlyHistory } from '@/mock/dashboardData'
import {
  buildDashboardCashflow,
  buildDashboardOverview,
  normalizePresentationData,
} from '@/utils/reportData'
import {
  normalizeSimulationError,
  normalizeSimulationResponse,
} from '@/utils/simulationData'

const PRESENTATION_REPORT_TYPES = [
  'asset_statement',
  'cash_flow',
  'financial-indicators',
  'financial-goal-result',
  'retirement-fund',
  'retirement-readiness',
  'pension-schedule',
  'lifetime-cashflow',
  'required-savings',
]

const PRESENTATION_REPORT_BY_STEP = {
  'balance-sheet': 'asset_statement',
  'cash-flow': 'cash_flow',
  'financial-indicators': 'financial-indicators',
  'financial-goal-result': 'financial-goal-result',
  'retirement-fund': 'retirement-fund',
  'retirement-readiness': 'retirement-readiness',
  'pension-schedule': 'pension-schedule',
  'lifetime-cashflow': 'lifetime-cashflow',
  'required-savings': 'required-savings',
}

function reportCacheKey(sessionId) {
  return `session:${sessionId}`
}

function mapReports(rows) {
  const mapped = {}
  for (const report of rows ?? []) {
    if (report?.status !== 'ready' || !report.content || typeof report.content !== 'object') continue
    mapped[report.report_type] = {
      ...report.content,
      _report_meta: {
        id: report.id,
        reportType: report.report_type,
        version: report.version,
        status: report.status,
        generatedAt: report.generated_at,
      },
    }
  }
  return normalizePresentationData(mapped)
}

function latestReadySession(rows) {
  return (rows ?? []).find((session) => (
    session?.is_ready_for_reports === true && session.status !== 'abandoned'
  )) ?? null
}

export const useReportStore = defineStore('report', () => {
  const reportsBySession = ref({})
  const simulationsBySession = ref({})
  const simulationErrorsBySession = ref({})
  const activeSessionId = ref(null)
  const loadingKeys = ref([])
  const simulationLoadingKeys = ref([])
  const error = ref(null)
  const reportInFlight = new Map()
  const reportGenerationInFlight = new Map()
  const simulationInFlight = new Map()
  const latestSimulationRequest = new Map()
  let cacheGeneration = 0
  let requestSequence = 0

  const activeCacheKey = computed(() => (
    activeSessionId.value ? reportCacheKey(activeSessionId.value) : null
  ))
  const reports = computed(() => (
    activeCacheKey.value ? reportsBySession.value[activeCacheKey.value] ?? null : null
  ))
  const simulation = computed(() => (
    activeCacheKey.value ? simulationsBySession.value[activeCacheKey.value] ?? null : null
  ))
  const simulationError = computed(() => (
    activeCacheKey.value ? simulationErrorsBySession.value[activeCacheKey.value] ?? null : null
  ))
  const loading = computed(() => loadingKeys.value.length > 0)
  const simulationLoading = computed(() => simulationLoadingKeys.value.length > 0)

  function setLoading(collection, key, value) {
    collection.value = value
      ? [...new Set([...collection.value, key])]
      : collection.value.filter((loadingKey) => loadingKey !== key)
  }

  function readCached(key) {
    return Object.prototype.hasOwnProperty.call(reportsBySession.value, key)
      ? reportsBySession.value[key]
      : undefined
  }

  function writeReports(key, value) {
    reportsBySession.value = { ...reportsBySession.value, [key]: value }
  }

  function writeSimulation(key, value) {
    simulationsBySession.value = { ...simulationsBySession.value, [key]: value }
  }

  function writeSimulationError(key, value) {
    simulationErrorsBySession.value = {
      ...simulationErrorsBySession.value,
      [key]: value,
    }
  }

  async function loadReports(sessionId, { force = false } = {}) {
    if (!sessionId) {
      activeSessionId.value = null
      return null
    }

    activeSessionId.value = sessionId
    const key = reportCacheKey(sessionId)
    error.value = null
    const cached = readCached(key)
    if (!force && cached !== undefined) return cached
    if (reportInFlight.has(key)) return reportInFlight.get(key)

    const generation = cacheGeneration
    const loadingToken = `${key}:load:${++requestSequence}`
    const request = Promise.resolve().then(async () => {
      setLoading(loadingKeys, loadingToken, true)
      try {
        const normalized = mapReports((await getReports(sessionId)).data?.data)
        if (generation === cacheGeneration) writeReports(key, normalized)
        return normalized
      } catch (requestError) {
        if (generation === cacheGeneration && activeCacheKey.value === key) error.value = requestError
        return null
      } finally {
        setLoading(loadingKeys, loadingToken, false)
        if (reportInFlight.get(key) === request) reportInFlight.delete(key)
      }
    })

    reportInFlight.set(key, request)
    return request
  }

  async function generateReports(sessionId) {
    if (!sessionId) return null
    activeSessionId.value = sessionId
    const key = reportCacheKey(sessionId)
    if (reportGenerationInFlight.has(key)) return reportGenerationInFlight.get(key)

    const generation = cacheGeneration
    const loadingToken = `${key}:generate:${++requestSequence}`
    error.value = null
    const request = Promise.resolve().then(async () => {
      setLoading(loadingKeys, loadingToken, true)
      try {
        const normalized = mapReports((await createReports(sessionId)).data?.data)
        if (generation === cacheGeneration) writeReports(key, normalized)
        return normalized
      } catch (requestError) {
        if (generation === cacheGeneration && activeCacheKey.value === key) error.value = requestError
        return null
      } finally {
        setLoading(loadingKeys, loadingToken, false)
        if (reportGenerationInFlight.get(key) === request) {
          reportGenerationInFlight.delete(key)
        }
      }
    })

    reportGenerationInFlight.set(key, request)
    return request
  }

  async function ensureReports(sessionId) {
    const current = await loadReports(sessionId, { force: true })
    if (PRESENTATION_REPORT_TYPES.every((reportType) => current?.[reportType])) return current
    return generateReports(sessionId)
  }

  async function resolveLatestReadySession() {
    const { data } = await getSessions()
    return latestReadySession(data.data)
  }

  async function loadLatestReports(options) {
    if (!options?.force && reports.value) return reports.value
    const loadingToken = `latest-session:${++requestSequence}`
    setLoading(loadingKeys, loadingToken, true)
    error.value = null
    const generation = cacheGeneration
    try {
      const latest = await resolveLatestReadySession()
      if (generation !== cacheGeneration) return null
      if (!latest) {
        activeSessionId.value = null
        return null
      }
      return loadReports(latest.id, options)
    } catch (requestError) {
      activeSessionId.value = null
      error.value = requestError
      return null
    } finally {
      setLoading(loadingKeys, loadingToken, false)
    }
  }

  async function runBaselineSimulation(sessionId = activeSessionId.value) {
    if (!sessionId) return null
    activeSessionId.value = sessionId
    const key = reportCacheKey(sessionId)
    const inFlightKey = `${key}:baseline`
    if (simulationInFlight.has(inFlightKey)) return simulationInFlight.get(inFlightKey)

    const requestId = ++requestSequence
    const loadingToken = `${inFlightKey}:${requestId}`
    const generation = cacheGeneration
    latestSimulationRequest.set(key, requestId)
    writeSimulationError(key, null)

    const request = Promise.resolve().then(async () => {
      setLoading(simulationLoadingKeys, loadingToken, true)
      try {
        const normalized = normalizeSimulationResponse(
          (await requestBaselineSimulation(sessionId)).data,
        )
        if (generation === cacheGeneration && latestSimulationRequest.get(key) === requestId) {
          writeSimulation(key, { ...normalized, stale: false })
        }
        return normalized
      } catch (requestError) {
        const normalizedError = normalizeSimulationError(requestError)
        if (generation === cacheGeneration && latestSimulationRequest.get(key) === requestId) {
          writeSimulationError(key, normalizedError)
        }
        return null
      } finally {
        setLoading(simulationLoadingKeys, loadingToken, false)
        if (simulationInFlight.get(inFlightKey) === request) simulationInFlight.delete(inFlightKey)
      }
    })

    simulationInFlight.set(inFlightKey, request)
    return request
  }

  async function runWhatIfSimulation(changes, sessionId = activeSessionId.value) {
    if (!sessionId) return null
    activeSessionId.value = sessionId
    const key = reportCacheKey(sessionId)
    const baselineKey = simulationsBySession.value[key]?.baselineKey
    if (!baselineKey) {
      const missing = new Error('기준 시뮬레이션을 먼저 계산해 주세요.')
      missing.code = 'BASELINE_REQUIRED'
      missing.retryable = false
      writeSimulationError(key, missing)
      return null
    }

    const requestId = ++requestSequence
    const loadingToken = `${key}:what-if:${requestId}`
    const generation = cacheGeneration
    latestSimulationRequest.set(key, requestId)
    writeSimulationError(key, null)
    setLoading(simulationLoadingKeys, loadingToken, true)
    try {
      const normalized = normalizeSimulationResponse(
        (await requestWhatIfSimulation(sessionId, baselineKey, changes)).data,
      )
      if (generation === cacheGeneration && latestSimulationRequest.get(key) === requestId) {
        writeSimulation(key, { ...normalized, stale: false })
      }
      return normalized
    } catch (requestError) {
      const normalizedError = normalizeSimulationError(requestError)
      if (generation === cacheGeneration && latestSimulationRequest.get(key) === requestId) {
        writeSimulationError(key, normalizedError)
        if (normalizedError.code === 'BASELINE_STALE') {
          const previous = simulationsBySession.value[key]
          writeSimulation(key, previous ? { ...previous, baselineKey: null, stale: true } : null)
        }
      }
      return null
    } finally {
      setLoading(simulationLoadingKeys, loadingToken, false)
    }
  }

  async function calculatePresentation(sessionId, { regenerateReports = false } = {}) {
    if (!sessionId) return null
    activeSessionId.value = sessionId
    const [reportResult, simulationResult] = await Promise.all([
      regenerateReports ? generateReports(sessionId) : ensureReports(sessionId),
      runBaselineSimulation(sessionId),
    ])
    return { reports: reportResult, simulation: simulationResult }
  }

  async function loadLatestPresentation() {
    const loadingToken = `latest-presentation:${++requestSequence}`
    setLoading(loadingKeys, loadingToken, true)
    error.value = null
    const generation = cacheGeneration
    try {
      const latest = await resolveLatestReadySession()
      if (generation !== cacheGeneration) return null
      if (!latest) {
        activeSessionId.value = null
        return null
      }
      return calculatePresentation(latest.id)
    } catch (requestError) {
      activeSessionId.value = null
      error.value = requestError
      return null
    } finally {
      setLoading(loadingKeys, loadingToken, false)
    }
  }

  function hasReport(reportKey) {
    return Boolean(reports.value?.[reportKey])
  }

  function loadDevelopmentPresentation(scenario) {
    if (!import.meta.env.DEV) throw new Error('Development preview is unavailable')
    // Normalize before changing state so malformed fixtures cannot leave half a scenario.
    const nextReports = mapReports(scenario.reports)
    const nextSimulation = scenario.simulation.success
      ? normalizeSimulationResponse(scenario.simulation)
      : null
    const nextError = scenario.simulation.success ? null : normalizeSimulationError({
      response: { status: 422, data: scenario.simulation },
    })
    clearReports()
    activeSessionId.value = `dev:${scenario.id}`
    const key = reportCacheKey(activeSessionId.value)
    writeReports(key, nextReports)
    simulationsBySession.value = { [key]: nextSimulation }
    simulationErrorsBySession.value = { [key]: nextError }
  }

  function presentationReportVersion(stepId) {
    if (stepId === 'retirement-simulation') {
      return simulation.value?.baselineKey
        ? `simulation:${simulation.value.baselineKey}`
        : `guidance-only:${simulationError.value?.code ?? 'unavailable'}`
    }
    const reportType = PRESENTATION_REPORT_BY_STEP[stepId]
    const meta = reportType ? reports.value?.[reportType]?._report_meta : null
    return meta?.status === 'ready' && meta?.id != null && meta?.version != null
      ? `report:${meta.id}:v${meta.version}`
      : 'guidance-only:unavailable'
  }

  function dashboardOverview(period = PERIODS[PERIODS.length - 1]) {
    return buildDashboardOverview(reports.value, periodData[period])
  }

  function dashboardCashflow(period = PERIODS[PERIODS.length - 1]) {
    return buildDashboardCashflow(reports.value, PERIODS, monthlyHistory, period)
  }

  function invalidateSimulation(sessionId = activeSessionId.value) {
    if (!sessionId) return
    const key = reportCacheKey(sessionId)
    if (simulationsBySession.value[key]) {
      writeSimulation(key, {
        ...simulationsBySession.value[key],
        baselineKey: null,
        stale: true,
      })
    }
  }

  function clearActiveReport() {
    activeSessionId.value = null
    error.value = null
  }

  function clearReports() {
    cacheGeneration += 1
    reportsBySession.value = {}
    simulationsBySession.value = {}
    simulationErrorsBySession.value = {}
    activeSessionId.value = null
    loadingKeys.value = []
    simulationLoadingKeys.value = []
    error.value = null
    reportInFlight.clear()
    reportGenerationInFlight.clear()
    simulationInFlight.clear()
    latestSimulationRequest.clear()
  }

  return {
    reports,
    simulation,
    loading,
    simulationLoading,
    error,
    simulationError,
    activeSessionId,
    periods: PERIODS,
    loadReports,
    loadLatestReports,
    generateReports,
    calculatePresentation,
    loadLatestPresentation,
    runBaselineSimulation,
    runWhatIfSimulation,
    invalidateSimulation,
    hasReport,
    presentationReportVersion,
    dashboardOverview,
    dashboardCashflow,
    clearActiveReport,
    clearReports,
    ...(import.meta.env.DEV ? { loadDevelopmentPresentation } : {}),
  }
})
