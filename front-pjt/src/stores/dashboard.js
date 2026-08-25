import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { PERIODS, periodData, monthlyHistory } from '@/mock/dashboardData'
import { useAiStore } from '@/stores/ai'
import { buildDashboardCashflow, buildDashboardOverview } from '@/utils/reportData'

export const useDashboardStore = defineStore('dashboard', () => {
  const aiStore = useAiStore()

  const selectedPeriod = ref(PERIODS[PERIODS.length - 1])
  const selectedCashflowPeriod = ref(PERIODS[PERIODS.length - 1])

  const reports = computed(() => aiStore.presentationData)
  const currentData = computed(() =>
    buildDashboardOverview(reports.value, periodData[selectedPeriod.value])
  )

  const currentCashflow = computed(() => {
    return buildDashboardCashflow(
      reports.value,
      PERIODS,
      monthlyHistory,
      selectedCashflowPeriod.value,
    )
  })

  async function ensureReportsLoaded() {
    if (!import.meta.env.DEV && !aiStore.sessionId) {
      await aiStore.continueLatestSession()
    }

    if (!aiStore.presentationData) {
      await aiStore.fetchPresentationData()
    }
  }

  function hasReport(reportKey) {
    return Boolean(reports.value?.[reportKey])
  }

  return {
    selectedPeriod,
    selectedCashflowPeriod,
    periods: PERIODS,
    currentData,
    currentCashflow,
    reports,
    loading: computed(() => aiStore.loading),
    error: computed(() => aiStore.error),
    ensureReportsLoaded,
    hasReport,
  }
})
