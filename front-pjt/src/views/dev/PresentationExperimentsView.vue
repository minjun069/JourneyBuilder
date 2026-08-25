<script setup>
import { computed, ref, onMounted } from 'vue'
import { useAiStore } from '@/stores/ai'

const ai = useAiStore()
onMounted(() => ai.fetchPresentationData())
import BalanceSheetSlide from '@/components/presentation/BalanceSheetSlide.vue'
import CashFlowSlide from '@/components/presentation/CashFlowSlide.vue'
import FinancialGoalSlide from '@/components/presentation/FinancialGoalSlide.vue'
import FinancialIndicatorsSlide from '@/components/presentation/FinancialIndicatorsSlide.vue'
import LifetimeCashflowSlide from '@/components/presentation/LifetimeCashflowSlide.vue'
import PensionScheduleSlide from '@/components/presentation/PensionScheduleSlide.vue'
import RequiredSavingsSlide from '@/components/presentation/RequiredSavingsSlide.vue'
import RetirementFundSlide from '@/components/presentation/RetirementFundSlide.vue'
import RetirementReadinessSlide from '@/components/presentation/RetirementReadinessSlide.vue'

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
    desc: '각 재무목표별 달성률을 프로그레스 바로 표시하는 슬라이드.',
  },
  {
    key: 'RetirementFundSlide',
    title: '은퇴 필요자금',
    component: RetirementFundSlide,
    desc: '은퇴 후 필요 자금의 출처(연금·저축·투자)를 분류 분석하는 슬라이드.',
  },
  {
    key: 'RetirementReadinessSlide',
    title: '은퇴 준비자금',
    component: RetirementReadinessSlide,
    desc: '게이지 차트로 현재 은퇴 준비율과 부족액을 한눈에 보여주는 슬라이드.',
  },
  {
    key: 'PensionScheduleSlide',
    title: '연금 수령 스케줄',
    component: PensionScheduleSlide,
    desc: '개인·퇴직·국민연금 수령 시작 연령과 월 수령액을 타임라인으로 나타내는 슬라이드.',
  },
  {
    key: 'LifetimeCashflowSlide',
    title: '생애 현금흐름 및 ',
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

const activeSlide = computed(() => SLIDES.find((s) => s.key === activeKey.value) ?? SLIDES[0])

function select(key) {
  if (key === activeKey.value) return
  activeKey.value = key
  resetTick.value++
}

function reset() {
  resetTick.value++
}
</script>

<template>
  <section class="diag-lab">
    <aside class="lab-sidebar">
      <h2 class="lab-sidebar-title">Presentation Lab</h2>
      <p class="lab-sidebar-sub">src/components/presentation ({{ SLIDES.length }})</p>

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
      <div class="lab-toolbar">
        <div>
          <span class="lab-doc-ref">DIAGNOSIS PLAYGROUND</span>
          <h3 class="lab-doc-title">
            {{ activeSlide.title }}
            <span class="lab-doc-file">{{ activeSlide.key }}.vue</span>
          </h3>
        </div>
        <button type="button" class="lab-reset-btn" @click="reset">초기화</button>
      </div>

      <div class="lab-panels">
        <div class="lab-preview">
          <component
            :is="activeSlide.component"
            :key="`${activeSlide.key}-${resetTick}`"
          />
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
  flex: 1.5;
  min-width: 0;
  overflow-y: auto;
  border: 1px solid #b0a99e;
  border-radius: 5px;
  background: #fff;
  padding: 18px 20px;
}

/* ── Info Panel ── */
.lab-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid #e6e8f0;
  border-radius: 5px;
  background: #1a1f3c;
  overflow: hidden;
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
</style>
