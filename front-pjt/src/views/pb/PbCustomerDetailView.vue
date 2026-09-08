<template>
  <div class="customer-detail">
    <button type="button" class="btn-back" @click="router.back()">← 고객 리스트</button>

    <p v-if="loading" class="placeholder">불러오는 중…</p>
    <p v-else-if="error" class="placeholder error">{{ error }}</p>

    <template v-else-if="detail">
      <!-- FR-04 프로필 헤더 -->
      <header class="profile-head">
        <div>
          <h2 class="name">{{ detail.profile.name }}</h2>
          <p class="sub">
            {{ detail.basic_info?.age ?? '—' }}세 ·
            {{ detail.basic_info?.job ?? '직업 미입력' }} ·
            {{ detail.basic_info?.marital_status ?? '—' }} ·
            니즈: {{ detail.basic_info?.need ?? '—' }}
          </p>
          <p class="sub muted">
            {{ detail.profile.email }}
            <template v-if="detail.profile.phone"> · {{ detail.profile.phone }}</template>
          </p>
          <div class="tags" v-if="detail.basic_info?.tendency?.length">
            <span v-for="tag in detail.basic_info.tendency" :key="tag" class="tag">{{ tag }}</span>
          </div>
        </div>
        <div class="head-right">
          <span class="badge" :class="statusMeta.cls">{{ statusMeta.label }}</span>
          <span v-if="detail.session" class="progress-note">
            입력 진행률 {{ detail.session.progress_pct }}%
          </span>
        </div>
      </header>

      <template v-if="derived">
        <!-- FR-04 요약 StatCard -->
        <section class="stat-row">
          <StatCard label="순자산" :amount="derived.net_worth" colorType="accent" isHighlight />
          <StatCard
            label="월 잉여현금흐름"
            :amount="derived.monthly_surplus"
            :colorType="derived.monthly_surplus < 0 ? 'negative' : 'positive'"
          />
          <div class="stat-card ratio-card">
            <div class="stat-label">부채비율</div>
            <div class="value-container">
              <div class="stat-value" :class="derived.debt_to_asset_ratio >= 80 ? 'negative' : 'positive'">
                {{ derived.debt_to_asset_ratio }}
              </div>
              <div class="stat-unit">%</div>
            </div>
          </div>
          <div class="stat-card ratio-card">
            <div class="stat-label">위험 지표</div>
            <div class="value-container">
              <div class="stat-value" :class="derived.risk_count >= 3 ? 'negative' : 'positive'">
                {{ derived.risk_count }}
              </div>
              <div class="stat-unit">/ 7</div>
            </div>
          </div>
        </section>

        <!-- FR-05 자산 구성 도넛 + FR-04 재무지표 게이지 -->
        <section class="card">
          <h3 class="card-title">자산 구성</h3>
          <AssetPieEcharts v-if="assetBreakdown.length" :breakdown="assetBreakdown" />
          <p v-else class="placeholder">자산 데이터가 없습니다.</p>
        </section>

        <section class="card">
          <h3 class="card-title">가계재무지표 7종</h3>
          <div class="gauge-grid">
            <div v-for="item in gaugeIndicators" :key="item.key" class="gauge-cell">
              <FinancialGaugeEcharts :indicator="item" />
              <span class="gauge-grade" :class="gradeClass(item.grade)">{{ item.grade }}</span>
            </div>
          </div>
        </section>

        <!-- FR-05 명세 테이블 -->
        <section class="card">
          <div class="tab-bar">
            <button
              v-for="tab in TABS" :key="tab.key" type="button"
              class="tab" :class="{ active: activeTab === tab.key }"
              @click="activeTab = tab.key"
            >
              {{ tab.label }} <span class="tab-count">{{ tabRows(tab.key).length }}</span>
            </button>
          </div>
          <table class="detail-table" v-if="tabRows(activeTab).length">
            <thead>
              <tr>
                <th v-for="col in TAB_COLUMNS[activeTab]" :key="col.key" :class="{ num: col.numeric }">
                  {{ col.label }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, idx) in tabRows(activeTab)" :key="idx">
                <td v-for="col in TAB_COLUMNS[activeTab]" :key="col.key" :class="{ num: col.numeric }">
                  {{ col.numeric ? formatEok(row[col.key]) : (row[col.key] ?? '—') }}
                </td>
              </tr>
            </tbody>
          </table>
          <p v-else class="placeholder">입력된 항목이 없습니다.</p>
        </section>
      </template>

      <section v-else class="card">
        <p class="placeholder">아직 재무 데이터가 입력되지 않은 고객입니다. (상담 상태: {{ statusMeta.label }})</p>
      </section>

      <!-- FR-05 재무 목표 -->
      <section class="card" v-if="detail.goals.periods.length || detail.goals.milestones.length">
        <h3 class="card-title">재무 목표</h3>
        <div class="goal-grid">
          <div v-for="period in detail.goals.periods" :key="'p' + period.id" class="goal-block">
            <h4 class="goal-title">
              {{ period.phase === 'after' ? '은퇴 후' : '은퇴 전' }}
              ({{ period.from_age }}~{{ period.to_age }}세)
            </h4>
            <ul class="goal-items">
              <li v-for="(item, i) in period.items" :key="i">
                {{ item.name }} — 월 {{ formatEok(item.amount) }}
              </li>
            </ul>
          </div>
          <div v-if="detail.goals.milestones.length" class="goal-block">
            <h4 class="goal-title">마일스톤</h4>
            <ul class="goal-items">
              <li v-for="ms in detail.goals.milestones" :key="'m' + ms.id">
                {{ ms.age }}세 · {{ ms.content }} — {{ formatEok(ms.amount) }}
                <span class="ms-cat">{{ ms.category }}</span>
              </li>
            </ul>
          </div>
        </div>
      </section>

      <!-- FR-05 마인드셋·만족도 -->
      <div class="two-col">
        <section class="card" v-if="detail.mindset">
          <h3 class="card-title">경제 마인드셋</h3>
          <dl class="mindset-list">
            <template v-for="field in MINDSET_FIELDS" :key="field.key">
              <template v-if="detail.mindset[field.key]">
                <dt>{{ field.label }}</dt>
                <dd>{{ detail.mindset[field.key] }}</dd>
              </template>
            </template>
          </dl>
        </section>

        <section class="card" v-if="detail.satisfaction">
          <h3 class="card-title">재무 만족도 <span class="title-sub">(1~5점)</span></h3>
          <div class="score-list">
            <div v-for="field in SATISFACTION_FIELDS" :key="field.key" class="score-row">
              <span class="score-label">{{ field.label }}</span>
              <div class="score-bar">
                <div class="score-fill" :style="{ width: (detail.satisfaction[field.key] ?? 0) * 20 + '%' }" />
              </div>
              <span class="score-num">{{ detail.satisfaction[field.key] ?? '—' }}</span>
            </div>
          </div>
        </section>
      </div>

      <!-- 세션 이력 (Q-05) -->
      <section class="card" v-if="detail.sessions.length">
        <h3 class="card-title">상담 세션 이력</h3>
        <table class="detail-table">
          <thead>
            <tr><th>세션</th><th>상태</th><th>시작</th><th>완료</th></tr>
          </thead>
          <tbody>
            <tr v-for="s in detail.sessions" :key="s.id">
              <td>#{{ s.id }}<span v-if="s.id === detail.session?.id" class="rep-mark"> · 대표</span></td>
              <td>{{ STATUS_META[s.status]?.label ?? s.status }}</td>
              <td>{{ formatDateTime(s.created_at) }}</td>
              <td>{{ formatDateTime(s.completed_at) }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import StatCard from '@/components/dashboard/StatCard.vue'
import AssetPieEcharts from '@/components/charts/AssetPieEcharts.vue'
import FinancialGaugeEcharts from '@/components/charts/FinancialGaugeEcharts.vue'
import { makeIndicator } from '@/components/charts/financialIndicatorSchema'
import { getCustomer } from '@/api/admin'
import { formatEok } from '@/utils/money'

const STATUS_META = {
  completed:   { label: '상담 완료', cls: 'done' },
  in_progress: { label: '진행 중', cls: 'progress' },
  onboarding:  { label: '온보딩', cls: 'onboarding' },
  abandoned:   { label: '이탈', cls: 'abandoned' },
}
const ASSET_COLORS = {
  '현금/저축성 자산': '#5e5ce6',
  '실물자산': '#ff9f40',
  '투자자산': '#4bc0c0',
  '부동산': '#ff6384',
  '연금자산': '#36a2eb',
}
const TABS = [
  { key: 'assets', label: '자산' },
  { key: 'liabilities', label: '부채' },
  { key: 'incomes', label: '소득' },
  { key: 'expenses', label: '지출' },
]
const TAB_COLUMNS = {
  assets: [
    { key: 'category', label: '분류' },
    { key: 'name', label: '항목' },
    { key: 'current_value', label: '평가액', numeric: true },
    { key: 'monthly_savings', label: '월 적립', numeric: true },
  ],
  liabilities: [
    { key: 'category', label: '분류' },
    { key: 'name', label: '항목' },
    { key: 'balance', label: '잔액', numeric: true },
    { key: 'interest_rate', label: '금리(%)' },
    { key: 'rate_type', label: '금리 유형' },
  ],
  incomes: [
    { key: 'category', label: '분류' },
    { key: 'type', label: '항목' },
    { key: 'amount', label: '월 금액', numeric: true },
  ],
  expenses: [
    { key: 'category', label: '분류' },
    { key: 'name', label: '항목' },
    { key: 'amount', label: '월 금액', numeric: true },
  ],
}
const MINDSET_FIELDS = [
  { key: 'values', label: '가치관' },
  { key: 'past_experience', label: '과거 경험' },
  { key: 'current_situation', label: '현 재무상황' },
  { key: 'current_challenge', label: '당면 과제' },
  { key: 'invest_philosophy', label: '투자 철학' },
  { key: 'future_goals', label: '미래 목표' },
]
const SATISFACTION_FIELDS = [
  { key: 'cashflow_score', label: '현금흐름 관리' },
  { key: 'risk_score', label: '위험 관리' },
  { key: 'investment_score', label: '투자' },
  { key: 'management_score', label: '재무관리' },
  { key: 'inheritance_score', label: '상속' },
]

const route = useRoute()
const router = useRouter()

const detail = ref(null)
const loading = ref(true)
const error = ref('')
const activeTab = ref('assets')

// 자산·현금흐름 미입력이면 파생부를 숨긴다 (NFR-04)
const derived = computed(() =>
  detail.value?.derived?.has_financial_input ? detail.value.derived : null)

const statusMeta = computed(() =>
  STATUS_META[detail.value?.session?.status] ?? { label: '세션 없음', cls: 'none' })

const assetBreakdown = computed(() => {
  const categories = detail.value?.asset_statement?.asset_categories ?? {}
  return Object.values(categories)
    .filter(category => category.total > 0)
    .map(category => ({
      category: category.label,
      amount: category.total,
      color: ASSET_COLORS[category.label] ?? '#9ca3af',
    }))
})

// derived.indicators 의 표시값을 기존 게이지 스키마(makeIndicator)에 주입 (FR-04)
const gaugeIndicators = computed(() =>
  (derived.value?.indicators ?? []).map(item => ({
    ...makeIndicator(item.key, item.value, derived.value.age),
    grade: item.grade,
  })))

function gradeClass(grade) {
  return { '양호': 'good', '주의': 'warn', '위험': 'risk' }[grade] ?? ''
}

function categoryItems(categoriesObj, extra = {}) {
  return Object.values(categoriesObj ?? {}).flatMap(category =>
    (category.items ?? []).map(item => ({ category: category.label, ...item, ...extra })))
}

const tabData = computed(() => {
  const bs = detail.value?.asset_statement
  const cf = detail.value?.cash_flow
  return {
    assets: categoryItems(bs?.asset_categories),
    liabilities: categoryItems(bs?.liability_categories),
    incomes: categoryItems(cf?.inflow_categories),
    // 지출 탭은 소비·저축·상환 흐름 전체 (자산 월 적립 포함)
    expenses: categoryItems(cf?.outflow_categories),
  }
})

function tabRows(key) {
  return tabData.value[key] ?? []
}

function formatDateTime(value) {
  if (!value) return '—'
  return new Date(value).toLocaleDateString('ko-KR', {
    year: 'numeric', month: 'short', day: 'numeric',
  })
}

onMounted(async () => {
  try {
    const body = await getCustomer(route.params.id)
    detail.value = body.data
  } catch (err) {
    error.value = err.response?.status === 404
      ? '고객을 찾을 수 없습니다.'
      : '고객 정보를 불러오지 못했습니다.'
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.btn-back {
  border: 0;
  background: transparent;
  color: #5e5ce6;
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
  padding: 0;
  margin-bottom: 16px;
}

.profile-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  background: #fff;
  border: 1px solid #ebebf5;
  border-radius: 14px;
  padding: 22px 26px;
  margin-bottom: 16px;
}

.name { font-size: 22px; font-weight: 700; color: #1a1a2e; margin: 0 0 6px; }
.sub { font-size: 14px; color: #555; margin: 0 0 4px; }
.sub.muted { color: #999; font-size: 12.5px; margin-bottom: 10px; }

.tags { display: flex; gap: 6px; flex-wrap: wrap; }
.tag {
  font-size: 12px;
  color: #5e5ce6;
  background: #eeeefc;
  border-radius: 999px;
  padding: 3px 10px;
}

.head-right { display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }

.badge {
  font-size: 13px;
  font-weight: 600;
  border-radius: 999px;
  padding: 5px 14px;
}
.badge.done       { background: #e6f6ee; color: #23855c; }
.badge.progress   { background: #eeeefc; color: #5e5ce6; }
.badge.onboarding { background: #fff3e0; color: #b06f14; }
.badge.abandoned,
.badge.none       { background: #f2f2f2; color: #888; }

.progress-note { font-size: 12.5px; color: #999; }

.stat-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px;
  margin-bottom: 16px;
}

/* StatCard 와 동일한 룩의 비율 카드 (금액이 아니라 StatCard 재사용 불가) */
.stat-card.ratio-card {
  background: #ffffff;
  border: 1px solid #f0f0f0;
  border-radius: 14px;
  padding: 20px 24px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
  display: flex;
  flex-direction: column;
}
.ratio-card .stat-label {
  font-size: 18px; font-weight: 600; color: #888;
  letter-spacing: 0.5px; margin-bottom: 10px;
}
.ratio-card .value-container { display: flex; align-items: baseline; gap: 4px; }
.ratio-card .stat-value { font-size: 28px; font-weight: 800; line-height: 1.1; color: #1a1a1a; }
.ratio-card .stat-value.negative { color: #e53935; }
.ratio-card .stat-unit { font-size: 14px; font-weight: 600; color: #aaa; }

.card {
  background: #fff;
  border: 1px solid #ebebf5;
  border-radius: 14px;
  padding: 20px 24px;
  margin-bottom: 16px;
}

.card-title { font-size: 16px; font-weight: 700; color: #1a1a2e; margin: 0 0 14px; }
.title-sub { font-size: 12px; font-weight: 400; color: #999; }

.gauge-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px;
}

.gauge-cell { position: relative; }
.gauge-grade {
  position: absolute;
  top: 6px;
  right: 10px;
  font-size: 11.5px;
  font-weight: 700;
  border-radius: 999px;
  padding: 2px 8px;
}
.gauge-grade.good { background: #e6f6ee; color: #23855c; }
.gauge-grade.warn { background: #fff3e0; color: #b06f14; }
.gauge-grade.risk { background: #fdecec; color: #d43a3a; }

.tab-bar { display: flex; gap: 6px; margin-bottom: 12px; }
.tab {
  border: 1px solid #e0dff7;
  background: #fff;
  color: #555;
  font-size: 13px;
  font-weight: 600;
  padding: 7px 14px;
  border-radius: 8px;
  cursor: pointer;
}
.tab.active { background: #5e5ce6; border-color: #5e5ce6; color: #fff; }
.tab-count { font-size: 11px; opacity: 0.75; }

.detail-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13.5px;
}
.detail-table th {
  text-align: left;
  font-size: 12px;
  color: #777;
  font-weight: 600;
  padding: 9px 12px;
  border-bottom: 1px solid #ebebf5;
  background: #fafaff;
  white-space: nowrap;
}
.detail-table th.num { text-align: right; }
.detail-table td {
  padding: 9px 12px;
  border-bottom: 1px solid #f4f4fb;
  color: #333;
}
.detail-table td.num { text-align: right; font-variant-numeric: tabular-nums; }

.rep-mark { color: #5e5ce6; font-size: 12px; font-weight: 600; }

.goal-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 16px;
}
.goal-title { font-size: 14px; font-weight: 700; color: #444; margin: 0 0 8px; }
.goal-items { margin: 0; padding-left: 18px; font-size: 13.5px; color: #555; }
.goal-items li { margin-bottom: 5px; }
.ms-cat {
  font-size: 11px;
  color: #b06f14;
  background: #fff3e0;
  border-radius: 999px;
  padding: 1px 8px;
  margin-left: 6px;
}

.two-col {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}
.two-col .card { margin-bottom: 0; }

.mindset-list { margin: 0; }
.mindset-list dt { font-size: 12.5px; font-weight: 700; color: #888; margin-bottom: 3px; }
.mindset-list dd { font-size: 13.5px; color: #333; margin: 0 0 12px; line-height: 1.5; }

.score-list { display: flex; flex-direction: column; gap: 12px; }
.score-row { display: flex; align-items: center; gap: 10px; }
.score-label { width: 96px; font-size: 13px; color: #555; flex-shrink: 0; }
.score-bar {
  flex: 1;
  height: 8px;
  background: #eeeefc;
  border-radius: 4px;
  overflow: hidden;
}
.score-fill { height: 100%; background: #5e5ce6; border-radius: 4px; }
.score-num { width: 20px; text-align: right; font-size: 13px; font-weight: 700; color: #1a1a2e; }

.placeholder { color: #888; font-size: 14px; }
.placeholder.error { color: #d43a3a; }
</style>
