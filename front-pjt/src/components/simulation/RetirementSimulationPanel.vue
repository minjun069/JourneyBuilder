<script setup>
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import SimulationFanChart from '@/components/simulation/SimulationFanChart.vue'
import SimulationIssueList from '@/components/simulation/SimulationIssueList.vue'
import { findDepletion, sanitizeRows } from '@/components/simulation/fanChart.js'
import { sectionLabel } from '@/components/simulation/fieldLabel.js'
import {
  assumptionLine,
  baselineHeadline,
  comparisonHeadline,
  expenseSummary,
  leverDiff,
} from '@/components/simulation/simulationNarrative.js'
import { useReportStore } from '@/stores/report'

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
// 생활비 구간은 시안대로 요약 버튼 뒤에 접어 둔다. 열면 기존 행 편집이 그대로 나온다.
const expensesOpen = ref(false)

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

const numPaths = computed(() => analysis.value?.metadata?.num_paths ?? baseline.value?.num_paths ?? null)
// 표 대신 전체 구간을 그리므로 다운샘플링 없이 asset_percentiles 를 그대로 쓴다.
const baselineRows = computed(() => sanitizeRows(baseline.value?.asset_percentiles ?? []))
const comparisonRows = computed(() => sanitizeRows(comparison.value?.asset_percentiles ?? []))
const horizonAge = computed(() => (
  baselineRows.value.length ? baselineRows.value[baselineRows.value.length - 1].age : null
))
const baselineDepletion = computed(() => findDepletion(
  baselineRows.value.map((row) => ({ age: row.age, value: row.p50 })),
))

const headline = computed(() => {
  if (comparison.value) {
    return comparisonHeadline({
      baselineProbability: baseline.value?.success_probability,
      comparisonProbability: comparison.value.success_probability,
      deltaPp: delta.value?.success_probability_delta_pp,
      baselineProfile: profile.value,
      changedProfile: scenario.value?.profile,
    })
  }
  return baselineHeadline({
    probability: baseline.value?.success_probability,
    paths: numPaths.value,
    horizonAge: horizonAge.value,
    depletionAge: baselineDepletion.value?.age ?? null,
  })
})

const lede = computed(() => (
  numPaths.value
    ? `지금 계획대로 저축하고 은퇴했을 때, 돈이 기대수명까지 버티는지 ${numPaths.value.toLocaleString('ko-KR')}가지 시장 상황으로 돌려봅니다.`
    : '지금 계획대로 저축하고 은퇴했을 때, 돈이 기대수명까지 버티는지 여러 시장 상황으로 돌려봅니다.'
))

const savingsDiff = computed(() => leverDiff({
  current: profile.value?.monthly_savings ?? null,
  next: monthlySavings.value,
  kind: 'amount',
}))
const ageDiff = computed(() => leverDiff({
  current: profile.value?.retirement_age ?? null,
  next: retirementAge.value,
  kind: 'age',
}))
const expenses = computed(() => expenseSummary(expenseRows.value))
const expensesChanged = computed(() => (
  JSON.stringify(profile.value?.retirement_expenses ?? []) !== JSON.stringify(expenseRows.value)
))
const assumptions = computed(() => assumptionLine({
  assumptions: analysis.value?.metadata?.assumptions,
  paths: numPaths.value,
  noticeCount: analysis.value?.notices?.length ?? 0,
}))

const noticeLabels = {
  partial_financial_scope: '금융자산·저축·연금·생활비만 반영한 부분 분석입니다.',
  single_portfolio_assumption: '자산별 비중 대신 하나의 공통 수익률 가정을 사용합니다.',
  pre_retirement_cashflow_not_recalculated: '은퇴 전 현금흐름은 월 총 적립액으로 단순화했습니다.',
  pension_account_not_modeled: '연금계좌 평가액과 납입·인출 과정은 별도로 계산하지 않습니다.',
  real_estate_not_modeled: '부동산 평가·처분·임대료는 반영하지 않습니다.',
  liabilities_not_modeled: '부채 원금·이자·상환은 반영하지 않습니다.',
  milestones_not_modeled: '일회성 재무목표 수입·지출은 반영하지 않습니다.',
  pension_expected_amount_estimated:
    '예상 월 수령액을 적지 않은 연금은 적립액·월 납입액·수익률로 환산해 계산했습니다.',
  pension_horizon_limited:
    '기대수명 이후의 연금 수령분은 계획 기간 밖이라 반영하지 않았습니다.',
  pension_outside_horizon:
    '수령 기간이 계획 기간과 겹치지 않아 이 연금은 반영하지 않았습니다.',
  other_financial_asset_not_modeled:
    '모형이 다루는 자산 유형이 아니어서 이 금융자산은 반영하지 않았습니다.',
}

// 천 단위 구분이 들어간 입력값을 정수로 되돌린다. 빈 값은 null 이라 검증에서 걸린다.
function parseAmountInput(text) {
  const cleaned = String(text ?? '').replace(/[^\d-]/g, '')
  const negative = cleaned.startsWith('-')
  const digits = cleaned.replace(/-/g, '')
  if (!digits) return null
  const value = Number(digits)
  if (!Number.isFinite(value)) return null
  return negative ? -value : value
}

function formatAmountInput(value) {
  return Number.isFinite(value) ? value.toLocaleString('ko-KR') : ''
}

const savingsText = ref('')
const ageText = ref('')

function onSavingsInput(event) {
  savingsText.value = event.target.value
  monthlySavings.value = parseAmountInput(event.target.value)
}
function onAgeInput(event) {
  ageText.value = event.target.value
  retirementAge.value = parseAmountInput(event.target.value)
}
function normalizeInputs() {
  savingsText.value = formatAmountInput(monthlySavings.value)
  ageText.value = formatAmountInput(retirementAge.value)
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
    normalizeInputs()
  },
  { immediate: true },
)

function addExpenseRow() {
  expensesOpen.value = true
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
  if (formError.value) {
    // 검증에 걸린 구간 값은 접혀 있으면 고칠 수 없으니 펼쳐 준다.
    if (formError.value.includes('생활비')) expensesOpen.value = true
    return
  }
  await reportStore.runWhatIfSimulation({
    monthly_savings: monthlySavings.value,
    retirement_age: retirementAge.value,
    retirement_expenses: expenseRows.value.map((row) => ({ ...row })),
  })
}

// 비교 결과를 지우려면 기준 계산으로 되돌린다(스토어를 건드리지 않는 유일한 방법).
async function resetToBaseline() {
  if (profile.value) {
    monthlySavings.value = profile.value.monthly_savings
    retirementAge.value = profile.value.retirement_age
    expenseRows.value = (profile.value.retirement_expenses ?? []).map((row) => ({ ...row }))
    normalizeInputs()
  }
  formError.value = ''
  await reportStore.runBaselineSimulation()
}
</script>

<template>
  <section class="simulation-panel" :class="{ compact }">
    <header class="panel-header">
      <div class="panel-title">
        <h2>은퇴 시뮬레이션</h2>
        <p v-if="!compact" class="lede">{{ lede }}</p>
      </div>
      <button
        v-if="reportStore.activeSessionId"
        class="ghost-button"
        type="button"
        :disabled="reportStore.simulationLoading"
        @click="reportStore.runBaselineSimulation()"
      >
        다시 계산
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
        AI 상담을 완료하면 은퇴 시뮬레이션 결과를 여기에서 확인할 수 있습니다.
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

      <!-- 답: 문장이 먼저, 숫자가 그 근거 -->
      <div class="headline" :class="`tone-${headline.tone}`">
        <div class="headline-verdict">
          <span class="verdict-chip">{{ headline.chip }}</span>
          <div class="verdict-value">
            <strong>{{ headline.value }}</strong>
            <span
              v-if="headline.deltaText"
              class="verdict-delta"
              :class="`delta-${headline.deltaTone}`"
            >{{ headline.deltaText }}</span>
          </div>
          <span v-if="headline.support" class="verdict-support">{{ headline.support }}</span>
        </div>
        <div class="headline-copy">
          <p class="headline-title">{{ headline.title }}</p>
          <p class="headline-detail">{{ headline.detail }}</p>
        </div>
      </div>

      <SimulationFanChart
        :baseline-rows="baselineRows"
        :comparison-rows="comparisonRows"
        :retirement-age="profile?.retirement_age ?? null"
        :comparison-retirement-age="scenario?.profile?.retirement_age ?? null"
        :compact="compact"
      />

      <!-- 조건 바꿔보기 -->
      <form class="controls" @submit.prevent="recalculate">
        <div class="controls-head">
          <div class="controls-intro">
            <h3>{{ comparison ? '바꾼 조건' : '조건을 바꾸면 어떻게 될까요' }}</h3>
            <p v-if="!comparison && !compact">
              아래 값을 조정하면 바꾼 결과가 위 그래프에 겹쳐 그려집니다. 상담 내용은 그대로 두고 화면에서만 비교합니다.
            </p>
          </div>
          <button
            v-if="comparison"
            class="link-button"
            type="button"
            :disabled="reportStore.simulationLoading"
            @click="resetToBaseline"
          >
            원래 조건으로 되돌리기
          </button>
        </div>

        <div class="lever-row">
          <div class="lever lever-grow">
            <label for="lever-savings">매달 저축액</label>
            <div class="field" :class="{ changed: savingsDiff.changed }">
              <input
                id="lever-savings"
                :value="savingsText"
                type="text"
                inputmode="numeric"
                autocomplete="off"
                @input="onSavingsInput"
                @blur="normalizeInputs"
              >
              <span class="unit">원</span>
            </div>
            <span class="lever-diff" :class="{ changed: savingsDiff.changed }">{{ savingsDiff.text }}</span>
          </div>

          <div class="lever lever-age">
            <label for="lever-age">은퇴 나이</label>
            <div class="field" :class="{ changed: ageDiff.changed }">
              <input
                id="lever-age"
                :value="ageText"
                type="text"
                inputmode="numeric"
                autocomplete="off"
                @input="onAgeInput"
                @blur="normalizeInputs"
              >
              <span class="unit">세</span>
            </div>
            <span class="lever-diff" :class="{ changed: ageDiff.changed }">{{ ageDiff.text }}</span>
          </div>

          <div class="lever lever-grow">
            <span class="lever-label">은퇴 후 생활비</span>
            <button
              class="disclosure"
              type="button"
              :aria-expanded="expensesOpen"
              aria-controls="expense-periods"
              @click="expensesOpen = !expensesOpen"
            >
              <span class="disclosure-value">{{ expenses.amounts }}</span>
              <span class="disclosure-action">{{ expensesOpen ? '구간 접기' : expenses.label }}</span>
            </button>
            <span class="lever-diff" :class="{ changed: expensesChanged }">
              {{ expensesChanged ? '지금 계획과 다르게 설정함' : expenses.ages }}
            </span>
          </div>

          <button
            class="primary-button"
            type="submit"
            :disabled="reportStore.simulationLoading || reportStore.simulation?.stale"
          >
            {{ reportStore.simulationLoading ? '계산 중…' : '이 조건으로 계산' }}
          </button>
        </div>

        <fieldset v-show="expensesOpen" id="expense-periods" class="expense-periods">
          <legend>은퇴 후 월 생활비 전체 일정</legend>
          <div v-for="(row, index) in expenseRows" :key="index" class="expense-row">
            <input v-model.number="row.from_age" aria-label="시작 나이" type="number" min="0" step="1">
            <span aria-hidden="true">~</span>
            <input v-model.number="row.to_age" aria-label="종료 나이" type="number" min="0" step="1">
            <input v-model.number="row.monthly_amount" aria-label="월 생활비" type="number" min="0" step="1">
            <span class="unit">원/월</span>
            <button type="button" :disabled="expenseRows.length <= 1" @click="removeExpenseRow(index)">삭제</button>
          </div>
          <button class="row-button" type="button" @click="addExpenseRow">기간 추가</button>
        </fieldset>

        <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>
      </form>

      <!-- 가정 -->
      <details class="assumptions" :open="!compact">
        <summary>
          <span class="assumption-line">{{ assumptions }}</span>
          <span class="assumption-more">계산 방법 자세히</span>
        </summary>
        <ul v-if="analysis.notices.length">
          <li v-for="notice in analysis.notices" :key="`${notice.code}-${notice.field_name}`">
            <span v-if="sectionLabel(notice.field_name)" class="notice-where">
              {{ sectionLabel(notice.field_name) }}
            </span>
            {{ noticeLabels[notice.code] ?? `추가 계산 안내: ${notice.code}` }}
          </li>
        </ul>
        <p>성공확률은 계획 기간 연말 잔액이 한 번도 음수가 되지 않은 모의 경로의 비율이며 미래 성과를 보장하지 않습니다.</p>
        <p v-if="reportStore.simulation?.meta">
          정책 {{ reportStore.simulation.meta.simulation_policy_version }} · {{ numPaths }}개 경로
        </p>
      </details>
    </template>

    <!-- 막힘: 입력이 빠졌을 때 -->
    <div
      v-else-if="reportStore.simulationError && inputNotReady"
      class="blocked"
      role="alert"
    >
      <div class="blocked-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="#5e5ce6" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 19V5" />
          <path d="M4 19h16" />
          <path d="M8 15l3.5-4 3 2.5L20 7" />
        </svg>
      </div>
      <div class="blocked-body">
        <div class="blocked-copy">
          <h3>{{ issues.length ? `${issues.length}가지만 채우면 바로 계산할 수 있어요` : '조금만 더 입력하면 계산할 수 있어요' }}</h3>
          <p class="state-message">{{ reportStore.simulationError.message }}</p>
        </div>
        <SimulationIssueList :issues="issues" />
        <div class="blocked-actions">
          <RouterLink class="state-link" :to="CONSULTATION_PATH">상담에서 이어서 입력하기</RouterLink>
          <span class="blocked-hint">입력을 마치면 이 화면에서 자동으로 다시 계산합니다.</span>
        </div>
      </div>
    </div>

    <div v-else-if="reportStore.simulationError" class="state-card error" role="alert">
      <p class="state-message">{{ reportStore.simulationError.message }}</p>
      <SimulationIssueList :issues="issues" />
      <p v-if="canRetry" class="state-hint">
        위 ‘다시 계산’ 버튼으로 다시 시도해 주세요.
      </p>
    </div>
  </section>
</template>

<style scoped>
.simulation-panel { display: flex; flex-direction: column; gap: 20px; color: #1a1a2e; }
.panel-header { display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; }
.panel-title { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.panel-header h2 { margin: 0; font-size: 26px; font-weight: 800; letter-spacing: -0.4px; }
.lede { margin: 0; font-size: 15px; line-height: 1.6; color: #5b6076; }
.ghost-button {
  flex: none; height: 40px; padding: 0 16px; border: 1px solid #d9dbe9; border-radius: 10px;
  background: #fff; color: #3d4159; font-size: 14px; font-weight: 700; cursor: pointer; font-family: inherit;
}

/* ── 답: 판정 + 근거 숫자 / 문장 ── */
.headline {
  display: flex; align-items: stretch; border: 1px solid #e9eaf2; border-radius: 16px;
  background: #fff; overflow: hidden;
}
.headline-verdict {
  flex: none; width: 300px; box-sizing: border-box; padding: 28px 32px;
  display: flex; flex-direction: column; gap: 10px; justify-content: center;
}
.verdict-chip {
  display: inline-flex; align-self: flex-start; padding: 5px 11px; border-radius: 999px;
  color: #fff; font-size: 13px; font-weight: 800;
}
.verdict-value { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.verdict-value strong {
  font-size: 56px; font-weight: 800; line-height: 1; letter-spacing: -2px; font-variant-numeric: tabular-nums;
}
.verdict-delta { font-size: 17px; font-weight: 800; font-variant-numeric: tabular-nums; }
.verdict-support { font-size: 14px; }
/* 판정 색은 "얼마나 안전한가", %p 색은 "좋아졌나 나빠졌나" 로 뜻을 나눈다. */
.verdict-delta.delta-up { color: #176035 !important; }
.verdict-delta.delta-down { color: #a01f26 !important; }
.verdict-delta.delta-flat { color: #4b5067 !important; }
.headline-copy {
  flex-grow: 1; min-width: 0; box-sizing: border-box; padding: 28px 32px;
  display: flex; flex-direction: column; gap: 14px; justify-content: center;
}
.headline-title { margin: 0; font-size: 21px; font-weight: 700; line-height: 1.5; letter-spacing: -0.3px; }
.headline-detail { margin: 0; font-size: 15px; line-height: 1.65; color: #5b6076; }

.tone-danger .headline-verdict { background: #fdf4f4; border-right: 1px solid #f3dede; }
.tone-danger .verdict-chip { background: #b4232a; }
.tone-danger .verdict-value strong { color: #8f1c22; }
.tone-danger .verdict-delta { color: #8f1c22; }
.tone-danger .verdict-support { color: #7a3034; }
.tone-caution .headline-verdict { background: #fdf7ee; border-right: 1px solid #efe2cc; }
.tone-caution .verdict-chip { background: #8a5200; }
.tone-caution .verdict-value strong { color: #7a4900; }
.tone-caution .verdict-delta { color: #7a4900; }
.tone-caution .verdict-support { color: #6b4a1c; }
.tone-safe .headline-verdict { background: #f2f7f3; border-right: 1px solid #dceadf; }
.tone-safe .verdict-chip { background: #1f7a44; }
.tone-safe .verdict-value strong { color: #176035; }
.tone-safe .verdict-delta { color: #1f7a44; }
.tone-safe .verdict-support { color: #3f6a4f; }
.tone-neutral .headline-verdict { background: #f6f6fa; border-right: 1px solid #e6e7f0; }
.tone-neutral .verdict-chip { background: #4b5067; }
.tone-neutral .verdict-value strong { color: #2c3048; }
.tone-neutral .verdict-delta { color: #4b5067; }
.tone-neutral .verdict-support { color: #5b6076; }

/* ── 조건 바꿔보기 ── */
.controls {
  box-sizing: border-box; padding: 22px 28px 24px; border: 1px solid #e9eaf2; border-radius: 16px;
  background: #fff; display: flex; flex-direction: column; gap: 16px;
}
.controls-head { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; }
.controls-intro { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.controls-intro h3 { margin: 0; font-size: 17px; font-weight: 800; }
.controls-intro p { margin: 0; font-size: 14px; line-height: 1.55; color: #5b6076; }
.link-button {
  flex: none; border: none; background: transparent; padding: 0; color: #4543b8;
  font-size: 13px; font-weight: 700; cursor: pointer; font-family: inherit;
}
.lever-row { display: flex; align-items: flex-end; gap: 14px; }
.lever { display: flex; flex-direction: column; gap: 7px; min-width: 0; }
.lever-grow { flex-grow: 1; }
.lever-age { flex: none; width: 190px; }
.lever label, .lever-label { font-size: 13px; font-weight: 700; color: #3d4159; }
.field {
  display: flex; align-items: center; gap: 8px; height: 46px; padding: 0 14px;
  border: 1px solid #d9dbe9; border-radius: 10px; background: #fff;
}
.field.changed { border-color: #9c9af0; background: #fbfbff; }
.field:focus-within { outline: 2px solid #5e5ce6; outline-offset: 1px; }
.field input {
  flex-grow: 1; min-width: 0; border: none; outline: none; background: transparent;
  font-size: 16px; font-weight: 700; color: #1a1a2e; font-variant-numeric: tabular-nums; font-family: inherit;
}
.unit { flex: none; font-size: 13px; color: #6e738c; }
.lever-diff { font-size: 12px; color: #6e738c; }
.lever-diff.changed { color: #4543b8; font-weight: 700; }
.disclosure {
  display: flex; align-items: center; justify-content: space-between; gap: 10px; height: 46px;
  padding: 0 14px; border: 1px solid #d9dbe9; border-radius: 10px; background: #fff;
  cursor: pointer; font-family: inherit; text-align: left;
}
.disclosure-value {
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-size: 15px; font-weight: 700; color: #1a1a2e; font-variant-numeric: tabular-nums;
}
.disclosure-action { flex: none; font-size: 13px; font-weight: 700; color: #4543b8; }
.primary-button {
  flex: none; height: 46px; padding: 0 22px; border: none; border-radius: 10px; background: #5e5ce6;
  color: #fff; font-size: 15px; font-weight: 800; cursor: pointer; font-family: inherit;
}
.expense-periods { border: 1px solid #edeef5; border-radius: 12px; padding: 14px 16px; margin: 0; background: #fbfbfe; }
.expense-periods legend { padding: 0 6px; font-size: 13px; font-weight: 700; color: #3d4159; }
.expense-row { display: grid; grid-template-columns: 78px auto 78px minmax(120px, 1fr) auto auto; gap: 8px; align-items: center; margin-bottom: 8px; }
.expense-row input { min-width: 0; border: 1px solid #d9dbe9; border-radius: 8px; padding: 8px; font-family: inherit; font-size: 14px; }
.expense-row button, .row-button { border: 0; border-radius: 8px; padding: 8px 12px; background: #eeeefe; color: #4543b8; font-weight: 700; cursor: pointer; font-family: inherit; font-size: 13px; }
button:disabled { cursor: not-allowed; opacity: .5; }
.form-error { margin: 0; font-size: 13px; font-weight: 700; color: #b4232a; }

/* ── 가정 ── */
.assumptions { border: 1px solid #e9eaf2; border-radius: 14px; background: #fbfbfe; padding: 14px 24px; }
.assumptions summary {
  display: flex; align-items: center; justify-content: space-between; gap: 20px;
  cursor: pointer; list-style: none;
}
.assumptions summary::-webkit-details-marker { display: none; }
.assumption-line { font-size: 13px; line-height: 1.6; color: #5b6076; }
.assumption-more { flex: none; font-size: 13px; font-weight: 700; color: #4543b8; }
.assumptions ul { margin: 14px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 7px; }
.assumptions li { font-size: 13px; line-height: 1.55; color: #5b6076; }
.assumptions p { margin: 10px 0 0; font-size: 12px; line-height: 1.55; color: #6e738c; }
.notice-where { border-radius: 6px; padding: 2px 7px; background: rgba(26, 31, 60, 0.07); font-size: 12px; font-weight: 700; color: #3d4159; }

/* ── 상태 ── */
.state-card {
  min-height: 260px; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 12px; border: 1px solid #e9eaf2; border-radius: 16px; padding: 24px; background: #fff;
  color: #5b6076; font-size: 15px; text-align: center;
}
.state-card.error { color: #b4232a; }
.state-message { margin: 0; font-weight: 700; }
.state-hint { margin: 0; font-size: 13px; color: #5b6076; }
.state-card :deep(.issue-list) { max-width: 520px; }
.warning { display: flex; flex-direction: column; gap: 8px; border-radius: 12px; padding: 18px; background: #fff7ed; color: #8a3c0b; }

.blocked {
  box-sizing: border-box; padding: 36px 40px; border: 1px solid #e9eaf2; border-radius: 16px;
  background: #fff; display: flex; align-items: center; gap: 40px;
}
.blocked-icon {
  flex: none; width: 96px; height: 96px; border-radius: 24px; background: #f4f4fd;
  display: flex; align-items: center; justify-content: center;
}
.blocked-icon svg { width: 44px; height: 44px; }
.blocked-body { flex-grow: 1; min-width: 0; display: flex; flex-direction: column; gap: 18px; }
.blocked-copy { display: flex; flex-direction: column; gap: 8px; }
.blocked-copy h3 { margin: 0; font-size: 22px; font-weight: 800; letter-spacing: -0.3px; }
.blocked-copy .state-message { font-size: 15px; line-height: 1.65; font-weight: 400; color: #5b6076; }
.blocked :deep(.issue-list) { gap: 9px; }
.blocked :deep(.issue-list li) {
  box-sizing: border-box; padding: 13px 16px; border: 1px solid #edeef5; border-radius: 12px;
  background: #fbfbfe; font-size: 15px; color: #1a1a2e;
}
.blocked :deep(.issue-where) { background: #eceafc; color: #3b3192; border-radius: 7px; padding: 3px 9px; }
.blocked-actions { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.blocked-hint { font-size: 13px; color: #6e738c; }
.state-link {
  display: inline-flex; align-items: center; height: 46px; padding: 0 22px; border-radius: 10px;
  background: #5e5ce6; color: #fff; font-size: 15px; font-weight: 800; text-decoration: none;
}

/* ── 슬라이드(compact): 같은 내용을 좁은 상자 안에 넣는다. 기능은 하나도 빼지 않는다. ── */
/* 자식 컴포넌트(차트 카드)의 루트도 부모의 scope 속성과 compact 클래스를 함께 갖는다.
   그래서 이 규칙은 반드시 패널 루트로 한정해야 차트 카드에 height:100% 가 새지 않는다. */
.simulation-panel.compact { gap: 10px; height: 100%; overflow-y: auto; }
/* 슬라이드는 높이가 고정이라 세로 flex 자식이 줄어들며 찌그러진다.
   줄이지 말고 패널이 스크롤되게 둔다. */
.simulation-panel.compact > * { flex: none; }
.compact .panel-header h2 { font-size: 17px; }
.compact .ghost-button { height: 30px; padding: 0 10px; font-size: 12px; }
.compact .headline { flex-direction: column; border-radius: 12px; }
.compact .headline-verdict { width: auto; padding: 12px 14px; gap: 6px; border-right: none; }
.compact .tone-danger .headline-verdict { border-bottom: 1px solid #f3dede; }
.compact .tone-caution .headline-verdict { border-bottom: 1px solid #efe2cc; }
.compact .tone-safe .headline-verdict { border-bottom: 1px solid #dceadf; }
.compact .tone-neutral .headline-verdict { border-bottom: 1px solid #e6e7f0; }
.compact .verdict-value strong { font-size: 30px; letter-spacing: -1px; }
.compact .verdict-delta { font-size: 14px; }
.compact .verdict-chip, .compact .verdict-support { font-size: 11px; }
.compact .headline-copy { padding: 12px 14px; gap: 6px; }
.compact .headline-title { font-size: 14px; }
.compact .headline-detail { font-size: 12px; }
.compact .controls { padding: 12px 14px; gap: 10px; border-radius: 12px; }
.compact .controls-intro h3 { font-size: 14px; }
.compact .lever-row { flex-direction: column; align-items: stretch; gap: 10px; }
.compact .lever-age { width: auto; }
.compact .field, .compact .disclosure, .compact .primary-button { height: 38px; }
.compact .field input, .compact .disclosure-value { font-size: 14px; }
.compact .primary-button { font-size: 13px; }
.compact .expense-row { grid-template-columns: 60px auto 60px minmax(80px, 1fr) auto auto; gap: 5px; }
.compact .expense-row input, .compact .expense-row button, .compact .row-button { padding: 5px 7px; font-size: 12px; }
.compact .assumptions { padding: 10px 14px; border-radius: 12px; }
.compact .assumption-line, .compact .assumption-more { font-size: 11px; }
.compact .state-card { min-height: 0; gap: 8px; padding: 14px; font-size: 13px; border-radius: 12px; }
.compact .blocked { flex-direction: column; align-items: flex-start; gap: 14px; padding: 16px; border-radius: 12px; }
.compact .blocked-icon { width: 48px; height: 48px; border-radius: 14px; }
.compact .blocked-icon svg { width: 24px; height: 24px; }
.compact .blocked-copy h3 { font-size: 15px; }
.compact .blocked-copy .state-message { font-size: 13px; }
.compact .blocked :deep(.issue-list li) { padding: 8px 10px; font-size: 13px; }
.compact .state-link { height: 34px; padding: 0 14px; font-size: 13px; }

@media (max-width: 860px) {
  .headline { flex-direction: column; }
  .headline-verdict { width: auto; border-right: none !important; }
  .lever-row { flex-direction: column; align-items: stretch; }
  .lever-age { width: auto; }
  .blocked { flex-direction: column; align-items: flex-start; gap: 20px; }
  .expense-row { grid-template-columns: 1fr auto 1fr; }
  .expense-row input:nth-of-type(3) { grid-column: 1 / 3; }
}
</style>
