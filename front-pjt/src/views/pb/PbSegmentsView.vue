<template>
  <div class="segments">
    <div class="page-head">
      <div>
        <h2 class="page-title">세그먼트 분석</h2>
        <p class="page-sub">고객군을 6개 축으로 나눠 인사이트를 찾습니다</p>
      </div>
      <div class="refresh-area">
        <span v-if="generatedAt" class="refreshed-at">{{ refreshedLabel }} 갱신</span>
        <button type="button" class="btn-refresh" :disabled="loading" @click="load">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
            <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2.5v3h-3" />
          </svg>
          새로고침
        </button>
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
            연령 × 순자산
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
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, ScatterChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { usePolling } from '@/composables/usePolling'
import { useAdminStore } from '@/stores/admin'
import { formatEok } from '@/utils/money'
import { AXIS_LABELS, AXIS_QUERY_PARAM, bucketLabel } from './pbLabels'
import { AXIS_TEXT, CHART_COLORS, GRADE_COLORS, SPLIT_LINE } from './pbTheme'

use([CanvasRenderer, BarChart, ScatterChart, GridComponent, TooltipComponent, LegendComponent])

const NEED_COLORS = {
  은퇴: CHART_COLORS[0], 투자: CHART_COLORS[1], 주택자금: CHART_COLORS[2],
  교육자금: CHART_COLORS[3], 보장: CHART_COLORS[4], 기타: CHART_COLORS[5],
}

const router = useRouter()
const adminStore = useAdminStore()
const {
  segments,
  segmentsMeta,
  segmentsLoading: loading,
  segmentsError,
} = storeToRefs(adminStore)

const activeAxis = ref('age_band')

const axes = computed(() => segments.value?.axes ?? null)
const indicatorRisk = computed(() => segments.value?.indicator_risk ?? [])
const scatter = computed(() => segments.value?.scatter ?? [])
const generatedAt = computed(() => segmentsMeta.value.generated_at)
const error = computed(() => {
  if (!segmentsError.value) return ''
  return segmentsError.value.response?.status === 403
    ? 'PB 권한이 필요한 화면입니다.'
    : '세그먼트 데이터를 불러오지 못했습니다.'
})

const refreshedLabel = computed(() =>
  generatedAt.value ? new Date(generatedAt.value).toLocaleTimeString('ko-KR') : '')

const activeBuckets = computed(() => axes.value?.[activeAxis.value] ?? [])

const axisBarOption = computed(() => ({
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: '{b}: {c}명' },
  grid: { left: 8, right: 16, top: 14, bottom: 0, containLabel: true },
  xAxis: {
    type: 'category',
    data: activeBuckets.value.map(b => bucketLabel(activeAxis.value, b.bucket)),
    axisLabel: { ...AXIS_TEXT, interval: 0 },
    axisLine: { lineStyle: { color: '#e6e6f0' } },
    axisTick: { show: false },
  },
  yAxis: { type: 'value', minInterval: 1, axisLabel: AXIS_TEXT, splitLine: SPLIT_LINE },
  series: [{
    type: 'bar',
    barWidth: '52%',
    itemStyle: { color: '#8280ee', borderRadius: [5, 5, 0, 0] },
    emphasis: { itemStyle: { color: '#5e5ce6' } },
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
        const d = p?.data?.meta
        if (!d) return ''
        return `${d.name}<br/>${d.age}세 · 순자산 ${formatEok(d.net_worth)}<br/>월 잉여 ${formatEok(d.monthly_surplus)}`
      },
    },
    legend: { top: 0, icon: 'circle', itemWidth: 9, itemHeight: 9, textStyle: { fontSize: 11, color: '#565a6e' } },
    grid: { left: 8, right: 20, top: 32, bottom: 0, containLabel: true },
    xAxis: {
      type: 'value', name: '나이', min: 18, max: 80,
      nameTextStyle: AXIS_TEXT, axisLabel: AXIS_TEXT, splitLine: SPLIT_LINE,
    },
    yAxis: {
      type: 'value', name: '순자산',
      nameTextStyle: AXIS_TEXT, splitLine: SPLIT_LINE,
      axisLabel: { ...AXIS_TEXT, formatter: value => `${(value / 100_000_000).toFixed(0)}억` },
    },
    series: [...groups.entries()].map(([need, points]) => ({
      name: need,
      type: 'scatter',
      itemStyle: { color: NEED_COLORS[need] ?? '#d0d1de', opacity: 0.78 },
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
  legend: { top: 0, icon: 'circle', itemWidth: 9, itemHeight: 9, textStyle: { fontSize: 12, color: '#565a6e' } },
  grid: { left: 8, right: 16, top: 30, bottom: 0, containLabel: true },
  xAxis: { type: 'value', minInterval: 1, axisLabel: AXIS_TEXT, splitLine: SPLIT_LINE },
  yAxis: {
    type: 'category',
    data: indicatorRisk.value.map(item => item.key),
    axisLabel: { ...AXIS_TEXT, fontSize: 11.5 },
    axisLine: { show: false },
    axisTick: { show: false },
  },
  series: ['양호', '주의', '위험'].map((grade, i) => ({
    name: grade,
    type: 'bar',
    stack: 'total',
    barWidth: 13,
    itemStyle: {
      color: GRADE_COLORS[grade],
      borderRadius: i === 2 ? [0, 3, 3, 0] : i === 0 ? [3, 0, 0, 3] : 0,
    },
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
  await adminStore.loadSegments()
}

load()
usePolling(load)
</script>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 20px;
}
.page-title { font-size: 22px; font-weight: 800; letter-spacing: -0.3px; color: #191a2b; margin: 0; }
.page-sub { font-size: 13px; color: #9a9cb0; margin: 5px 0 0; }
.refresh-area { display: flex; align-items: center; gap: 12px; }
.refreshed-at { font-size: 12px; color: #a2a4b8; font-variant-numeric: tabular-nums; }
.btn-refresh {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid #e3e2f4;
  background: #fff;
  color: #55538a;
  font-size: 13px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: 9px;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.btn-refresh svg { width: 13px; height: 13px; }
.btn-refresh:hover:not(:disabled) { border-color: #c5c3ee; box-shadow: 0 1px 6px rgba(94, 92, 230, 0.12); }
.btn-refresh:disabled { opacity: 0.5; }

.axis-bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
.axis-chip {
  border: 1px solid #e6e6f0;
  background: #fff;
  color: #565a6e;
  font-size: 13px;
  font-weight: 600;
  padding: 8px 16px;
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.axis-chip:hover { border-color: #c5c3ee; color: #5e5ce6; }
.axis-chip.active {
  background: #5e5ce6;
  border-color: #5e5ce6;
  color: #fff;
  box-shadow: 0 2px 8px rgba(94, 92, 230, 0.28);
}

.chart-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
  gap: 14px;
  margin-bottom: 14px;
}

.card {
  background: #fff;
  border: 1px solid #ebebf2;
  border-radius: 14px;
  padding: 20px 24px;
  box-shadow: 0 1px 3px rgba(25, 26, 43, 0.04);
}
.card-title { font-size: 15px; font-weight: 700; color: #191a2b; margin: 0 0 12px; }
.title-sub { font-size: 12px; font-weight: 400; color: #a2a4b8; margin-left: 8px; }

.chart { height: 300px; }
.chart.stack { height: 280px; }

.placeholder { color: #9a9cb0; font-size: 14px; }
.placeholder.error { color: #d64550; }
</style>
