<script setup>
import { computed } from 'vue'
import { useReportStore } from '@/stores/report'
import { fmtKRW } from '@/utils/format'
const reportStore = useReportStore()
const pension = computed(() => reportStore.reports?.['pension-schedule'])
const rows = computed(() => (pension.value?.yearly_schedule ?? [])
  .filter((row, index, all) => index === 0 || index === all.length - 1 || row.age % 5 === 0))
const maxAmount = computed(() => Math.max(0, ...rows.value.map((row) => row.monthly_amount)))
const methodLabel = computed(() => ({ user_input: '사용자가 입력한 예상액', simple_formula: '가입기간·소득에 따른 간이 추정', unavailable: '추정에 필요한 정보 없음' }[pension.value?.results.estimate_method] ?? '계산 결과'))
</script>

<template>
  <div class="slide">
    <template v-if="pension">
      <div class="summary">국민연금 · {{ pension.results.pension_start_age }}세부터 <strong>{{ fmtKRW(pension.results.final_monthly_est) }}/월</strong><br><small>{{ methodLabel }}</small></div>
      <div v-if="rows.length" class="timeline">
        <div v-for="row in rows" :key="row.age" class="tl-row">
          <div class="tl-age">{{ row.age }}세</div>
          <div class="tl-bar-wrap"><div class="tl-bar national" :style="{ width: `${maxAmount > 0 ? row.monthly_amount / maxAmount * 100 : 0}%` }"></div></div>
          <div class="tl-info"><span class="tl-name">{{ row.year }}년</span><span class="tl-amount">{{ fmtKRW(row.monthly_amount) }}/월</span></div>
        </div>
      </div>
      <p v-else role="status">국민연금 수령 예상액이 없습니다. 가입·예상 수령 정보를 확인해 주세요.</p>
      <p class="note">현재 보고서는 국민연금 예상 수령 일정입니다. 개인·퇴직연금 합계가 아닙니다.</p>
    </template>
    <p v-else role="status">연금 수령 일정 보고서를 준비해 주세요.</p>
  </div>
</template>

<style scoped>
.slide {
  width: 100%;
  height: 100%;
  padding: 14px 20px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 14px;
  box-sizing: border-box;
  overflow: auto;
}

.timeline { display: flex; flex-direction: column; gap: 12px; }

.tl-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.tl-age { width: 36px; font-size: 11px; font-weight: 700; color: #6b7280; flex-shrink: 0; }

.tl-bar-wrap {
  flex: 1;
  height: 18px;
  background: #f0f0f8;
  border-radius: 4px;
  overflow: hidden;
}

.tl-bar { height: 100%; border-radius: 4px; transition: width 0.5s; }
.tl-bar.personal   { background: #a78bfa; }
.tl-bar.retirement { background: #60a5fa; }
.tl-bar.national   { background: #5e5ce6; }

.tl-info { width: 120px; display: flex; justify-content: space-between; flex-shrink: 0; }
.tl-name   { font-size: 11px; color: #4a5070; }
.tl-amount { font-size: 11px; font-weight: 700; color: #1a1f3c; }

.summary {
  font-size: 12px;
  color: #6b7280;
  background: #f4f5ff;
  border-radius: 8px;
  padding: 8px 12px;
}
.note { font-size: 11px; color: #64748b; line-height: 1.6; }
.tl-info { min-width: 105px; }
.tl-bar-wrap { min-width: 30px; }
</style>
