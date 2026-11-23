<script setup>
import { computed } from 'vue'
import { useReportStore } from '@/stores/report'
import FinancialGaugeEcharts from '@/components/charts/FinancialGaugeEcharts.vue'

const reportStore = useReportStore()

// 서버가 실제로 계산한 리포트 본문은 지표별 평면 키로 오고, 시드 글·목업은 groups 형태다.
// 그리기 직전에 두 모양을 groups 로 맞춘다 — 이미 저장된 글도 다시 만들지 않고 그대로 그려진다
const fi = computed(() => toIndicatorGroups(
  props.data ?? reportStore.reports?.['financial-indicators'],
))

const GROUP_LABEL = { cashflow: '현금흐름', assets: '자산', risk: '리스크 대비' }

// 표시 순서는 라벨 맵 순서로 고정한다 — 글에 첨부된 스냅샷은 jsonb 로 저장돼
// 키 순서가 보존되지 않아 들어온 순서를 믿으면 리스크 대비가 먼저 뜨기도 한다
const GROUP_ORDER = Object.keys(GROUP_LABEL)

const groups = computed(() => {
  const data = fi.value
  if (!data) return []
  return Object.entries(data.groups ?? {}).map(([key, items]) => ({
    key,
    label: GROUP_LABEL[key] ?? key,
    items,
  }))
})
</script>

<template>
  <div class="slide">
    <div
      v-for="group in groups"
      :key="group.key"
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
</template>

<style scoped>
.slide {
  width: 100%;
  min-height: 100%;
  padding: 14px 16px;
  box-sizing: border-box;
  background: #f5f5f2;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
}
.indicator-group { display: flex; flex-direction: column; gap: 8px; }
.group-title {
  font-size: 11px; font-weight: 700; color: #888;
  text-transform: uppercase; letter-spacing: 0.6px;
}
.gauge-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
}
</style>
