<script setup>
import { computed } from 'vue'
import { useAiStore } from '@/stores/ai'
import { fmtKRW } from '@/utils/format'

const ai = useAiStore()

const INCOME_VISUAL = {
  '근로소득': { icon: '💼', accent: '#5e5ce6' },
  '사업소득': { icon: '🏪', accent: '#0ea5e9' },
  '이자소득': { icon: '🏦', accent: '#22c55e' },
  '배당소득': { icon: '📊', accent: '#10b981' },
  '연금소득': { icon: '🌂', accent: '#a78bfa' },
  '기타':     { icon: '📋', accent: '#818cf8' },
}
const EXPENSE_VISUAL = {
  fixed:    { icon: '🔒', accent: '#f59e0b' },
  variable: { icon: '🛒', accent: '#ef4444' },
}

const cf = computed(() => ai.presentationData?.cash_flow)

const incomeGroups  = computed(() =>
  (cf.value?.income_groups ?? []).map(g => ({ ...g, ...(INCOME_VISUAL[g.key] ?? { icon: '📋', accent: '#818cf8' }) }))
)
const expenseGroups = computed(() =>
  (cf.value?.expense_groups ?? []).map(g => ({ ...g, ...(EXPENSE_VISUAL[g.key] ?? { icon: '📋', accent: '#9ca3af' }) }))
)

const isSurplusHealthy = computed(() => (cf.value?.surplus_ratio ?? 0) >= 20)

const fmt = fmtKRW
</script>

<template>
  <div class="slide">

    <!-- ── 상단 요약 카드 ── -->
    <div class="summary-row">
      <div class="summary-card card--income">
        <span class="card-label">총 수입</span>
        <span class="card-amount" style="color:#5e5ce6">{{ fmt(cf?.total_income) }}</span>
        <span class="card-sub">{{ cf?.income_groups?.reduce((s, g) => s + g.items.length, 0) }}개 항목</span>
      </div>
      <div class="summary-card card--expense">
        <span class="card-label">총 지출</span>
        <span class="card-amount" style="color:#f59e0b">{{ fmt(cf?.total_expense) }}</span>
        <span class="card-sub">{{ cf?.expense_groups?.reduce((s, g) => s + g.items.length, 0) }}개 항목</span>
      </div>
      <div class="summary-card card--surplus">
        <span class="card-label">잉여 현금</span>
        <span class="card-amount" :style="{ color: isSurplusHealthy ? '#16a34a' : '#dc2626' }">
          {{ fmt(cf?.surplus) }}
        </span>
        <span class="card-sub">
          잉여율 {{ cf?.surplus_ratio }}% — 기준(20%) {{ isSurplusHealthy ? '충족' : '미달' }}
        </span>
      </div>
    </div>

    <!-- ── 본문 2열 그리드 ── -->
    <div class="body-grid">

      <!-- 수입 컬럼 -->
      <div class="col">
        <div class="col-title">수입</div>
        <div v-for="group in incomeGroups" :key="group.key" class="group">
          <div class="group-bar" :style="{ background: group.accent }"></div>
          <div class="group-inner">
            <div class="group-head">
              <span class="group-icon">{{ group.icon }}</span>
              <span class="group-name">{{ group.label }}</span>
              <span class="group-total" :style="{ color: group.total > 0 ? group.accent : '#9ca3af' }">
                {{ fmt(group.total) }}
              </span>
            </div>
            <div v-for="item in group.items" :key="item.label" class="item-row">
              <span class="item-name">{{ item.label }}</span>
              <span class="item-val">{{ fmt(item.value) }}</span>
            </div>
          </div>
        </div>
        <div class="col-total">
          <span>수입 총계</span>
          <span style="color:#5e5ce6">{{ fmt(cf?.total_income) }}</span>
        </div>
      </div>

      <!-- 지출 컬럼 -->
      <div class="col">
        <div class="col-title">지출</div>
        <div v-for="group in expenseGroups" :key="group.key" class="group">
          <div class="group-bar" :style="{ background: group.accent }"></div>
          <div class="group-inner">
            <div class="group-head">
              <span class="group-icon">{{ group.icon }}</span>
              <span class="group-name">{{ group.label }}</span>
              <span class="group-total" :style="{ color: group.total > 0 ? group.accent : '#9ca3af' }">
                {{ fmt(group.total) }}
              </span>
            </div>
            <div v-for="item in group.items" :key="item.label" class="item-row">
              <span class="item-name">{{ item.label }}</span>
              <span class="item-val">{{ fmt(item.value) }}</span>
            </div>
          </div>
        </div>
        <div class="col-total">
          <span>지출 총계</span>
          <span style="color:#f59e0b">{{ fmt(cf?.total_expense) }}</span>
        </div>
      </div>

    </div>
  </div>
</template>

<style scoped>
.slide {
  width: 100%;
  min-height: 100%;
  padding: 16px;
  box-sizing: border-box;
  background: #f5f5f2;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.summary-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.summary-card {
  background: #fff; border-radius: 12px; padding: 14px 16px 12px;
  display: flex; flex-direction: column; gap: 4px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06); border-top: 4px solid transparent;
}
.card--income  { border-top-color: #5e5ce6; }
.card--expense { border-top-color: #f59e0b; }
.card--surplus { border-top-color: #22c55e; }
.card-label  { font-size: 11px; font-weight: 600; color: #6b7280; }
.card-amount { font-size: 19px; font-weight: 800; letter-spacing: -0.02em; line-height: 1.2; }
.card-sub    { font-size: 11px; color: #9ca3af; }
.body-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; align-items: start; }
.col { display: flex; flex-direction: column; gap: 6px; }
.col-title {
  font-size: 11px; font-weight: 700; color: #6b7280;
  letter-spacing: 0.06em; text-transform: uppercase;
  display: flex; align-items: center; gap: 8px; margin-bottom: 2px;
}
.col-title::after { content: ''; flex: 1; height: 1px; background: #e5e7eb; }
.group { display: flex; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
.group-bar   { width: 4px; flex-shrink: 0; }
.group-inner { flex: 1; min-width: 0; padding: 8px 10px; }
.group-head {
  display: flex; align-items: center; gap: 5px;
  padding-bottom: 6px; border-bottom: 1px solid #f3f4f6; margin-bottom: 4px;
}
.group-icon  { font-size: 14px; line-height: 1; flex-shrink: 0; }
.group-name  { font-size: 12px; font-weight: 700; color: #374151; flex: 1; min-width: 0; }
.group-total { font-size: 12px; font-weight: 800; flex-shrink: 0; }
.item-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 3px 0; border-bottom: 1px solid #f9fafb;
}
.item-row:last-child { border-bottom: none; }
.item-name { font-size: 11px; color: #6b7280; flex: 1; min-width: 0; }
.item-val  { font-size: 11px; font-weight: 600; color: #374151; flex-shrink: 0; white-space: nowrap; }
.won { font-size: 10px; margin-right: 1px; }
.col-total {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 10px; background: #fff; border-radius: 8px;
  border-top: 2px solid #1a1f3c; font-size: 12px; font-weight: 800; color: #1a1f3c;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}
</style>
