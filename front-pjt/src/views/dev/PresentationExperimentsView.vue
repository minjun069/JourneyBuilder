<script setup>
import { computed, createApp, h, onBeforeUnmount, onMounted, ref, shallowReactive, toRaw, watch } from 'vue'
import { createPinia, disposePinia, getActivePinia, setActivePinia } from 'pinia'
import PresentationPreview from '@/dev/PresentationPreview.vue'
import BalanceSheetSlide from '@/components/presentation/BalanceSheetSlide.vue'
import CashFlowSlide from '@/components/presentation/CashFlowSlide.vue'
import FinancialGoalSlide from '@/components/presentation/FinancialGoalSlide.vue'
import FinancialIndicatorsSlide from '@/components/presentation/FinancialIndicatorsSlide.vue'
import LifetimeCashflowSlide from '@/components/presentation/LifetimeCashflowSlide.vue'
import PensionScheduleSlide from '@/components/presentation/PensionScheduleSlide.vue'
import RequiredSavingsSlide from '@/components/presentation/RequiredSavingsSlide.vue'
import RetirementFundSlide from '@/components/presentation/RetirementFundSlide.vue'
import RetirementReadinessSlide from '@/components/presentation/RetirementReadinessSlide.vue'
import RetirementSimulationSlide from '@/components/presentation/RetirementSimulationSlide.vue'

const SLIDES = [
  {
    key: 'BalanceSheetSlide',
    title: '재무상태표',
    component: BalanceSheetSlide,
    desc: '자산·부채·순자산을 좌우 컬럼으로 비교하는 재무상태표 슬라이드.',
  },
  {
    key: 'CashFlowSlide',
    title: '현금흐름표',
    component: CashFlowSlide,
    desc: '수입·지출·잉여 현금흐름을 바 차트로 시각화하는 슬라이드.',
  },
  {
    key: 'FinancialIndicatorsSlide',
    title: '가계재무지표',
    component: FinancialIndicatorsSlide,
    desc: '가계재무지표 별 게이지 차트',
  },
  {
    key: 'FinancialGoalSlide',
    title: '재무목표 요약',
    component: FinancialGoalSlide,
    desc: '은퇴 전후 기간별 지출과 일회성 재무목표를 타임라인으로 표시하는 슬라이드.',
  },
  {
    key: 'RetirementFundSlide',
    title: '은퇴 필요자금',
    component: RetirementFundSlide,
    desc: '단계별 은퇴 필요자금과 예상 연금 수령 총액을 표시하는 슬라이드.',
  },
  {
    key: 'RetirementReadinessSlide',
    title: '은퇴 준비자금',
    component: RetirementReadinessSlide,
    desc: '은퇴 시 예상 자산 구성과 필요 은퇴자금 대비 준비율을 보여주는 슬라이드.',
  },
  {
    key: 'RetirementSimulationSlide',
    title: '은퇴 시뮬레이션',
    component: RetirementSimulationSlide,
    desc: '기존 시뮬레이션 계산기의 기준 성공확률·자산 분포 또는 입력 보완 상태.',
  },
  {
    key: 'PensionScheduleSlide',
    title: '연금 수령 스케줄',
    component: PensionScheduleSlide,
    desc: '국민연금의 수령 시작 연령과 나이별 예상 월 수령액을 보여주는 슬라이드.',
  },
  {
    key: 'LifetimeCashflowSlide',
    title: '생애 현금흐름',
    component: LifetimeCashflowSlide,
    desc: '연대별(30~80대) 수입·지출·순자산 변화를 막대 그래프로 보여주는 슬라이드.',
  },
  {
    key: 'RequiredSavingsSlide',
    title: '필요 저축액',
    component: RequiredSavingsSlide,
    desc: '목표 은퇴자금 달성을 위해 매월 적립해야 하는 저축액을 보여주는 슬라이드.',
  },
]

const activeKey = ref(SLIDES[0].key)
const resetTick = ref(0)
const scenarios = ref([])
const scenarioId = ref('normal')
const loadError = ref('')
const loading = ref(false)
const previewElement = ref(null)
const splitPreview = ref(false)
const chartWidth = ref(71.4)
const selectedScenario = computed(() => scenarios.value.find((scenario) => scenario.id === scenarioId.value))
const parentPinia = getActivePinia()
const restoreStoreContext = () => setActivePinia(parentPinia)
const previewState = shallowReactive({ scenario: null, slide: SLIDES[0].component, resetTick: 0, restoreStoreContext })
const previewPinia = createPinia()
let previewApp

const activeSlide = computed(() => SLIDES.find((s) => s.key === activeKey.value) ?? SLIDES[0])

function select(key) {
  if (key === activeKey.value) return
  activeKey.value = key
  resetTick.value++
}

function reset() {
  resetTick.value++
  previewState.scenario = selectedScenario.value ? structuredClone(toRaw(selectedScenario.value)) : null
}

watch(activeSlide, (slide) => { previewState.slide = slide.component })
watch(resetTick, (value) => { previewState.resetTick = value })
watch(selectedScenario, reset)

async function loadBundle() {
  loading.value = true
  loadError.value = ''
  try {
    const response = await fetch('/__dev/presentation-data', { cache: 'no-store' })
    const data = await response.json()
    if (!response.ok) throw new Error(data.message ?? '계산 데이터를 불러오지 못했습니다.')
    if (data.schema_version !== 'presentation-lab-v1' || !Array.isArray(data.scenarios) || !data.scenarios.length) {
      throw new Error('지원하지 않는 계산 데이터입니다. npm run prepare:presentation을 다시 실행해 주세요.')
    }
    scenarios.value = data.scenarios
    if (!data.scenarios.some((scenario) => scenario.id === scenarioId.value)) scenarioId.value = data.scenarios[0].id
  } catch (error) {
    scenarios.value = []
    loadError.value = error.message
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  // A separate Vue application owns its Pinia injection. The real chat/dashboard
  // Store and localStorage are never replaced, even while requests are in flight.
  previewApp = createApp({ render: () => h(PresentationPreview, previewState) })
  previewApp.use(previewPinia)
  previewApp.mount(previewElement.value)
  restoreStoreContext()
  void loadBundle()
})
onBeforeUnmount(() => {
  previewApp?.unmount()
  disposePinia(previewPinia)
  restoreStoreContext()
})
</script>

<template>
  <section class="diag-lab">
    <aside class="lab-sidebar">
      <h2 class="lab-sidebar-title">Presentation Lab</h2>
      <p class="lab-sidebar-sub">가상 입력 · 기존 계산기 · {{ SLIDES.length }}개 화면</p>
      <label class="scenario-control">
        입력 시나리오
        <select v-model="scenarioId" :disabled="loading || !scenarios.length">
          <option v-for="scenario in scenarios" :key="scenario.id" :value="scenario.id">{{ scenario.title }}</option>
        </select>
      </label>
      <p class="scenario-description">{{ selectedScenario?.description }}</p>
      <button class="lab-reset-btn" type="button" :disabled="loading" @click="loadBundle">계산 결과 새로 읽기</button>

      <div class="lab-group">
        <div class="lab-group-label">진단 슬라이드</div>
        <ul class="lab-list">
          <li v-for="s in SLIDES" :key="s.key">
            <button
              type="button"
              class="lab-item"
              :class="{ active: s.key === activeKey }"
              @click="select(s.key)"
            >
              <span class="lab-item-title">{{ s.title }}</span>
              <span class="lab-item-file">{{ s.key }}.vue</span>
            </button>
          </li>
        </ul>
      </div>
    </aside>

    <div class="lab-main">
      <p v-if="loadError" role="alert" class="lab-error">{{ loadError }}</p>
      <div class="lab-toolbar">
        <div>
          <span class="lab-doc-ref">DEV · 실제 DB 저장 및 AI 호출 없음</span>
          <h3 class="lab-doc-title">
            {{ activeSlide.title }}
            <span class="lab-doc-file">{{ activeSlide.key }}.vue</span>
          </h3>
        </div>
        <button type="button" class="lab-reset-btn" @click="reset">초기화</button>
      </div>

      <div class="layout-controls">
        <label><input v-model="splitPreview" type="checkbox"> 채팅 분할 배치</label>
        <label v-if="splitPreview">차트 너비 {{ chartWidth }}%
          <input v-model.number="chartWidth" aria-label="차트 너비" type="range" min="55" max="85" step="0.1">
        </label>
      </div>

      <div class="lab-panels">
        <div class="preview-workspace">
          <div class="lab-preview" :style="{ width: splitPreview ? `${chartWidth}%` : '100%' }">
            <div ref="previewElement" class="preview-root" />
          </div>
          <aside v-if="splitPreview" class="chat-placeholder">
            <strong>AI 채팅 영역</strong>
            <p>차트와 채팅의 배치·너비를 확인하는 미리보기입니다.</p>
            <p>이번 작업에서는 AI 설명을 요청하지 않습니다.</p>
          </aside>
        </div>
        <div class="lab-info">
          <div class="lab-info-header">컴포넌트 정보</div>
          <div class="lab-info-body">
            <div class="info-row">
              <span class="info-label">파일</span>
              <span class="info-value mono">{{ activeSlide.key }}.vue</span>
            </div>
            <div class="info-row">
              <span class="info-label">경로</span>
              <span class="info-value mono">src/components/presentation/</span>
            </div>
            <div class="info-divider"></div>
            <div class="info-desc">{{ activeSlide.desc }}</div>
            <details v-if="selectedScenario" class="input-details">
              <summary>계산에 사용한 사용자 입력</summary>
              <pre>{{ JSON.stringify(selectedScenario.input, null, 2) }}</pre>
              <p>backend-pjt/devtools/presentation_inputs.py 수정 후 npm run prepare:presentation으로 다시 계산합니다.</p>
            </details>
            <div class="info-divider"></div>
            <div class="info-row">
              <span class="info-label">순서</span>
              <span class="info-value">
                {{ SLIDES.findIndex((s) => s.key === activeKey) + 1 }} / {{ SLIDES.length }}
              </span>
            </div>
            <div class="info-nav">
              <button
                type="button"
                class="nav-btn"
                :disabled="SLIDES.findIndex((s) => s.key === activeKey) === 0"
                @click="select(SLIDES[SLIDES.findIndex((s) => s.key === activeKey) - 1].key)"
              >
                이전
              </button>
              <button
                type="button"
                class="nav-btn"
                :disabled="SLIDES.findIndex((s) => s.key === activeKey) === SLIDES.length - 1"
                @click="select(SLIDES[SLIDES.findIndex((s) => s.key === activeKey) + 1].key)"
              >
                다음
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.diag-lab {
  height: calc(100vh - 80px);
  display: flex;
  background: linear-gradient(180deg, #f7f7ff 0%, #ffffff 100%);
}

/* ── Sidebar ── */
.lab-sidebar {
  width: 260px;
  flex-shrink: 0;
  border-right: 1px solid #e6e8f0;
  padding: 20px 16px;
  overflow-y: auto;
  background: #fff;
}

.lab-sidebar-title {
  font-size: 16px;
  font-weight: 800;
  color: #1a1f3c;
}

.lab-sidebar-sub {
  font-size: 11px;
  color: #9498b5;
  margin: 2px 0 18px;
}

.lab-group-label {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #5e5ce6;
  margin-bottom: 8px;
}

.lab-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.lab-item {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 8px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background 0.12s;
}

.lab-item:hover {
  background: #f4f5ff;
}

.lab-item.active {
  background: #5e5ce6;
}

.lab-item-title {
  font-size: 13px;
  font-weight: 700;
  color: #31374a;
}

.lab-item.active .lab-item-title {
  color: #fff;
}

.lab-item-file {
  font-size: 10px;
  color: #9498b5;
}

.lab-item.active .lab-item-file {
  color: #d9d9ff;
}

/* ── Main ── */
.lab-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  padding: 20px 24px;
}

.lab-toolbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 16px;
}

.lab-doc-ref {
  display: block;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  color: #9498b5;
  margin-bottom: 4px;
}

.lab-doc-title {
  font-size: 19px;
  font-weight: 800;
  color: #1a1f3c;
  letter-spacing: -0.01em;
}

.lab-doc-file {
  font-size: 12px;
  font-weight: 600;
  color: #9498b5;
  margin-left: 8px;
}

.lab-reset-btn {
  height: 34px;
  padding: 0 16px;
  border: 1.5px solid #5e5ce6;
  border-radius: 8px;
  background: transparent;
  color: #5e5ce6;
  font: inherit;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
}

.lab-reset-btn:hover {
  background: #5e5ce6;
  color: #fff;
}

/* ── Panels ── */
.lab-panels {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 20px;
}

.lab-preview {
  flex: 0 0 auto;
  min-width: 0;
  overflow-y: auto;
  border: 1px solid #b0a99e;
  border-radius: 5px;
  background: #fff;
  padding: 0;
  box-sizing: border-box;
}

/* ── Info Panel ── */
.lab-info {
  flex: 0 0 auto;
  max-height: 200px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid #e6e8f0;
  border-radius: 5px;
  background: #1a1f3c;
  overflow: auto;
}

.lab-info-header {
  flex-shrink: 0;
  padding: 10px 14px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #9498b5;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.lab-info-body {
  flex: 1;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow-y: auto;
}

.info-row {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.info-label {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #6b7280;
}

.info-value {
  font-size: 13px;
  font-weight: 600;
  color: #c9d1ff;
}

.info-value.mono {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 12px;
}

.info-divider {
  height: 1px;
  background: rgba(255, 255, 255, 0.08);
}

.info-desc {
  font-size: 13px;
  line-height: 1.6;
  color: #a0a8c8;
}

.info-nav {
  display: flex;
  gap: 8px;
  margin-top: auto;
}

.nav-btn {
  flex: 1;
  height: 34px;
  border: 1.5px solid rgba(94, 92, 230, 0.5);
  border-radius: 8px;
  background: transparent;
  color: #a0a8ff;
  font: inherit;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}

.nav-btn:hover:not(:disabled) {
  background: #5e5ce6;
  border-color: #5e5ce6;
  color: #fff;
}

.nav-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.scenario-control { display: grid; gap: 8px; color: #475569; font-size: 13px; }
.scenario-control select { padding: 9px; border: 1px solid #cbd5e1; border-radius: 7px; background: white; }
.scenario-description { font-size: 12px; color: #64748b; margin: 10px 0; }
.lab-group { margin-top: 20px; }
.lab-list { list-style: none; }
.lab-error { background: #fff1f2; color: #9f1239; padding: 12px; border-radius: 8px; }
.layout-controls { display: flex; align-items: center; gap: 20px; margin-bottom: 12px; font-size: 13px; }
.layout-controls label { display: flex; align-items: center; gap: 8px; }
.lab-panels { flex-direction: column; overflow: auto; }
.preview-workspace { display: flex; flex: 1; min-height: 420px; min-width: 0; }
.preview-root { min-height: 100%; height: 100%; }
.chat-placeholder { flex: 1; min-width: 0; padding: 16px; background: #f1f5f9; color: #475569; overflow-wrap: anywhere; font-size: 12px; }
.chat-placeholder p { margin-top: 14px; }
.input-details { margin-top: 14px; font-size: 12px; color: #d4d9ef; }
.input-details summary { cursor: pointer; }
.input-details pre { white-space: pre-wrap; overflow-wrap: anywhere; margin: 10px 0; }
@media (max-width: 900px) {
  .lab-sidebar { width: 190px; }
  .lab-main { padding: 12px; }
  .lab-doc-file { display: none; }
  .layout-controls { flex-wrap: wrap; }
}
</style>
