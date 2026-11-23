<script setup>
import { computed } from 'vue'
import { useReportStore } from '@/stores/report'
import { fmtKRW } from '@/utils/format'
const reportStore = useReportStore()
const savings = computed(() => reportStore.reports?.['required-savings'])
const currentWidth = computed(() => savings.value?.target_monthly_savings > 0
  ? Math.min(100, Math.max(0, savings.value.current_monthly_savings / savings.value.target_monthly_savings * 100)) : 0)
</script>

<template>
  <div class="slide">
    <template v-if="savings">
    <div class="compare">
      <div class="box current">
        <div class="box-label">현재 월 저축</div>
        <div class="box-amount">{{ fmtKRW(savings.current_monthly_savings) }}</div>
        <div class="bar-track"><div class="bar" :style="{ width: `${currentWidth}%`, background: '#94a3b8' }"></div></div>
      </div>
      <div class="arrow">→</div>
      <div class="box needed">
        <div class="box-label">목표 달성을 위한<br/>필요 월 저축</div>
        <div class="box-amount hl">{{ fmtKRW(savings.target_monthly_savings) }}</div>
        <div class="bar-track"><div class="bar" :style="{ width: savings.target_monthly_savings > 0 ? '100%' : '0%', background: '#5e5ce6' }"></div></div>
      </div>
    </div>
    <div class="gap-box">
      <span class="gap-label">월 부족 저축액</span>
      <span class="gap-amount">{{ fmtKRW(savings.additional_required_savings) }}</span>
    </div>
    <div class="note">현재 금융·연금자산 월 적립액과 은퇴 부족자금에 대한 추가 필요 저축액입니다.</div>
    </template>
    <p v-else role="status">필요저축액 보고서를 준비해 주세요.</p>
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
  gap: 12px;
  box-sizing: border-box;
  overflow: auto;
}

.compare { display: flex; align-items: center; gap: 12px; }

.box {
  flex: 1;
  background: #fff;
  border: 1px solid #e6e8f0;
  border-radius: 12px;
  padding: 12px 14px;
}

.box.needed { border-color: #5e5ce6; background: #f4f5ff; }

.box-label  { font-size: 10px; color: #6b7280; font-weight: 600; line-height: 1.4; margin-bottom: 4px; }
.box-amount { font-size: 22px; font-weight: 800; color: #1a1f3c; margin-bottom: 8px; }
.box-amount.hl { color: #5e5ce6; }

.bar-track { height: 6px; background: #f0f0f8; border-radius: 999px; overflow: hidden; }
.bar { height: 100%; border-radius: 999px; }

.arrow { font-size: 20px; color: #c4c8dc; flex-shrink: 0; }

.gap-box {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  background: #fff1f1;
  border-radius: 10px;
  padding: 10px 14px;
}

.gap-label  { font-size: 12px; font-weight: 700; color: #b83232; }
.gap-amount { font-size: 20px; font-weight: 800; color: #b83232; }

.note { font-size: 11px; color: #6b7280; }
.compare { flex-wrap: wrap; }
.box { min-width: 150px; }
.box-amount { overflow-wrap: anywhere; }
.gap-box { flex-wrap: wrap; gap: 8px; }
</style>
