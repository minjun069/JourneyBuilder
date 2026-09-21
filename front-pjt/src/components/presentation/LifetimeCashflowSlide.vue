<script setup>
import { computed } from 'vue'
import { useReportStore } from '@/stores/report'
import { fmtKRW } from '@/utils/format'

// 글에 첨부된 스냅샷을 받는다. 없으면 기존처럼 store 를 본다(대시보드·AI 화면 동작 유지)
const props = defineProps({
  data: { type: Object, default: null },
})

const reportStore = useReportStore()
const report = computed(() => props.data ?? reportStore.reports?.['lifetime-cashflow'])

const rows = computed(() => report.value?.chart_data ?? [])
const sampledRows = computed(() => {
  const list = rows.value
  if (list.length <= 14) return list

  const firstAge = list[0]?.age
  const lastAge = list[list.length - 1]?.age
  return list.filter(row => row.age === firstAge || row.age === lastAge || row.age % 5 === 0)
})

const maxCash = computed(() => Math.max(
  1,
  ...sampledRows.value.flatMap(row => [
    Math.abs(row.cash_inflow ?? 0),
    Math.abs(row.cash_outflow ?? 0),
  ]),
))

const maxAsset = computed(() => Math.max(
  1,
  ...sampledRows.value.map(row => Math.abs(row.total_net_worth ?? 0)),
))

// data 는 prop 이름이 되었으므로 막대 배열은 columns 로 둔다
const columns = computed(() => sampledRows.value.map(row => ({
  age: `${row.age}세`,
  income: height(row.cash_inflow, maxCash.value),
  expense: height(Math.abs(row.cash_outflow ?? 0), maxCash.value),
  asset: height(row.total_net_worth, maxAsset.value),
  raw: row,
})))

const depletionText = computed(() => {
  const point = report.value?.depletion_point
  if (!point?.is_depleted) return '금융자산 고갈 없음'
  return `${point.depletion_age}세 금융자산 고갈 예상`
})

function height(value, max) {
  return Math.max(4, Math.round((Math.abs(value ?? 0) / max) * 100))
}

function label(col) {
  const row = col.raw
  return [
    col.age,
    `수입 ${fmtKRW(row.cash_inflow)}`,
    `지출 ${fmtKRW(Math.abs(row.cash_outflow ?? 0))}`,
    `순자산 ${fmtKRW(row.total_net_worth)}`,
  ].join('\n')
}
</script>

<template>
  <div class="slide">
    <div class="summary">
      <div>
        <span class="summary-label">최종 순자산</span>
        <strong>{{ fmtKRW(report?.final_status?.final_net_worth) }}</strong>
      </div>
      <div>
        <span class="summary-label">부족자금</span>
        <strong>{{ fmtKRW(report?.final_status?.final_deficit) }}</strong>
      </div>
      <div>
        <span class="summary-label">고갈 시점</span>
        <strong>{{ depletionText }}</strong>
      </div>
    </div>

    <div class="legend">
      <span class="dot income-dot"></span>수입
      <span class="dot expense-dot"></span>지출
      <span class="dot asset-dot"></span>순자산
    </div>
    <div class="chart">
      <div v-for="(col, i) in columns" :key="i" class="col" :title="label(col)">
        <div class="bar-group">
          <div class="bar income-bar" :style="{ height: col.income + '%' }"></div>
          <div class="bar expense-bar" :style="{ height: col.expense + '%' }"></div>
          <div class="bar asset-bar" :style="{ height: col.asset + '%' }"></div>
        </div>
        <div class="age-label">{{ col.age }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.slide {
  width: 100%;
  height: 100%;
  /* 아래 @container 규칙의 기준 — 화면 폭이 아니라 슬라이드가 놓인 상자 폭을 본다 */
  container-type: inline-size;
  padding: 12px 20px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  box-sizing: border-box;
  overflow: hidden;
}

.summary {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  flex-shrink: 0;
}

.summary > div {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 12px 14px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
}

.summary-label {
  font-size: 11px;
  color: #6b7280;
}

.summary strong {
  font-size: 15px;
  color: #1f2937;
}

.legend {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #6b7280;
  flex-shrink: 0;
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  display: inline-block;
  margin-right: 2px;
  margin-left: 10px;
}

.dot:first-child { margin-left: 0; }

.income-dot  { background: #5e5ce6; }
.expense-dot { background: #f59e0b; }
.asset-dot   { background: #22c55e; }

.chart {
  flex: 1;
  display: flex;
  align-items: flex-end;
  gap: 8px;
  min-height: 0;
}

.col {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  height: 100%;
}

.bar-group {
  flex: 1;
  width: 100%;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  gap: 3px;
}

.bar {
  flex: 1;
  border-radius: 3px 3px 0 0;
  min-height: 4px;
  transition: height 0.5s;
}

.income-bar  { background: #5e5ce6; opacity: 0.85; }
.expense-bar { background: #f59e0b; opacity: 0.85; }
.asset-bar   { background: #22c55e; opacity: 0.85; }

.age-label { font-size: 10px; color: #9498b5; white-space: nowrap; flex-shrink: 0; }

/* 좁은 상자(커뮤니티 글의 첨부 슬라이드)용 — 열이 나이 레이블 너비(min-content) 아래로
   줄지 않아 375px 화면에서는 오른쪽 열이 상자 밖으로 밀려 통째로 잘렸다.
   상자 폭이 좁을 때만 간격·글자를 줄여 모든 열을 안에 넣는다.
   컨테이너 쿼리라 대시보드·AI 화면(넓은 폭)에는 적용되지 않는다 */
@container (max-width: 460px) {
  .chart { gap: 2px; }
  .col { min-width: 0; gap: 2px; }
  .bar-group { gap: 2px; }
  .bar { border-radius: 2px 2px 0 0; }
  .age-label { font-size: 8px; letter-spacing: -0.04em; }
}
</style>
