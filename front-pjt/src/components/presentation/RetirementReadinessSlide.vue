<script setup>
import { computed } from 'vue'
import { useReportStore } from '@/stores/report'
import { fmtKRW } from '@/utils/format'
const reportStore = useReportStore()
const readiness = computed(() => reportStore.reports?.['retirement-readiness'])
const fund = computed(() => reportStore.reports?.['retirement-fund'])
const ratio = computed(() => fund.value?.total_required_fund > 0 && readiness.value
  ? readiness.value.total_retirement_assets / fund.value.total_required_fund * 100 : null)
const gaugeOffset = computed(() => 251.2 * (1 - Math.min(100, Math.max(0, ratio.value ?? 0)) / 100))
</script>

<template>
  <div class="slide">
    <template v-if="readiness">
    <div class="gauge-area">
      <svg viewBox="0 0 200 110" class="gauge" role="img" :aria-label="ratio === null ? '목표 대비 준비율 계산 불가' : `은퇴 시 예상 준비율 ${ratio.toFixed(1)}%`">
        <path d="M20 100 A80 80 0 0 1 180 100" stroke="#e5e7eb" stroke-width="18" fill="none" stroke-linecap="round"/>
        <path d="M20 100 A80 80 0 0 1 180 100" stroke="#5e5ce6" stroke-width="18" fill="none"
          stroke-linecap="round"
          stroke-dasharray="251.2"
          :stroke-dashoffset="gaugeOffset"
        />
        <text x="100" y="88" text-anchor="middle" font-size="22" font-weight="800" fill="#1a1f3c">{{ ratio === null ? '—' : `${ratio.toFixed(1)}%` }}</text>
        <text x="100" y="104" text-anchor="middle" font-size="9" fill="#6b7280">은퇴 시 예상 준비율</text>
      </svg>
    </div>
    <div class="details">
      <div class="rd-row"><span class="rd-k">{{ readiness.retirement_age }}세 예상 준비자금</span><span class="rd-v">{{ fmtKRW(readiness.total_retirement_assets) }}</span></div>
      <div v-if="fund" class="rd-row"><span class="rd-k">필요 은퇴자금</span><span class="rd-v">{{ fmtKRW(fund.total_required_fund) }}</span></div>
      <div class="rd-row"><span class="rd-k">금융자산</span><span class="rd-v">{{ fmtKRW(readiness.retirement_composition.financial) }}</span></div>
      <div class="rd-row"><span class="rd-k">부동산</span><span class="rd-v">{{ fmtKRW(readiness.retirement_composition.real_estate) }}</span></div>
      <div class="rd-row"><span class="rd-k">연금 적립자산</span><span class="rd-v">{{ fmtKRW(readiness.retirement_composition.pension_accumulation) }}</span></div>
      <div class="rd-row"><span class="rd-k">예상 퇴직금</span><span class="rd-v">{{ fmtKRW(readiness.retirement_income_sources.severance_pay_estimated) }}</span></div>
      <p class="note">예상 준비자금 ÷ 필요 은퇴자금으로 표시한 비율입니다. 부동산을 포함한 자산 예상액이며 현금화 가능액과 다를 수 있습니다.</p>
    </div>
    </template>
    <p v-else role="status">은퇴 준비자금 보고서를 준비해 주세요.</p>
  </div>
</template>

<style scoped>
.slide {
  width: 100%;
  height: 100%;
  padding: 14px 20px;
  display: flex;
  align-items: center;
  gap: 20px;
  box-sizing: border-box;
  overflow: auto;
  flex-wrap: wrap;
}

.gauge-area { flex: 0 0 180px; }
.gauge { width: 180px; height: 100px; display: block; }

.details {
  flex: 1;
  min-width: 220px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rd-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  padding: 5px 0;
  border-bottom: 1px solid #f0f0f8;
}

.rd-k { color: #6b7280; }
.rd-v { font-weight: 700; color: #1a1f3c; }
.rd-v.red { color: #b83232; }
.rd-row.deficit .rd-k { font-weight: 700; }
.rd-row { gap: 10px; flex-wrap: wrap; }
.note { font-size: 11px; color: #64748b; line-height: 1.6; }
</style>
