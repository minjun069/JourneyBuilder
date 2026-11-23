<template>
  <div class="gauge-card">
    <div class="gauge-chart-area">
      <VChart v-if="indicator.value != null" :option="chartOption" autoresize />
      <div v-else class="unavailable-value" aria-label="지표 계산 불가">—</div>
    </div>
    <div class="gauge-footer">
      <div class="gauge-label">{{ indicator.label }}</div>
      <div class="gauge-desc">{{ indicator.description }}</div>
      <span
        class="gauge-status"
        :style="{ backgroundColor: currentZone.color + '22', color: currentZone.color }"
      >
        {{ currentZone.status }}
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { GaugeChart } from 'echarts/charts'
import { TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'

use([CanvasRenderer, GaugeChart, TooltipComponent])

const props = defineProps({
  indicator: { type: Object, required: true },
})

const currentZone = computed(() => {
  const { value, zones } = props.indicator
  if (value == null) return { color: '#94a3b8', status: '판정 불가' }
  if (props.indicator.status) return { color: props.indicator.color, status: props.indicator.status }
  if (value < zones[0].from) return zones[0]
  return zones.find((z) => value >= z.from && value < z.to) || zones[zones.length - 1]
})

// ECharts gauge axisLine.color takes [[normalizedEnd, color], ...]
const normalizedZones = computed(() => {
  const { min, max, zones } = props.indicator
  const range = max - min
  return zones.map((z) => [(Math.min(z.to, max) - min) / range, z.color])
})

const chartOption = computed(() => ({
  series: [
    {
      type: 'gauge',
      min: props.indicator.min,
      max: props.indicator.max,
      startAngle: 200,
      endAngle: -20,
      radius: '90%',
      center: ['50%', '60%'],
      axisLine: {
        lineStyle: {
          width: 14,
          color: normalizedZones.value,
        },
      },
      pointer: {
        length: '55%',
        width: 4,
        itemStyle: { color: '#444' },
      },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      detail: {
        valueAnimation: true,
        formatter: `{value}${props.indicator.unit}`,
        fontSize: 13,
        fontWeight: 'bold',
        color: '#1a1a1a',
        offsetCenter: [0, '50%'],
      },
      data: [{ value: props.indicator.value }],
      animation: true,
      animationDuration: 600,
    },
  ],
}))
</script>

<style scoped>
.gauge-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  background: #fff;
  border: 1px solid #f0f0f0;
  border-radius: 12px;
  padding: 8px 12px 14px;
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.04);
  min-width: 0;
}

.gauge-chart-area {
  width: 150px;
  height: 100px;
}

.gauge-footer {
  margin-top: 4px;
  text-align: center;
  width: 100%;
}

.gauge-label {
  font-size: 12px;
  font-weight: 700;
  color: #333;
  margin-bottom: 2px;
}

.gauge-desc {
  font-size: 10px;
  color: #aaa;
  margin-bottom: 6px;
}

.gauge-status {
  display: inline-block;
  font-size: 11px;
  font-weight: 700;
  padding: 2px 10px;
  border-radius: 20px;
}
.unavailable-value { height: 100%; display: grid; place-items: center; font-size: 26px; color: #94a3b8; }
</style>
