<script setup>
import { computed } from 'vue'
import { useReportStore } from '@/stores/report'
import { fmtKRW } from '@/utils/format'

// 글에 첨부된 스냅샷을 받는다. 없으면 기존처럼 store 를 본다(대시보드·AI 화면 동작 유지)
const props = defineProps({
  data: { type: Object, default: null },
})

const reportStore = useReportStore()

const ASSET_VISUAL = {
  cash:    { icon: '💳', accent: '#3b82f6' },
  tangible:{ icon: '📦', accent: '#f97316' },
  invest:  { icon: '📈', accent: '#6366f1' },
  estate:  { icon: '🏠', accent: '#0891b2' },
  pension: { icon: '🌂', accent: '#0d9488' },
}

const LIABILITY_VISUAL = {
  short: { icon: '⚡', accent: '#f59e0b' },
  long:  { icon: '🏚', accent: '#ef4444' },
}

const bs = computed(() => props.data ?? reportStore.reports?.asset_statement)

const assetGroups      = computed(() =>
  (bs.value?.asset_groups ?? []).map(g => ({ ...g, ...ASSET_VISUAL[g.key] }))
)
const liabilityGroups  = computed(() =>
  (bs.value?.liability_groups ?? []).map(g => ({ ...g, ...LIABILITY_VISUAL[g.key] }))
)

const fmt = fmtKRW
</script>

<template>
  <div class="slide">

    <!-- ── 상단 요약 카드 ── -->
    <div class="summary-row">
      <div class="summary-card card--assets">
        <span class="card-label">총 자산</span>
        <span class="card-amount" style="color:#1d4ed8">{{ fmt(bs?.total_assets) }}</span>
        <span class="card-sub">{{ bs?.asset_groups?.reduce((s, g) => s + g.items.length, 0) }}개 항목</span>
      </div>
      <div class="summary-card card--liabilities">
        <span class="card-label">총 부채</span>
        <span class="card-amount" style="color:#dc2626">{{ fmt(bs?.total_liabilities) }}</span>
        <span class="card-sub">{{ bs?.liability_groups?.reduce((s, g) => s + g.items.length, 0) }}개 항목</span>
      </div>
      <div class="summary-card card--networth">
        <span class="card-label">순자산 (NET WORTH)</span>
        <span class="card-amount" style="color:#5e5ce6">{{ fmt(bs?.net_worth) }}</span>
        <span class="card-sub">부채비율 {{ bs?.debt_ratio }}%</span>
      </div>
    </div>

    <!-- ── 본문 2열 그리드 ── -->
    <div class="body-grid">

      <!-- 자산 컬럼 -->
      <div class="col">
        <div class="col-title">자산</div>
        <div v-for="group in assetGroups" :key="group.key" class="group">
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
          <span>자산 총계</span>
          <span style="color:#1d4ed8">{{ fmt(bs?.total_assets) }}</span>
        </div>
      </div>

      <!-- 부채 컬럼 -->
      <div class="col">
        <div class="col-title">부채</div>
        <div v-for="group in liabilityGroups" :key="group.key" class="group">
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
          <span>부채 총계</span>
          <span style="color:#dc2626">{{ fmt(bs?.total_liabilities) }}</span>
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
  background: #f8fafc;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* ── 요약 카드 ── */
.summary-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.summary-card {
  background: #fff; border-radius: 12px; padding: 14px 16px 12px;
  display: flex; flex-direction: column; gap: 4px;
  box-shadow: 0 1px 6px rgba(0,0,0,0.07);
  border-left: 4px solid transparent;
}
.card--assets      { border-left-color: #3b82f6; }
.card--liabilities { border-left-color: #ef4444; }
.card--networth    { border-left-color: #5e5ce6; }
.card-label  { font-size: 11px; font-weight: 600; color: #6b7280; }
.card-amount { font-size: 18px; font-weight: 800; letter-spacing: -0.02em; line-height: 1.3; }
.card-sub    { font-size: 11px; color: #9ca3af; }

/* ── 본문 2열 ── */
.body-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; align-items: start; }
.col { display: flex; flex-direction: column; gap: 6px; }
.col-title {
  font-size: 11px; font-weight: 700; color: #6b7280;
  letter-spacing: 0.08em; text-transform: uppercase;
  display: flex; align-items: center; gap: 8px; margin-bottom: 2px;
}
.col-title::after { content: ''; flex: 1; height: 1px; background: #e2e8f0; }

/* ── 그룹 카드 ── */
.group { display: flex; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
.group-bar   { width: 4px; flex-shrink: 0; }
.group-inner { flex: 1; min-width: 0; padding: 8px 10px; }
.group-head {
  display: flex; align-items: center; gap: 6px;
  padding-bottom: 6px; border-bottom: 1px solid #f1f5f9; margin-bottom: 4px;
}
.group-icon  { font-size: 14px; line-height: 1; flex-shrink: 0; }
.group-name  { font-size: 12px; font-weight: 700; color: #1e293b; flex: 1; min-width: 0; }
.group-total { font-size: 12px; font-weight: 800; flex-shrink: 0; }

/* ── 항목 행 ── */
.item-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 3px 0; border-bottom: 1px solid #f8fafc;
}
.item-row:last-child { border-bottom: none; }
.item-name { font-size: 11px; color: #64748b; flex: 1; min-width: 0; }
.item-val  { font-size: 11px; font-weight: 600; color: #334155; flex-shrink: 0; white-space: nowrap; }

/* ── 컬럼 합계 ── */
.col-total {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px; background: #fff; border-radius: 8px;
  font-size: 12px; font-weight: 800; color: #1e293b;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  border: 1px solid #e2e8f0;
}
</style>
