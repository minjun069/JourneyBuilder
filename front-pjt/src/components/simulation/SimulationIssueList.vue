<script setup>
// 422 응답의 error.details.issues 를 그대로 보여준다.
// 기준 계산 실패 상태와 변경안 경고가 같은 목록을 써야 하므로 컴포넌트로 뺐다.
import { sectionLabel } from './fieldLabel.js'

defineProps({
  issues: { type: Array, default: () => [] },
})
</script>

<template>
  <ul v-if="issues.length" class="issue-list">
    <li v-for="(issue, index) in issues" :key="`${issue.field}-${issue.code}-${index}`">
      <span v-if="sectionLabel(issue.field)" class="issue-where">{{ sectionLabel(issue.field) }}</span>
      <span class="issue-message">{{ issue.message }}</span>
    </li>
  </ul>
</template>

<style scoped>
.issue-list { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; text-align: left; }
.issue-list li { display: flex; flex-wrap: wrap; gap: 6px; align-items: baseline; font-size: 13px; line-height: 1.5; }
.issue-where { flex: none; border-radius: 6px; padding: 2px 7px; background: rgba(26, 31, 60, 0.07); font-size: 12px; font-weight: 700; }
.issue-message { flex: 1; min-width: 0; }
</style>
