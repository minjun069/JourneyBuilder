<script setup>
import { computed } from 'vue'
import { useReportStore } from '@/stores/report'
import FinancialGaugeEcharts from '@/components/charts/FinancialGaugeEcharts.vue'
import { makeIndicator } from '@/components/charts/financialIndicatorSchema'

const reportStore = useReportStore()

const fi = computed(() => reportStore.reports?.['financial-indicators'])

const GROUP_LABEL = { cashflow: '현금흐름', assets: '자산', risk: '리스크 대비' }

const groups = computed(() => {
  const data = fi.value
  if (!data) return []
  const age = data.age
  return Object.entries(data.groups).map(([key, items]) => ({
    key,
    label: GROUP_LABEL[key] ?? key,
    items: items.map(i => makeIndicator(i.key, i.value, age)),
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
