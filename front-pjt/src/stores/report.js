import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { getSessions } from '@/api/consulting'
import { getReports } from '@/api/reports'
import { MOCK_PRESENTATION_DATA } from '@/mock/aiData'
import { PERIODS, periodData, monthlyHistory } from '@/mock/dashboardData'
import {
  buildDashboardCashflow,
  buildDashboardOverview,
  normalizePresentationData,
} from '@/utils/reportData'

const DEVELOPMENT_CACHE_KEY = 'development'

function reportCacheKey(sessionId) {
  return `session:${sessionId}`
}

function mapReports(rows) {
  const mapped = {}
  for (const report of rows ?? []) {
    mapped[report.report_type] = report.content
  }
  return normalizePresentationData(mapped)
}

export const useReportStore = defineStore('report', () => {
  const reportsBySession = ref({})
  const activeCacheKey = ref(null)
  const loadingKeys = ref([])
  const error = ref(null)
  const inFlight = new Map()
  let cacheGeneration = 0
  let requestSequence = 0

  const reports = computed(() => {
    if (!activeCacheKey.value) return null
    return reportsBySession.value[activeCacheKey.value] ?? null
  })
  const loading = computed(() => loadingKeys.value.length > 0)

  function setLoading(key, value) {
    loadingKeys.value = value
      ? [...new Set([...loadingKeys.value, key])]
      : loadingKeys.value.filter((loadingKey) => loadingKey !== key)
  }

  function readCached(key) {
    return Object.prototype.hasOwnProperty.call(reportsBySession.value, key)
      ? reportsBySession.value[key]
      : undefined
  }

  function writeCached(key, value) {
    reportsBySession.value = { ...reportsBySession.value, [key]: value }
  }

  async function loadReports(sessionId, { force = false } = {}) {
    const key = import.meta.env.DEV ? DEVELOPMENT_CACHE_KEY : reportCacheKey(sessionId)
    activeCacheKey.value = key
    error.value = null

    const cached = readCached(key)
    if (!force && cached !== undefined) return cached
    if (!import.meta.env.DEV && !sessionId) {
      activeCacheKey.value = null
      return null
    }

    if (inFlight.has(key)) return inFlight.get(key)

    const generation = cacheGeneration
    const loadingToken = `${key}:${++requestSequence}`
    const request = Promise.resolve().then(async () => {
      setLoading(loadingToken, true)
      try {
        const normalized = import.meta.env.DEV
          ? normalizePresentationData(MOCK_PRESENTATION_DATA)
          : mapReports((await getReports(sessionId)).data?.data)

        if (generation === cacheGeneration) writeCached(key, normalized)
        return normalized
      } catch (requestError) {
        if (generation === cacheGeneration && activeCacheKey.value === key) {
          error.value = requestError
        }
        return null
      } finally {
        setLoading(loadingToken, false)
        if (inFlight.get(key) === request) inFlight.delete(key)
      }
    })

    inFlight.set(key, request)
    return request
  }

  async function loadLatestReports(options) {
    if (import.meta.env.DEV) return loadReports(null, options)
    if (!options?.force && reports.value) return reports.value

    const loadingToken = `latest-session:${++requestSequence}`
    setLoading(loadingToken, true)
    error.value = null
    try {
      const { data } = await getSessions()
      const latest = data.data?.[0]
      if (!latest) {
        activeCacheKey.value = null
        return null
      }
      return loadReports(latest.id, options)
    } catch (requestError) {
      activeCacheKey.value = null
      error.value = requestError
      return null
    } finally {
      setLoading(loadingToken, false)
    }
  }

  function hasReport(reportKey) {
    return Boolean(reports.value?.[reportKey])
  }

  function dashboardOverview(period = PERIODS[PERIODS.length - 1]) {
    return buildDashboardOverview(reports.value, periodData[period])
  }

  function dashboardCashflow(period = PERIODS[PERIODS.length - 1]) {
    return buildDashboardCashflow(reports.value, PERIODS, monthlyHistory, period)
  }

  function clearActiveReport() {
    activeCacheKey.value = null
    error.value = null
  }

  function clearReports() {
    cacheGeneration += 1
    reportsBySession.value = {}
    activeCacheKey.value = null
    loadingKeys.value = []
    error.value = null
    inFlight.clear()
  }

  return {
    reports,
    loading,
    error,
    periods: PERIODS,
    loadReports,
    loadLatestReports,
    hasReport,
    dashboardOverview,
    dashboardCashflow,
    clearActiveReport,
    clearReports,
  }
})
