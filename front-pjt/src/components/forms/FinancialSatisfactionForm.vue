<script setup>
import { reactive } from 'vue'

const ITEMS = [
  { key: 'cashflow',       label: '현금흐름 관리' },
  { key: 'riskManagement', label: '위험 관리' },
  { key: 'investment',     label: '투자' },
  { key: 'management',     label: '재무관리' },
  { key: 'inheritance',    label: '상속' },
]

const form = reactive({
  cashflow:       null,
  riskManagement: null,
  investment:     null,
  management:     null,
  inheritance:    null,
})

defineExpose({ form })
</script>

<template>
  <div class="doc-fields">

    <div class="scale-guide">
      <span>1 매우 불만족</span>
      <span>5 매우 만족</span>
    </div>

    <div v-for="item in ITEMS" :key="item.key" class="field-row">
      <span class="field-label">{{ item.label }}</span>
      <div class="scale-group">
        <label
          v-for="n in 5"
          :key="n"
          class="scale-btn"
          :class="{ active: form[item.key] === n }"
          @click="form[item.key] = n"
        >
          <span>{{ n }}</span>
        </label>
      </div>
    </div>

  </div>
</template>

<style scoped>
.doc-fields { display: flex; flex-direction: column; }

.field-row {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 9px 0 7px;
  border-bottom: 1px solid #eae5de;
}
.field-row:last-child { border-bottom: none; }

.field-label {
  width: 80px;
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.04em;
  color: #9498b5;
  line-height: 1.5;
  white-space: pre-line;
}

.scale-group { display: flex; gap: 6px; }
.scale-btn { cursor: pointer; }
.scale-btn span {
  display: inline-flex; align-items: center; justify-content: center;
  width: 36px; height: 36px; border: 1.5px solid #d0ccc6; border-radius: 4px;
  font-size: 14px; font-weight: 700; color: #9498b5; transition: all 0.12s; user-select: none;
}
.scale-btn.active span { border-color: #5e5ce6; background: #5e5ce6; color: #fff; }

.scale-guide {
  display: flex; justify-content: space-between;
  font-size: 10px; color: #b0b4c8; padding-top: 6px;
}
</style>
