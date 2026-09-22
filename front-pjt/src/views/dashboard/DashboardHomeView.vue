<template>
  <!-- 형제 탭과 같은 로딩/오류/보고서 없음 상태를 공유한다. 세 보고서가 모두 준비돼야 본문을 그린다. -->
  <DashboardReportShell title="재무 현황" :report-key="HOME_REPORT_KEYS" :panel="false">
  <div class="home-view">

    <!-- 섹션: 은퇴 준비 상태 -->
    <div class="section">
      <div class="section-header">
        <span class="section-label">은퇴 준비 점수</span>
      </div>
      <RetirementScoreCard
        :score="currentData.retirementScore.score"
        :grade="currentData.retirementScore.grade"
        :ai-comment="currentData.aiComment"
      />
    </div>

    <div class="section-rule"></div>

    <!-- 섹션: 자산 -->
    <div class="section">
      <div class="section-header">
        <span class="section-label">자산</span>
      </div>
      <div class="section-body">
        <div class="net-worth-row">
          <StatCard 
            label="총자산"
            :amount="currentData.assets.total" 
            colorType="positive" 
          />
          <StatCard 
            label="총부채" 
            :amount="currentData.assets.debt" 
            colorType="negative" 
          />
          <StatCard 
            label="순자산" 
            :amount="currentData.assets.net" 
            colorType="accent" 
            :isHighlight="true" 
          />
        </div>
        <div class="card chart-card">
          <div class="card-title">자산 별 비중</div>
          <AssetPieEcharts :breakdown="currentData.assets.breakdown" />
        </div>
      </div>
    </div>

    <div class="section-rule"></div>

    <!-- 섹션: 현금흐름 -->
    <div class="section">
      <div class="section-header">
        <span class="section-label">현금흐름</span>
      </div>
      <div class="section-body">
        <div class="net-worth-row">
          <StatCard 
            label="총현금유입"
            :amount="currentCashflow.summary.inflow" 
            colorType="positive" 
          />
          <StatCard 
            label="총현금유출" 
            :amount="currentCashflow.summary.outflow" 
            colorType="negative" 
          />
          <StatCard 
            label="순현금흐름" 
            :amount="currentCashflow.summary.net" 
            colorType="accent" 
            :isHighlight="true" 
          />
        </div>

        <div class="card chart-card">
          <div class="card-title">월별 현금흐름</div>
          <CashFlowBarEcharts
            :monthly-history="currentCashflow.history"
            :selected-period="selectedCashflowPeriod"
          />
        </div>
      </div>
    </div>

    <div class="section-rule"></div>
    
    <!-- 섹션: 가계재무지표 -->
    <div class="section">
      <div class="section-header">
        <span class="section-label">가계재무지표</span>
      </div>
      <div class="section-body">
        <div class="card">
          <div class="indicators-section">
            <div
              v-for="(group, groupKey) in groups"
              :key="groupKey"
              class="indicator-group"
            >
              <div class="group-title">{{ group.label }}</div>
              <div class="gauge-grid">
                <FinancialGaugeEcharts
                  v-for="indicator in group.items"
                  :key="indicator.key"
                  :indicator="indicator"
                />
              </div>
            </div>
          </div>

        </div>
      </div>
    </div>

  </div>
  </DashboardReportShell>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useReportStore } from '@/stores/report'

import DashboardReportShell from '@/components/dashboard/DashboardReportShell.vue'
import RetirementScoreCard from '@/components/dashboard/RetirementScoreCard.vue'
import StatCard from '@/components/dashboard/StatCard.vue'

import AssetPieEcharts from '@/components/charts/AssetPieEcharts.vue'
import CashFlowBarEcharts from '@/components/charts/CashFlowBarEcharts.vue'
import FinancialGaugeEcharts from '@/components/charts/FinancialGaugeEcharts.vue'

// 홈은 자산현황표·현금흐름표·가계재무지표를 합쳐 보여주므로 셋 모두 준비됐을 때만 본문을 그린다.
const HOME_REPORT_KEYS = ['asset_statement', 'cash_flow', 'financial-indicators']

const reportStore = useReportStore()
const selectedPeriod = ref(reportStore.periods[reportStore.periods.length - 1])
const selectedCashflowPeriod = ref(reportStore.periods[reportStore.periods.length - 1])

const currentData = computed(() => reportStore.dashboardOverview(selectedPeriod.value))
const currentCashflow = computed(() => reportStore.dashboardCashflow(selectedCashflowPeriod.value))

const groups = computed(() => {
  const indicators = currentData.value?.financialIndicators

  return {
    cashflow: { label: '현금흐름', items: indicators?.cashflow || [] },
    assets: { label: '자산', items: indicators?.assets || [] },
    risk: { label: '리스크 대비', items: indicators?.risk || [] }
  }
})

</script>

<style scoped>
.home-view {
  display: flex;
  flex-direction: column;
  gap: 20px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

/* 공통 카드 */
.card {
  background: #ffffff;
  border: 1px solid #e9eaf2;
  border-radius: 12px;
  padding: 20px 24px;
  box-shadow: 0 1px 3px rgba(20, 20, 43, 0.06);
}

.card-title {
  font-size: 16px;
  font-weight: 700;
  color: #1a1a2e;
  margin-bottom: 16px;
}

.card-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.card-header-row .card-title {
  margin-bottom: 0;
}

/* 섹션 분할 */
.section {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 12px;
}

.section-label {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 17px;
  font-weight: 800;
  color: #1a1a2e;
  white-space: nowrap;
  letter-spacing: -0.2px;
}

.section-label::before {
  content: '';
  width: 4px;
  height: 17px;
  border-radius: 2px;
  background: #5e5ce6;
}

.section-rule {
  flex: 1;
  height: 1px;
  background-color: #eceef5;
}

.section-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.chart-card {
  min-width: 0;
}

.net-worth-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}

/* 가계 재무지표 */
.indicators-section {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.indicator-group {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.group-title {
  font-size: 12px;
  font-weight: 700;
  color: #888;
  text-transform: uppercase;
  letter-spacing: 0.6px;
}

.gauge-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
}
</style>
