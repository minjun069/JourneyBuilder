<template>
  <div class="overview">
    <div class="page-head">
      <div>
        <h2 class="page-title">개요</h2>
        <p class="page-sub">전체 고객 현황을 한눈에 파악합니다</p>
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
      <!-- FR-06 KPI 4종 -->
      <section class="kpi-row">
        <template v-if="kpi">
          <button type="button" class="kpi-card" @click="router.push('/pb/customers')">
            <span class="kpi-icon" style="background:#efeffd; color:#5e5ce6">
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6">
                <circle cx="7.5" cy="6.5" r="3" />
                <path d="M2.5 16.5c0-2.8 2.2-5 5-5s5 2.2 5 5" />
                <path d="M13.5 4.2a3 3 0 0 1 0 4.6M15.2 11.9c1.4.8 2.3 2.3 2.3 4.1" />
              </svg>
            </span>
            <span class="kpi-body">
              <span class="kpi-label">총 고객</span>
              <span class="kpi-value">{{ kpi.customer_count }}<small>명</small></span>
            </span>
          </button>
          <button type="button" class="kpi-card" @click="router.push('/pb/customers?status=completed')">
            <span class="kpi-icon" style="background:#e8f6ef; color:#23855c">
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="m4 10.5 4 4 8-9" />
              </svg>
            </span>
            <span class="kpi-body">
              <span class="kpi-label">상담 완료율</span>
              <span class="kpi-value">{{ kpi.completed_rate }}<small>%</small></span>
            </span>
          </button>
          <div class="kpi-card static">
            <span class="kpi-icon" style="background:#e7f0fd; color:#3577d4">
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
                <path d="M3 15.5 7.5 10l3.5 3 5.5-7" />
                <path d="M13 6h3.5V9.5" />
              </svg>
            </span>
            <span class="kpi-body">
              <span class="kpi-label">총 순자산</span>
              <span class="kpi-value accent">{{ formatEok(kpi.total_net_worth) }}</span>
            </span>
          </div>
          <button type="button" class="kpi-card" @click="router.push('/pb/customers?health=risky')">
            <span class="kpi-icon" style="background:#fdebec; color:#d64550">
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
                <path d="M10 3 2.5 16h15L10 3ZM10 8.5v3.5M10 14.6v.4" />
              </svg>
            </span>
            <span class="kpi-body">
              <span class="kpi-label">위험 고객</span>
              <span class="kpi-value" :class="{ danger: kpi.risk_customer_count > 0 }">
                {{ kpi.risk_customer_count }}<small>명</small>
              </span>
            </span>
          </button>
        </template>
        <template v-else>
          <div v-for="n in 4" :key="n" class="kpi-card static"><span class="sk sk-kpi" /></div>
        </template>
      </section>

      <!-- FR-06 대표 세그먼트 차트 2개 -->
      <div class="chart-row">
        <section class="card">
          <h3 class="card-title">주요 니즈 분포 <span class="title-sub">조각 클릭 → 고객 리스트</span></h3>
          <VChart class="chart" :option="needDonutOption" autoresize @click="onNeedClick" />
        </section>
        <section class="card">
          <h3 class="card-title">재무지표 위험도</h3>
          <VChart class="chart tall" :option="indicatorStackOption" autoresize />
        </section>
      </div>

      <!-- FR-06 최근 상담 활동 -->
      <section class="card">
        <h3 class="card-title">최근 상담 활동</h3>
        <ul class="activity-list" v-if="activities.length">
          <li v-for="act in activities" :key="act.session_id" class="activity-row"
              @click="router.push(`/pb/customers/${act.user_id}`)">
            <span class="avatar" :style="avatarStyle(act.name)">{{ initialOf(act.name) }}</span>
            <span class="act-main">
              <span class="act-name">{{ act.name }}</span>
              <span class="act-desc">{{ STATUS_LABELS[act.status] ?? act.status }}</span>
            </span>
            <span class="act-status"><i class="dot" :class="act.status" /></span>
            <span class="act-time">{{ formatDateTime(act.completed_at ?? act.created_at) }}</span>
          </li>
        </ul>
        <p v-else class="placeholder">최근 활동이 없습니다</p>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { getSegments, getSummary } from '@/api/admin'
import { usePolling } from '@/composables/usePolling'
import { formatEok } from '@/utils/money'
import { bucketLabel } from './pbLabels'
import { AXIS_TEXT, CHART_COLORS, GRADE_COLORS, SPLIT_LINE, avatarStyle, initialOf } from './pbTheme'

use([CanvasRenderer, PieChart, BarChart, GridComponent, TooltipComponent, LegendComponent])

const STATUS_LABELS = {
  completed: '상담을 완료했습니다', in_progress: '상담을 진행하고 있습니다',
  onboarding: '상담을 시작했습니다', abandoned: '상담에서 이탈했습니다',
}

const router = useRouter()

const kpi = ref(null)
const activities = ref([])
const needBuckets = ref([])
const indicatorRisk = ref([])
const generatedAt = ref(null)
const loading = ref(false)
const error = ref('')

const refreshedLabel = computed(() =>
  generatedAt.value ? new Date(generatedAt.value).toLocaleTimeString('ko-KR') : '')

const needDonutOption = computed(() => ({
  tooltip: { trigger: 'item', formatter: '{b}: {c}명 ({d}%)' },
  legend: {
    orient: 'vertical', right: 0, top: 'center',
    itemWidth: 9, itemHeight: 9, icon: 'circle',
    textStyle: { fontSize: 12, color: '#565a6e' },
  },
  series: [{
    type: 'pie',
    radius: ['46%', '72%'],
    center: ['36%', '50%'],
    padAngle: 2,
    itemStyle: { borderRadius: 5 },
    label: { show: false },
    data: needBuckets.value
      .filter(b => b.count > 0)
      .map((b, i) => ({
        name: bucketLabel('need', b.bucket),
        value: b.count,
        bucket: b.bucket,
        itemStyle: { color: CHART_COLORS[i % CHART_COLORS.length] },
      })),
  }],
}))

const indicatorStackOption = computed(() => ({
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
  legend: { top: 0, icon: 'circle', itemWidth: 9, itemHeight: 9, textStyle: { fontSize: 12, color: '#565a6e' } },
  grid: { left: 8, right: 16, top: 30, bottom: 0, containLabel: true },
  xAxis: { type: 'value', minInterval: 1, axisLabel: AXIS_TEXT, splitLine: SPLIT_LINE },
  yAxis: {
    type: 'category',
    data: indicatorRisk.value.map(item => item.key.replace('지표', '')),
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

function onNeedClick(params) {
  const bucket = params?.data?.bucket
  if (bucket) router.push(`/pb/customers?need=${encodeURIComponent(bucket)}`)
}

function formatDateTime(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString('ko-KR', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [summaryBody, segmentsBody] = await Promise.all([getSummary(), getSegments()])
    kpi.value = summaryBody.data.kpi
    activities.value = summaryBody.data.recent_activities
    needBuckets.value = segmentsBody.data.axes.need
    indicatorRisk.value = segmentsBody.data.indicator_risk
    generatedAt.value = summaryBody.meta.generated_at
  } catch (err) {
    error.value = err.response?.status === 403
      ? 'PB 권한이 필요한 화면입니다.'
      : '개요 데이터를 불러오지 못했습니다.'
  } finally {
    loading.value = false
  }
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

.kpi-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 14px;
  margin-bottom: 16px;
}
.kpi-card {
  display: flex;
  align-items: center;
  gap: 14px;
  text-align: left;
  background: #fff;
  border: 1px solid #ebebf2;
  border-radius: 14px;
  padding: 18px 20px;
  cursor: pointer;
  font-family: inherit;
  transition: box-shadow 0.15s ease, transform 0.15s ease;
}
.kpi-card:hover:not(.static) { box-shadow: 0 4px 16px rgba(94, 92, 230, 0.1); transform: translateY(-1px); }
.kpi-card.static { cursor: default; }
.kpi-icon {
  width: 40px;
  height: 40px;
  border-radius: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.kpi-icon svg { width: 19px; height: 19px; }
.kpi-body { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.kpi-label { font-size: 12.5px; font-weight: 600; color: #8a8ca3; }
.kpi-value { font-size: 23px; font-weight: 800; color: #191a2b; letter-spacing: -0.3px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.kpi-value small { font-size: 13px; font-weight: 600; color: #b0b2c4; margin-left: 2px; }
.kpi-value.accent { color: #5e5ce6; }
.kpi-value.danger { color: #d64550; }

.sk-kpi {
  display: block;
  width: 100%;
  height: 40px;
  border-radius: 8px;
  background: linear-gradient(90deg, #f0f0f6 25%, #f8f8fc 50%, #f0f0f6 75%);
  background-size: 200% 100%;
  animation: shimmer 1.4s ease infinite;
}
@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

.chart-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 14px;
  margin-bottom: 16px;
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

.chart { height: 240px; }
.chart.tall { height: 260px; }

.activity-list { list-style: none; margin: 0; padding: 0; }
.activity-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 6px;
  border-bottom: 1px solid #f5f5fa;
  cursor: pointer;
  border-radius: 8px;
  transition: background 0.12s ease;
}
.activity-row:last-child { border-bottom: none; }
.activity-row:hover { background: #f8f8fe; }

.avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
  flex-shrink: 0;
}
.act-main { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.act-name { font-size: 13.5px; font-weight: 600; color: #191a2b; }
.act-desc { font-size: 12px; color: #9a9cb0; }
.act-status .dot { display: block; width: 7px; height: 7px; border-radius: 50%; }
.dot.completed { background: #3eb37f; }
.dot.in_progress { background: #5e5ce6; }
.dot.onboarding { background: #f2a54a; }
.dot.abandoned { background: #c2c3d1; }
.act-time { font-size: 12px; color: #a2a4b8; font-variant-numeric: tabular-nums; white-space: nowrap; }

.placeholder { color: #9a9cb0; font-size: 14px; }
.placeholder.error { color: #d64550; }
</style>
