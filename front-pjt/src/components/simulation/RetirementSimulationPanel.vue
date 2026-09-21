<script setup>
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import SimulationIssueList from '@/components/simulation/SimulationIssueList.vue'
import { useReportStore } from '@/stores/report'
import { fmtKRW } from '@/utils/format'

const CONSULTATION_PATH = '/ai'

defineProps({
  compact: { type: Boolean, default: false },
})

const reportStore = useReportStore()
const monthlySavings = ref(null)
const retirementAge = ref(null)
const expenseRows = ref([])
const hydratedBaselineKey = ref(null)
const formError = ref('')

const analysis = computed(() => reportStore.simulation?.analysis ?? null)
const baseline = computed(() => analysis.value?.baseline ?? null)
const scenario = computed(() => analysis.value?.scenarios?.[0] ?? null)
const comparison = computed(() => scenario.value?.result ?? null)
const delta = computed(() => scenario.value?.delta ?? null)
const profile = computed(() => analysis.value?.baseline_profile ?? null)
const issues = computed(() => reportStore.simulationError?.details?.issues ?? [])
// INPUT_NOT_READY 는 실패가 아니라 "아직 입력이 덜 됐다" 는 뜻이다.
// 빨간 오류 대신 중립 안내 + 상담 링크로 보여 준다.
const inputNotReady = computed(() => reportStore.simulationError?.code === 'INPUT_NOT_READY')
const canRetry = computed(() => reportStore.simulationError?.retryable === true)
// 세션 목록을 아직 받아오는 중에는 activeSessionId 가 null 이다.
// 그동안 "완료 상담이 없습니다" 를 띄우면 대시보드에 들어올 때마다 깜빡인다.
const stateLoading = computed(() => (
  !analysis.value
  && !reportStore.simulationError
  && (reportStore.loading || reportStore.simulationLoading)
))
const trajectory = computed(() => {
  const baselineRows = baseline.value?.asset_percentiles ?? []
  const comparisonByAge = new Map(
    (comparison.value?.asset_percentiles ?? []).map((row) => [row.age, row]),
  )
  if (baselineRows.length <= 7) return baselineRows.map((row) => ({ ...row, comparison: comparisonByAge.get(row.age) }))
  return baselineRows
    .filter((row, index) => index === 0 || index === baselineRows.length - 1 || row.age % 10 === 0)
    .map((row) => ({ ...row, comparison: comparisonByAge.get(row.age) }))
})

const noticeLabels = {
  partial_financial_scope: '금융자산·저축·연금·생활비만 반영한 부분 분석입니다.',
  single_portfolio_assumption: '자산별 비중 대신 하나의 공통 수익률 가정을 사용합니다.',
  pre_retirement_cashflow_not_recalculated: '은퇴 전 현금흐름은 월 총 적립액으로 단순화했습니다.',
  pension_account_not_modeled: '연금계좌 평가액과 납입·인출 과정은 별도로 계산하지 않습니다.',
  real_estate_not_modeled: '부동산 평가·처분·임대료는 반영하지 않습니다.',
  liabilities_not_modeled: '부채 원금·이자·상환은 반영하지 않습니다.',
  milestones_not_modeled: '일회성 재무목표 수입·지출은 반영하지 않습니다.',
}

watch(
  () => reportStore.simulation?.baselineKey,
  (baselineKey) => {
    if (!baselineKey || baselineKey === hydratedBaselineKey.value) return
    const next = reportStore.simulation?.analysis?.baseline_profile
    if (!next) return
    hydratedBaselineKey.value = baselineKey
    monthlySavings.value = next.monthly_savings
    retirementAge.value = next.retirement_age
    expenseRows.value = (next.retirement_expenses ?? []).map((row) => ({ ...row }))
    formError.value = ''
  },
  { immediate: true },
)

function probability(value) {
  if (typeof value !== 'number') return '계산 전'
  if (value > 0 && value < 0.001) return '<0.1%'
  return `${(value * 100).toFixed(1)}%`
}

function probabilityDelta(value) {
  if (typeof value !== 'number') return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(1)}%p`
}

function addExpenseRow() {
  expenseRows.value.push({
    from_age: retirementAge.value ?? profile.value?.retirement_age ?? 60,
    to_age: profile.value?.end_age ?? 90,
    monthly_amount: 0,
  })
}

function removeExpenseRow(index) {
  if (expenseRows.value.length <= 1) return
  expenseRows.value.splice(index, 1)
}

function validateChanges() {
  if (!Number.isInteger(monthlySavings.value) || monthlySavings.value < 0) {
    return '월 적립액은 0 이상의 원 단위 정수로 입력해 주세요.'
  }
  if (!Number.isInteger(retirementAge.value) || retirementAge.value < 18 || retirementAge.value > 100) {
    return '은퇴 나이는 18세부터 100세 사이의 정수로 입력해 주세요.'
  }
  if (expenseRows.value.length === 0) return '은퇴 생활비 기간을 한 개 이상 입력해 주세요.'
  for (const row of expenseRows.value) {
    if (
      !Number.isInteger(row.from_age)
      || !Number.isInteger(row.to_age)
      || !Number.isInteger(row.monthly_amount)
      || row.from_age < 0
      || row.to_age < row.from_age
      || row.monthly_amount < 0
    ) {
      return '생활비 기간과 월 금액을 다시 확인해 주세요.'
    }
  }
  return ''
}

async function recalculate() {
  formError.value = validateChanges()
  if (formError.value) return
  await reportStore.runWhatIfSimulation({
    monthly_savings: monthlySavings.value,
    retirement_age: retirementAge.value,
    retirement_expenses: expenseRows.value.map((row) => ({ ...row })),
  })
}
</script>

<template>
  <section class="simulation-panel" :class="{ compact }">
    <header class="panel-header">
      <div>
        <h2>은퇴 시뮬레이션</h2>
        <p class="subtitle">결과는 서버에 저장하지 않으며 화면을 새로 열 때 최신 정보로 다시 계산합니다.</p>
      </div>
      <button
        v-if="reportStore.activeSessionId"
        class="secondary-button"
        type="button"
        :disabled="reportStore.simulationLoading"
        @click="reportStore.runBaselineSimulation()"
      >
        기준 다시 계산
      </button>
    </header>

    <div v-if="stateLoading" class="state-card">
      최신 상담 정보로 시뮬레이션을 계산하고 있습니다.
    </div>
    <template v-else-if="!reportStore.activeSessionId">
      <div v-if="reportStore.error" class="state-card error" role="alert">
        상담 정보를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.
      </div>
      <div v-else class="state-card">
        <p class="state-message">AI 상담을 완료하면 은퇴 시뮬레이션을 여기에서 확인할 수 있습니다.</p>
        <RouterLink class="state-link" :to="CONSULTATION_PATH">AI 상담 시작하기</RouterLink>
      </div>
    </template>

    <template v-if="analysis">
      <div v-if="reportStore.simulation?.stale" class="warning" role="alert">
        상담 정보나 계산 기준이 변경되었습니다. 기준을 다시 계산해 주세요.
      </div>
      <div v-if="reportStore.simulationError" class="warning" role="alert">
        <p class="state-message">{{ reportStore.simulationError.message }}</p>
        <SimulationIssueList :issues="issues" />
      </div>

      <div class="result-grid">
        <article class="result-card">
          <span>기준 성공확률</span>
          <strong>{{ probability(baseline?.success_probability) }}</strong>
          <small>최종 잔액 P50 {{ fmtKRW(baseline?.final_balance_percentiles?.p50) }}</small>
        </article>
        <article class="result-card comparison" :class="{ empty: !comparison }">
          <span>변경 성공확률</span>
          <strong>{{ probability(comparison?.success_probability) }}</strong>
          <small v-if="comparison">최종 잔액 P50 {{ fmtKRW(comparison.final_balance_percentiles.p50) }}</small>
          <small v-else>조건을 조절하고 다시 계산해 보세요.</small>
        </article>
        <article class="result-card delta">
          <span>성공확률 차이</span>
          <strong>{{ probabilityDelta(delta?.success_probability_delta_pp) }}</strong>
          <small>변경안 − 기준안</small>
        </article>
      </div>

      <form class="controls" @submit.prevent="recalculate">
        <label>
          <span>월 총 적립액</span>
          <input v-model.number="monthlySavings" type="number" min="0" step="1">
          <small>원/월</small>
        </label>
        <label>
          <span>은퇴 나이</span>
          <input v-model.number="retirementAge" type="number" min="18" max="100" step="1">
          <small>세</small>
        </label>

        <fieldset>
          <legend>은퇴 후 월 생활비 전체 일정</legend>
          <div v-for="(row, index) in expenseRows" :key="index" class="expense-row">
            <input v-model.number="row.from_age" aria-label="시작 나이" type="number" min="0" step="1">
            <span>~</span>
            <input v-model.number="row.to_age" aria-label="종료 나이" type="number" min="0" step="1">
            <input v-model.number="row.monthly_amount" aria-label="월 생활비" type="number" min="0" step="1">
            <span>원/월</span>
            <button type="button" :disabled="expenseRows.length <= 1" @click="removeExpenseRow(index)">삭제</button>
          </div>
          <button class="row-button" type="button" @click="addExpenseRow">기간 추가</button>
        </fieldset>

        <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>
        <button
          class="primary-button"
          type="submit"
          :disabled="reportStore.simulationLoading || reportStore.simulation?.stale"
        >
          {{ reportStore.simulationLoading ? '계산 중…' : '변경 조건으로 다시 계산' }}
        </button>
      </form>

      <div v-if="!compact" class="trajectory-wrap">
        <h3>나이별 금융자산 분포</h3>
        <table>
          <thead>
            <tr><th>나이</th><th>기준 P10</th><th>기준 P50</th><th>기준 P90</th><th>변경 P50</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in trajectory" :key="row.age">
              <td>{{ row.age }}세</td>
              <td>{{ fmtKRW(row.balance.p10) }}</td>
              <td>{{ fmtKRW(row.balance.p50) }}</td>
              <td>{{ fmtKRW(row.balance.p90) }}</td>
              <td>{{ row.comparison ? fmtKRW(row.comparison.balance.p50) : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <details v-if="analysis.notices.length" class="notices" :open="!compact">
        <summary>계산 범위와 가정</summary>
        <ul>
          <li v-for="notice in analysis.notices" :key="`${notice.code}-${notice.field_name}`">
            {{ noticeLabels[notice.code] ?? `추가 계산 안내: ${notice.code}` }}
          </li>
        </ul>
        <p>성공확률은 계획 기간 연말 잔액이 한 번도 음수가 되지 않은 모의 경로의 비율이며 미래 성과를 보장하지 않습니다.</p>
        <p>정책 {{ reportStore.simulation.meta.simulation_policy_version }} · {{ analysis.metadata?.num_paths ?? baseline?.num_paths }}개 경로</p>
      </details>
    </template>

    <div
      v-else-if="reportStore.simulationError"
      class="state-card"
      :class="inputNotReady ? 'guidance' : 'error'"
      role="alert"
    >
      <p class="state-message">{{ reportStore.simulationError.message }}</p>
      <SimulationIssueList :issues="issues" />
      <RouterLink v-if="inputNotReady" class="state-link" :to="CONSULTATION_PATH">
        상담에서 입력 보완하기
      </RouterLink>
      <p v-else-if="canRetry" class="state-hint">
        위 ‘기준 다시 계산’ 버튼으로 다시 시도해 주세요.
      </p>
    </div>
  </section>
</template>

<style scoped>
.simulation-panel { display: flex; flex-direction: column; gap: 18px; color: #1a1f3c; }
.panel-header { display: flex; justify-content: space-between; gap: 18px; align-items: flex-start; }
.panel-header h2 { margin: 2px 0 6px; font-size: 26px; font-weight: 800; letter-spacing: -0.4px; color: #1a1a2e; }
.subtitle { margin: 0; color: #6b7280; font-size: 13px; }
.result-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.result-card { border: 1px solid #e9eaf2; border-radius: 12px; padding: 16px; background: #fff; box-shadow: 0 1px 3px rgba(20, 20, 43, 0.06); display: flex; flex-direction: column; gap: 6px; }
.result-card span, .result-card small { color: #6b7280; font-size: 13px; font-weight: 600; }
.result-card strong { font-size: 26px; font-weight: 800; letter-spacing: -0.3px; }
.result-card.comparison { border-color: #c9c8ff; background: #f8f8ff; }
.result-card.delta strong { color: #5e5ce6; }
.result-card.empty strong { color: #9ca3af; }
.controls { border: 1px solid #e9eaf2; border-radius: 12px; padding: 16px; background: #fff; box-shadow: 0 1px 3px rgba(20, 20, 43, 0.06); display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.controls label { display: grid; grid-template-columns: 1fr minmax(110px, 1fr) auto; gap: 8px; align-items: center; font-size: 13px; font-weight: 700; }
input { min-width: 0; border: 1px solid #d8dae6; border-radius: 7px; padding: 8px; }
fieldset { grid-column: 1 / -1; border: 0; padding: 0; margin: 0; }
legend { margin-bottom: 8px; font-size: 13px; font-weight: 700; }
.expense-row { display: grid; grid-template-columns: 72px auto 72px minmax(120px, 1fr) auto auto; gap: 6px; align-items: center; margin-bottom: 7px; }
button { border: 0; border-radius: 8px; padding: 9px 12px; cursor: pointer; }
button:disabled { cursor: not-allowed; opacity: .5; }
.primary-button { grid-column: 1 / -1; background: #5e5ce6; color: #fff; font-weight: 800; }
.secondary-button, .row-button { background: #eeeefe; color: #4543b8; font-weight: 700; }
.row-button { padding: 7px 10px; }
/* 상태 카드는 다른 대시보드 페이지(DashboardReportShell)와 같은 모양을 쓴다. */
.state-card {
  min-height: 260px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  border: 1px solid #e9eaf2;
  border-radius: 12px;
  padding: 24px;
  background: #fff;
  color: #777;
  font-size: 15px;
  text-align: center;
}
.state-card.error { color: #dc2626; }
.state-card.guidance { color: #4b5163; }
.state-message { margin: 0; font-weight: 700; }
.state-hint { margin: 0; font-size: 13px; color: #6b7280; }
.state-link { border-radius: 8px; padding: 9px 14px; background: #eeeefe; color: #4543b8; font-size: 14px; font-weight: 800; text-decoration: none; }
.state-card :deep(.issue-list) { max-width: 520px; }
.warning { display: flex; flex-direction: column; gap: 8px; border-radius: 12px; padding: 18px; background: #fff7ed; color: #9a3412; }
.form-error { color: #b91c1c; }
.form-error { grid-column: 1 / -1; margin: 0; font-size: 13px; }
.trajectory-wrap { overflow-x: auto; }
.trajectory-wrap h3 { font-size: 15px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { padding: 8px; border-bottom: 1px solid #ececf5; text-align: right; white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
.notices { color: #5b6175; font-size: 12px; }
.notices summary { cursor: pointer; font-weight: 800; }
.compact { gap: 9px; height: 100%; overflow-y: auto; }
.compact .panel-header h2 { font-size: 18px; }
.compact .subtitle { display: none; }
.compact .result-grid { gap: 6px; }
.compact .result-card { padding: 8px; }
.compact .result-card strong { font-size: 18px; }
.compact .controls { padding: 9px; gap: 7px; }
.compact .expense-row { grid-template-columns: 52px auto 52px minmax(80px, 1fr) auto auto; }
.compact input, .compact button { padding: 5px 7px; font-size: 11px; }
.compact .state-card { min-height: 0; gap: 8px; padding: 12px; font-size: 13px; }
.compact .state-link { padding: 6px 10px; font-size: 12px; }
@media (max-width: 850px) {
  .result-grid, .controls { grid-template-columns: 1fr; }
  .primary-button, fieldset, .form-error { grid-column: 1; }
  .expense-row { grid-template-columns: 1fr auto 1fr; }
  .expense-row input:nth-of-type(3) { grid-column: 1 / 3; }
}
</style>
