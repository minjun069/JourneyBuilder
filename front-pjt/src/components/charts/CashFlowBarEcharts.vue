<template>
  <div class="chart-wrapper">
    <VChart :option="chartOption" autoresize />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'

use([CanvasRenderer, BarChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent])

const props = defineProps({
  monthlyHistory: { type: Array, required: true },
  selectedPeriod: { type: String, required: true },
})

function shortLabel(period) {
  return period.replace('년 ', '/').replace('월', '')
}

const chartOption = computed(() => {
  const selectedIdx = props.monthlyHistory.findIndex((m) => m.period === props.selectedPeriod)
  const labels = props.monthlyHistory.map((m) => shortLabel(m.period))

  const makeBarData = (key, baseColor, highlightColor) =>
    props.monthlyHistory.map((m, i) => ({
      value: m[key],
      itemStyle: { color: i === selectedIdx ? highlightColor : baseColor, borderRadius: [4, 4, 0, 0] },
    }))

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const label = params[0].axisValue
        return params
          .map(
            (p) =>
              `${p.marker}${p.seriesName}: ${(p.value / 10000).toLocaleString()}만원`
          )
          .join('<br/>')
          .replace(/^/, `${label}<br/>`)
      },
    },
    legend: {
      top: 0,
      data: ['유입', '유출'],
      textStyle: { fontSize: 12, color: '#444' },
    },
    grid: { left: '2%', right: '2%', bottom: '3%', top: '36px', containLabel: true },
    xAxis: {
      type: 'category',
      data: labels,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { fontSize: 11, color: '#888' },
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        formatter: (val) => `${(val / 10000).toLocaleString()}만`,
        fontSize: 11,
        color: '#888',
      },
      splitLine: { lineStyle: { color: '#f5f5f5' } },
    },
    series: [
      {
        name: '유입',
        type: 'bar',
        barMaxWidth: 28,
        data: makeBarData('inflow', 'rgba(66,165,245,0.65)', '#1565c0'),
      },
      {
        name: '유출',
        type: 'bar',
        barMaxWidth: 28,
        data: makeBarData('outflow', 'rgba(239,83,80,0.55)', '#b71c1c'),
      },
    ],
  }
})
</script>

<style scoped>
.chart-wrapper {
  height: 240px;
}
</style>
