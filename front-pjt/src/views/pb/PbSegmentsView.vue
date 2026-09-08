<template>
  <div class="segments">
    <div class="page-head">
      <h2 class="page-title">세그먼트 분석</h2>
      <div class="refresh-area">
        <span v-if="generatedAt" class="refreshed-at">마지막 갱신 {{ refreshedLabel }}</span>
        <button type="button" class="btn-refresh" :disabled="loading" @click="load">새로고침</button>
      </div>
    </div>

    <p v-if="error" class="placeholder error">{{ error }}</p>

    <template v-else>
      <div class="axis-bar">
        <button
          v-for="(label, axis) in AXIS_LABELS" :key="axis" type="button"
          class="axis-chip" :class="{ active: activeAxis === axis }"
          @click="activeAxis = axis"
        >
          {{ label }}
        </button>
      </div>

      <div class="chart-row">
        <section class="card">
          <h3 class="card-title">
            {{ AXIS_LABELS[activeAxis] }} 분포
            <span class="title-sub">막대 클릭 → 해당 고객 리스트</span>
          </h3>
          <VChart class="chart" :option="axisBarOption" autoresize @click="onBucketClick" />
        </section>

        <section class="card">
          <h3 class="card-title">
            연령 × 순자산 산점도
            <span class="title-sub">색 = 니즈 · 크기 = 월 잉여 · 점 클릭 → 고객 상세</span>
          </h3>
          <VChart class="chart" :option="scatterOption" autoresize @click="onScatterClick" />
        </section>
      </div>

      <section class="card">
        <h3 class="card-title">재무지표 7종 위험도 <span class="title-sub">양호 / 주의 / 위험 고객 수</span></h3>
        <VChart class="chart stack" :option="indicatorStackOption" autoresize />
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, ScatterChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { getSegments } from '@/api/admin'
import { usePolling } from '@/composables/usePolling'
import { formatEok } from '@/utils/money'
import { AXIS_LABELS, AXIS_QUERY_PARAM, bucketLabel } from './pbLabels'

use([CanvasRenderer, BarChart, ScatterChart, GridComponent, TooltipComponent, LegendComponent])

const AXIS_BAR_COLOR = '#5e5ce6'
const NEED_COLORS = {
  은퇴: '#5e5ce6', 투자: '#4bc0c0', 주택자금: '#ff9f40',
  교육자금: '#ff6384', 보장: '#36a2eb', 기타: '#9ca3af',
}
const GRADE_COLORS = { 양호: '#66bb6a', 주의: '#ffa726', 위험: '#ef5350' }

const router = useRouter()

const axes = ref(null)
const indicatorRisk = ref([])
const scatter = ref([])
const generatedAt = ref(null)
const loading = ref(false)
const error = ref('')
const activeAxis = ref('age_band')

const refreshedLabel = computed(() =>
  generatedAt.value ? new Date(generatedAt.value).toLocaleTimeString('ko-KR') : '')

const activeBuckets = computed(() => axes.value?.[activeAxis.value] ?? [])

const axisBarOption = computed(() => ({
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: '{b}: {c}명' },
  grid: { left: 8, right: 16, top: 12, bottom: 0, containLabel: true },
  xAxis: {
    type: 'category',
    data: activeBuckets.value.map(b => bucketLabel(activeAxis.value, b.bucket)),
    axisLabel: { fontSize: 11, interval: 0 },
  },
  yAxis: { type: 'value', minInterval: 1 },
  series: [{
    type: 'bar',
    barWidth: '55%',
    itemStyle: { color: AXIS_BAR_COLOR, borderRadius: [4, 4, 0, 0] },
    data: activeBuckets.value.map(b => ({ value: b.count, bucket: b.bucket })),
  }],
}))

// 니즈별 시리즈로 나눠 색을 입힌 산점도 (S7.1)
const scatterOption = computed(() => {
  const groups = new Map()
  for (const point of scatter.value) {
    const need = point.need ?? '미입력'
    if (!groups.has(need)) groups.set(need, [])
    groups.get(need).push(point)
  }
  return {
    tooltip: {
      trigger: 'item',
      formatter: p => {
        const d = p.data.meta
        return `${d.name}<br/>${d.age}세 · 순자산 ${formatEok(d.net_worth)}<br/>월 잉여 ${formatEok(d.monthly_surplus)}`
      },
    },
    legend: { top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 8, right: 20, top: 30, bottom: 0, containLabel: true },
    xAxis: { type: 'value', name: '나이', min: 18, max: 80 },
    yAxis: {
      type: 'value', name: '순자산',
      axisLabel: { formatter: value => `${(value / 100_000_000).toFixed(0)}억` },
    },
    series: [...groups.entries()].map(([need, points]) => ({
      name: need,
      type: 'scatter',
      itemStyle: { color: NEED_COLORS[need] ?? '#d7d7e8', opacity: 0.75 },
      symbolSize: (value, params) => {
        const surplus = Math.abs(params?.data?.meta?.monthly_surplus ?? 0)
        return 8 + Math.min(16, surplus / 250_000)
      },
      data: points.map(point => ({
        value: [point.age, point.net_worth],
        meta: point,
      })),
    })),
  }
})

const indicatorStackOption = computed(() => ({
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
  legend: { top: 0, textStyle: { fontSize: 12 } },
  grid: { left: 8, right: 16, top: 28, bottom: 0, containLabel: true },
  xAxis: { type: 'value', minInterval: 1 },
  yAxis: {
    type: 'category',
    data: indicatorRisk.value.map(item => item.key),
    axisLabel: { fontSize: 11 },
  },
  series: ['양호', '주의', '위험'].map(grade => ({
    name: grade,
    type: 'bar',
    stack: 'total',
    barWidth: 14,
    itemStyle: { color: GRADE_COLORS[grade] },
    data: indicatorRisk.value.map(item => item[grade]),
  })),
}))

// FR-07 크로스 필터: 막대 클릭 → 조건이 걸린 고객 리스트로 이동
function onBucketClick(params) {
  const bucket = params?.data?.bucket
  if (!bucket) return
  const param = AXIS_QUERY_PARAM[activeAxis.value]
  router.push(`/pb/customers?${param}=${encodeURIComponent(bucket)}`)
}

function onScatterClick(params) {
  const userId = params?.data?.meta?.user_id
  if (userId) router.push(`/pb/customers/${userId}`)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const body = await getSegments()
    axes.value = body.data.axes
    indicatorRisk.value = body.data.indicator_risk
    scatter.value = body.data.scatter
    generatedAt.value = body.meta.generated_at
  } catch (err) {
    error.value = err.response?.status === 403
      ? 'PB 권한이 필요한 화면입니다.'
      : '세그먼트 데이터를 불러오지 못했습니다.'
  } finally {
    loading.value = false
  }
}

load()
usePolling(load)
</script>

<style scoped>
.page-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 18px; }
.page-title { font-size: 22px; font-weight: 700; color: #1a1a2e; margin: 0; }
.refresh-area { display: flex; align-items: center; gap: 10px; }
.refreshed-at { font-size: 12.5px; color: #888; }
.btn-refresh {
  border: 1px solid #d9d8f5; background: #fff; color: #5e5ce6;
  font-size: 13px; font-weight: 600; padding: 7px 14px; border-radius: 8px; cursor: pointer;
}
.btn-refresh:hover:not(:disabled) { background: #f3f3ff; }
.btn-refresh:disabled { opacity: 0.5; }

.axis-bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
.axis-chip {
  border: 1px solid #e0dff7;
  background: #fff;
  color: #555;
  font-size: 13px;
  font-weight: 600;
  padding: 8px 16px;
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.axis-chip:hover { border-color: #5e5ce6; color: #5e5ce6; }
.axis-chip.active { background: #5e5ce6; border-color: #5e5ce6; color: #fff; }

.chart-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}

.card {
  background: #fff;
  border: 1px solid #ebebf5;
  border-radius: 14px;
  padding: 20px 24px;
}
.card-title { font-size: 16px; font-weight: 700; color: #1a1a2e; margin: 0 0 12px; }
.title-sub { font-size: 12px; font-weight: 400; color: #999; margin-left: 8px; }

.chart { height: 300px; }
.chart.stack { height: 280px; }

.placeholder { color: #888; font-size: 14px; }
.placeholder.error { color: #d43a3a; }
</style>
