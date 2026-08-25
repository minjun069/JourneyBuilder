<script setup>
import { computed, ref } from 'vue'
import ExpensesForm from '@/components/forms/ExpensesForm.vue'
import FinancialAssetsForm from '@/components/forms/FinancialAssetsForm.vue'
import FinancialGoalsForm from '@/components/forms/FinancialGoalsForm.vue'
import FinancialInfoForm from '@/components/forms/FinancialInfoForm.vue'
import FinancialSatisfactionForm from '@/components/forms/FinancialSatisfactionForm.vue'
import IncomeForm from '@/components/forms/IncomeForm.vue'
import LiabilitiesForm from '@/components/forms/LiabilitiesForm.vue'
import PensionAssetsForm from '@/components/forms/PensionAssetsForm.vue'
import RealEstateForm from '@/components/forms/RealEstateForm.vue'
import BasicInfoForm from '@/components/forms/BasicInfoForm.vue'
import FinancialMindsetForm from '@/components/forms/FinancialMindsetForm.vue'

// AiChatView의 STEP_FORM_MAP과 동일한 순서/제목 (src/views/ai/AiChatView.vue 참고)
const LINKED_FORMS = [
  { key: 'BasicInfoForm', title: '기본 정보', component: BasicInfoForm },
  { key: 'FinancialMindsetForm', title: '경제 관념', component: FinancialMindsetForm },
  { key: 'FinancialSatisfactionForm', title: '재무 만족도', component: FinancialSatisfactionForm },
  { key: 'FinancialInfoForm', title: '재무 정보 안내', component: FinancialInfoForm },
  { key: 'FinancialAssetsForm', title: '금융자산 현황', component: FinancialAssetsForm },
  { key: 'PensionAssetsForm', title: '연금자산 현황', component: PensionAssetsForm },
  { key: 'RealEstateForm', title: '부동산자산 현황', component: RealEstateForm },
  { key: 'LiabilitiesForm', title: '부채 현황 정리', component: LiabilitiesForm },
  { key: 'IncomeForm', title: '소득 현황 조사', component: IncomeForm },
  { key: 'ExpensesForm', title: '지출 현황 분석', component: ExpensesForm },
  { key: 'FinancialGoalsForm', title: '재무목표', component: FinancialGoalsForm },
]

const ALL_FORMS = [...LINKED_FORMS]

const activeKey = ref(LINKED_FORMS[0].key)
const resetTick = ref(0)
const formRef = ref(null)

const activeForm = computed(() => ALL_FORMS.find((f) => f.key === activeKey.value) ?? ALL_FORMS[0])

const formStateJson = computed(() => {
  const form = formRef.value?.form
  if (!form) return '{}'
  try {
    return JSON.stringify(form, null, 2)
  } catch {
    return String(form)
  }
})

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
  <section class="form-lab">
    <aside class="lab-sidebar">
      <h2 class="lab-sidebar-title">Form Lab</h2>
      <p class="lab-sidebar-sub">src/components/forms ({{ ALL_FORMS.length }})</p>

      <div class="lab-group">
        <div class="lab-group-label">AI 상담 플로우 연결됨</div>
        <ul class="lab-list">
          <li v-for="f in LINKED_FORMS" :key="f.key">
            <button type="button" class="lab-item" :class="{ active: f.key === activeKey }" @click="select(f.key)">
              <span class="lab-item-title">{{ f.title }}</span>
              <span class="lab-item-file">{{ f.key }}.vue</span>
            </button>
          </li>
        </ul>
      </div>

    </aside>

    <div class="lab-main">
      <div class="lab-toolbar">
        <div>
          <span class="lab-doc-ref">FORM PLAYGROUND</span>
          <h3 class="lab-doc-title">{{ activeForm.title }} <span class="lab-doc-file">{{ activeForm.key }}.vue</span></h3>
        </div>
        <button type="button" class="lab-reset-btn" @click="reset">초기화</button>
      </div>

      <div class="lab-panels">
        <div class="lab-preview">
          <component :is="activeForm.component" ref="formRef" :key="`${activeForm.key}-${resetTick}`" />
        </div>
        <div class="lab-state">
          <div class="lab-state-header">실시간 form 상태</div>
          <pre class="lab-state-json">{{ formStateJson }}</pre>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.form-lab {
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

.lab-group + .lab-group {
  margin-top: 18px;
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

.lab-panels {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 20px;
}

.lab-preview {
  flex: 1.2;
  min-width: 0;
  overflow-y: auto;
  border: 1px solid #b0a99e;
  border-radius: 5px;
  background: #fff;
  padding: 18px 20px;
}

.lab-state {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid #e6e8f0;
  border-radius: 5px;
  background: #1a1f3c;
  overflow: hidden;
}

.lab-state-header {
  flex-shrink: 0;
  padding: 10px 14px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #9498b5;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.lab-state-json {
  flex: 1;
  margin: 0;
  padding: 14px;
  overflow: auto;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 12.5px;
  line-height: 1.6;
  color: #c9d1ff;
  white-space: pre;
}
</style>
