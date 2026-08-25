<script setup>
import { reactive } from 'vue'

const ITEMS = [
  { key: 'values',           label: '가치관',      placeholder: '돈과 재무에 대한 본인의 가치관을 자유롭게 서술해주세요.' },
  { key: 'pastExperience',   label: '과거경험',    placeholder: '재무적으로 중요했던 과거 경험을 공유해주세요.' },
  { key: 'currentSituation', label: '현 재무상황', placeholder: '현재 재무 상황에 대해 느끼는 점을 서술해주세요.' },
  { key: 'currentChallenge', label: '당면과제',    placeholder: '현재 가장 시급하게 해결하고 싶은 재무적 과제를 설명해주세요.' },
  { key: 'investPhilosophy', label: '투자철학',    placeholder: '투자에 대한 본인의 철학이나 원칙을 서술해주세요.' },
  { key: 'futureGoals',      label: '미래인생목표', placeholder: '재무적 목표와 연관된 미래 인생 목표를 서술해주세요.' },
]

const form = reactive({
  values: '',
  pastExperience: '',
  currentSituation: '',
  currentChallenge: '',
  investPhilosophy: '',
  futureGoals: '',
})

defineExpose({ form })
</script>

<template>
  <div class="doc-fields">
    <div v-for="item in ITEMS" :key="item.key" class="field-row">
      <span class="field-label">{{ item.label }}</span>
      <textarea
        class="field-textarea"
        v-model="form[item.key]"
        :placeholder="item.placeholder"
        rows="3"
      />
    </div>
  </div>
</template>

<style scoped>
.doc-fields { display: flex; flex-direction: column; }

.field-row {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 11px 0 10px;
  border-bottom: 1px solid #eae5de;
}
.field-row:last-child { border-bottom: none; }

.field-label {
  width: 72px;
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.04em;
  color: #9498b5;
  line-height: 1.6;
  padding-top: 6px;
}

.field-textarea {
  flex: 1;
  border: 1px solid #d0ccc6;
  border-radius: 4px;
  background: transparent;
  padding: 8px 10px;
  font: inherit;
  font-size: 14px;
  color: #1a1f3c;
  outline: none;
  resize: vertical;
  min-height: 72px;
  line-height: 1.6;
  transition: border-color 0.15s;
  box-sizing: border-box;
  width: 100%;
}
.field-textarea:focus { border-color: #5e5ce6; }
.field-textarea::placeholder { color: #c4c0ba; font-style: italic; font-size: 12px; line-height: 1.6; }
</style>
