<script setup>
import { computed } from 'vue'
import { PROCESS_STEPS, STEP_IDS } from '@/views/ai/data/processSteps.js'

const props = defineProps({
  currentStepId: { type: String, required: true },
  visitedStepIds: { default: () => new Set() },
})

const emit = defineEmits(['step-click'])

const currentIndex = computed(() => STEP_IDS.indexOf(props.currentStepId))
const currentStep = computed(() => PROCESS_STEPS.find((s) => s.id === props.currentStepId))
const currentPhase = computed(() => currentStep.value?.phase || 'intro')

function isVisible(step) {
  if (step.depth === 0) return true
  if (step.depth === 1) return step.phase === currentPhase.value
  if (step.depth === 2) {
    return (
      currentPhase.value === 'collection' &&
      (props.currentStepId === 'financial-info' || currentStep.value?.depth === 2)
    )
  }
  return false
}

function getStatus(step) {
  if (STEP_IDS.indexOf(step.id) === currentIndex.value) return 'active'
  if (props.visitedStepIds.has(step.id)) return 'done'
  return 'pending'
}

function handleClick(step) {
  emit('step-click', step)
}
</script>

<template>
  <aside class="process-sidebar">
    <div class="sidebar-header">진행 프로세스</div>
    <nav class="step-list">
      <template v-for="step in PROCESS_STEPS" :key="step.id">
        <button
          v-show="isVisible(step)"
          class="step-item"
          :class="[`depth-${step.depth}`, `status-${getStatus(step)}`]"
          type="button"
          @click="handleClick(step)"
        >
          <span class="step-indicator">
            <svg v-if="getStatus(step) === 'done'" viewBox="0 0 10 10" fill="none">
              <circle cx="5" cy="5" r="4.5" fill="#5e5ce6" />
              <path d="M2.5 5l1.8 1.8L7.5 3.5" stroke="#fff" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <svg v-else-if="getStatus(step) === 'active'" viewBox="0 0 10 10" fill="none">
              <circle cx="5" cy="5" r="4.5" fill="#ffffff" />
              <circle cx="5" cy="5" r="2.5" fill="#5e5ce6" />
            </svg>
            <svg v-else viewBox="0 0 10 10" fill="none">
              <circle cx="5" cy="5" r="4" stroke="#4b5563" stroke-width="1.5" />
            </svg>
          </span>
          <span class="step-label">{{ step.label }}</span>
        </button>
      </template>
    </nav>
  </aside>
</template>

<style scoped>
.process-sidebar {
  width: 230px;
  flex-shrink: 0;
  background: #111827;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.sidebar-header {
  padding: 16px 16px 12px;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: #4b5563;
  border-bottom: 1px solid #1f2937;
  flex-shrink: 0;
}

.step-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 0 16px;
  scrollbar-width: thin;
  scrollbar-color: #374151 transparent;
}

/* ── Step items ── */
.step-item {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  width: 100%;
  text-align: left;
  background: none;
  border: none;
  cursor: pointer;
  transition: background 0.12s;
}

.step-item:hover { background: #1f2937; }

.depth-0 {
  padding: 7px 16px;
  margin-top: 10px;
}

.depth-0:first-child { margin-top: 0; }

.depth-1 { padding: 5px 16px 5px 26px; }
.depth-2 { padding: 4px 16px 4px 38px; }

/* ── Indicator SVG ── */
.step-indicator {
  width: 11px;
  height: 11px;
  flex-shrink: 0;
  margin-top: 3px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.step-indicator svg { width: 11px; height: 11px; overflow: visible; }

/* ── Labels ── */
.step-label {
  line-height: 1.45;
  white-space: pre-wrap;
  transition: color 0.15s;
}

/* depth-0: 대제목 */
.depth-0 .step-label {
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.01em;
}

/* depth-1: 중제목 */
.depth-1 .step-label {
  font-size: 13px;
  font-weight: 500;
}

/* depth-2: 소제목 */
.depth-2 .step-label {
  font-size: 12px;
  font-weight: 400;
}

/* ── 상태별 색상 ── */
.status-done .step-label    { color: #6b7280; }
.status-active .step-label  { color: #f9fafb; font-weight: 700; }
.status-pending .step-label { color: #9ca3af; }

/* depth-0은 항상 눈에 띄게 */
.depth-0.status-done .step-label    { color: #6b7280; font-weight: 700; }
.depth-0.status-pending .step-label { color: #9ca3af; font-weight: 700; }
</style>
