<template>
  <div class="overview">
    <div class="page-head">
      <h2 class="page-title">개요</h2>
      <div class="refresh-area">
        <span v-if="generatedAt" class="refreshed-at">마지막 갱신 {{ refreshedLabel }}</span>
        <button type="button" class="btn-refresh" :disabled="loading" @click="load">새로고침</button>
      </div>
    </div>

    <p v-if="error" class="placeholder error">{{ error }}</p>

    <template v-else>
      <!-- FR-06 KPI 4종 -->
      <section class="kpi-row" v-if="kpi">
        <div class="kpi-card" @click="router.push('/pb/customers')">
          <span class="kpi-label">총 고객</span>
          <span class="kpi-value">{{ kpi.customer_count }}<small>명</small></span>
        </div>
        <div class="kpi-card" @click="router.push('/pb/customers?status=completed')">
          <span class="kpi-label">상담 완료율</span>
          <span class="kpi-value">{{ kpi.completed_rate }}<small>%</small></span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">총 순자산</span>
          <span class="kpi-value accent">{{ formatEok(kpi.total_net_worth) }}</span>
        </div>
        <div class="kpi-card" @click="router.push('/pb/customers?health=risky')">
          <span class="kpi-label">위험 고객</span>
          <span class="kpi-value" :class="{ danger: kpi.risk_customer_count > 0 }">
            {{ kpi.risk_customer_count }}<small>명</small>
          </span>
        </div>
      </section>

      <!-- FR-06 대표 세그먼트 차트 2개 -->
      <div class="chart-row">
        <section class="card">
          <h3 class="card-title">주요 니즈 분포</h3>
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
        <table class="activity-table" v-if="activities.length">
          <tbody>
            <tr v-for="act in activities" :key="act.session_id" class="activity-row"
                @click="router.push(`/pb/customers/${act.user_id}`)">
              <td class="act-name">{{ act.name }}</td>
              <td>
                <span class="act-status" :class="act.status">
                  {{ STATUS_LABELS[act.status] ?? act.status }}
                </span>
              </td>
              <td class="act-time">{{ formatDateTime(act.completed_at ?? act.created_at) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="placeholder">최근 활동이 없습니다.</p>
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

use([CanvasRenderer, PieChart, BarChart, GridComponent, TooltipComponent, LegendComponent])

const STATUS_LABELS = {
  completed: '상담 완료', in_progress: '상담 진행', onboarding: '상담 시작', abandoned: '이탈',
}
const NEED_COLORS = ['#5e5ce6', '#4bc0c0', '#ff9f40', '#ff6384', '#36a2eb', '#9ca3af', '#d7d7e8']
const GRADE_COLORS = { 양호: '#66bb6a', 주의: '#ffa726', 위험: '#ef5350' }

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
    itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 12, color: '#444' },
  },
  series: [{
    type: 'pie',
    radius: ['42%', '70%'],
    center: ['38%', '50%'],
    label: { show: false },
    data: needBuckets.value
      .filter(b => b.count > 0)
      .map((b, i) => ({
        name: bucketLabel('need', b.bucket),
        value: b.count,
        bucket: b.bucket,
        itemStyle: { color: NEED_COLORS[i % NEED_COLORS.length] },
      })),
  }],
}))

const indicatorStackOption = computed(() => ({
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
  legend: { top: 0, textStyle: { fontSize: 12 } },
  grid: { left: 8, right: 16, top: 28, bottom: 0, containLabel: true },
  xAxis: { type: 'value', minInterval: 1 },
  yAxis: {
    type: 'category',
    data: indicatorRisk.value.map(item => item.key.replace('지표', '')),
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

.kpi-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px;
  margin-bottom: 16px;
}
.kpi-card {
  background: #fff;
  border: 1px solid #ebebf5;
  border-radius: 14px;
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  cursor: pointer;
  transition: box-shadow 0.15s ease;
}
.kpi-card:hover { box-shadow: 0 4px 14px rgba(94, 92, 230, 0.12); }
.kpi-label { font-size: 13px; font-weight: 600; color: #888; }
.kpi-value { font-size: 26px; font-weight: 800; color: #1a1a2e; }
.kpi-value small { font-size: 14px; font-weight: 600; color: #aaa; margin-left: 3px; }
.kpi-value.accent { color: #5e5ce6; }
.kpi-value.danger { color: #e53935; }

.chart-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}

.card {
  background: #fff;
  border: 1px solid #ebebf5;
  border-radius: 14px;
  padding: 20px 24px;
  margin-bottom: 0;
}
.card-title { font-size: 16px; font-weight: 700; color: #1a1a2e; margin: 0 0 12px; }

.chart { height: 240px; }
.chart.tall { height: 260px; }

.activity-table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.activity-row { cursor: pointer; }
.activity-row:hover { background: #f7f7ff; }
.activity-table td { padding: 10px 12px; border-bottom: 1px solid #f4f4fb; }
.act-name { font-weight: 600; color: #1a1a2e; }
.act-status { font-size: 12px; font-weight: 600; border-radius: 999px; padding: 3px 10px; }
.act-status.completed { background: #e6f6ee; color: #23855c; }
.act-status.in_progress { background: #eeeefc; color: #5e5ce6; }
.act-status.onboarding { background: #fff3e0; color: #b06f14; }
.act-status.abandoned { background: #f2f2f2; color: #888; }
.act-time { text-align: right; color: #999; font-size: 12.5px; }

.placeholder { color: #888; font-size: 14px; }
.placeholder.error { color: #d43a3a; }
</style>
