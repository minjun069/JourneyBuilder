<template>
  <div class="chart-wrapper">
    <VChart :option="chartOption" autoresize />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { PieChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'

use([CanvasRenderer, PieChart, TooltipComponent, LegendComponent])

const props = defineProps({
  breakdown: { type: Array, required: true },
})

const total = computed(() => props.breakdown.reduce((s, b) => s + b.amount, 0))

const chartOption = computed(() => ({
  tooltip: {
    trigger: 'item',
    formatter: (p) => {
      const pct = ((p.value / total.value) * 100).toFixed(1)
      return `${p.name}<br/>${(p.value / 100000000).toFixed(2)}억원 (${pct}%)`
    },
  },
  legend: {
    orient: 'vertical',
    right: '0%',
    top: 'center',
    itemWidth: 10,
    itemHeight: 10,
    textStyle: { fontSize: 12, color: '#444' },
    formatter: (name) => {
      const item = props.breakdown.find((b) => b.category === name)
      if (!item) return name
      const pct = ((item.amount / total.value) * 100).toFixed(1)
      return `${name}  ${pct}%`
    },
  },
  series: [
    {
      type: 'pie',
      radius: ['42%', '70%'],
      center: ['35%', '50%'],
      avoidLabelOverlap: false,
      label: {
        show: true,
        position: 'center',
        formatter: () => `${(total.value / 100000000).toFixed(1)}억\n총자산`,
        fontSize: 13,
        color: '#555',
        lineHeight: 20,
      },
      emphasis: {
        label: { show: false },
        itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.15)' },
      },
      labelLine: { show: false },
      data: props.breakdown.map((b) => ({
        name: b.category,
        value: b.amount,
        itemStyle: { color: b.color },
      })),
    },
  ],
}))
</script>

<style scoped>
.chart-wrapper {
  height: 220px;
}
</style>
