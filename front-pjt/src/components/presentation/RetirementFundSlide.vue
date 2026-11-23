<script setup>
import { computed } from 'vue'
import { useReportStore } from '@/stores/report'
import { fmtKRW } from '@/utils/format'
const reportStore = useReportStore()
const fund = computed(() => reportStore.reports?.['retirement-fund'])
</script>

<template>
  <div class="slide">
    <div v-if="fund" class="card">
      <div class="rows">
        <div class="row"><span class="k">은퇴까지 준비기간</span><span class="v">{{ fund.prep_period_years }}년</span></div>
        <div class="row"><span class="k">은퇴 기간</span><span class="v">{{ fund.retirement_period_years }}년</span></div>
        <div class="row"><span class="k">물가상승률 가정</span><span class="v">{{ (fund.assumptions.inflation_rate * 100).toFixed(1) }}% / 년</span></div>
      </div>
      <div class="divider"></div>
      <div class="result-row">
        <span class="result-k">필요 총 은퇴자금</span>
        <span class="result-v">{{ fmtKRW(fund.total_required_fund) }}</span>
      </div>
      <div class="sub-row">
        <span class="sub-k">예상 연금 수령 총액</span>
        <span class="sub-v">{{ fmtKRW(fund.total_prepared_fund) }}</span>
      </div>
      <div class="sub-row"><span class="sub-k">연금 수령액 대비 부족자금</span><span class="sub-v">{{ fmtKRW(fund.shortfall) }}</span></div>
      <div v-for="phase in fund.phase_details" :key="phase.phase_name" class="phase">
        <div class="row"><span>{{ phase.phase_name }} · {{ phase.duration_years }}년</span><strong>{{ fmtKRW(phase.total_required) }}</strong></div>
        <div class="phase-track"><div :style="{ width: `${fund.total_required_fund > 0 ? phase.total_required / fund.total_required_fund * 100 : 0}%` }" /></div>
        <small>구간 시작 시점 월 필요액 {{ fmtKRW(phase.monthly_required) }}</small>
      </div>
    </div>
    <p v-else role="status">은퇴 필요자금 보고서를 준비해 주세요.</p>
  </div>
</template>

<style scoped>
.slide {
  width: 100%;
  height: 100%;
  padding: 14px 20px;
  display: flex;
  align-items: center;
  box-sizing: border-box;
  overflow: auto;
}

.card {
  width: 100%;
  background: #fff;
  border: 1px solid #e6e8f0;
  border-radius: 14px;
  padding: 14px 18px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rows { display: flex; flex-direction: column; gap: 4px; }

.row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #4a5070;
  padding: 3px 0;
  border-bottom: 1px solid #f0f0f8;
}

.k { color: #6b7280; }
.v { font-weight: 700; color: #1a1f3c; }

.divider { height: 2px; background: #1a1f3c; }

.result-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.result-k { font-size: 12px; font-weight: 700; color: #4a5070; }
.result-v { font-size: 22px; font-weight: 800; color: #5e5ce6; }

.sub-row {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: #9498b5;
}
.phase { margin-top: 6px; }
.phase small { color: #64748b; font-size: 11px; }
.phase-track { height: 7px; background: #eef2ff; border-radius: 4px; margin: 5px 0; }
.phase-track > div { height: 100%; background: #6366f1; border-radius: inherit; }
.row, .result-row, .sub-row { flex-wrap: wrap; gap: 6px; }
</style>
