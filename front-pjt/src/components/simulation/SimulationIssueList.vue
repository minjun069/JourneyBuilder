<script setup>
// 422 응답의 error.details.issues 를 그대로 보여준다.
// 기준 계산 실패 상태와 변경안 경고가 같은 목록을 써야 하므로 컴포넌트로 뺐다.
defineProps({
  issues: { type: Array, default: () => [] },
})

const SECTION_LABELS = {
  basic_info: '기본 정보',
  financial_assets: '금융자산',
  pension_assets: '연금',
  goal_periods: '은퇴 후 생활비',
  changes: '변경 조건',
}

// 서버 메시지는 "예상 월 수령액을(를) 입력해 주세요." 처럼 어느 행인지 말하지 않는다.
// field 경로(pension_assets[1].expected_amount)에서 사람이 읽을 위치를 만들어 붙인다.
function sectionLabel(field) {
  const matched = /^([a-z_]+)(?:\[(\d+)\])?/.exec(typeof field === 'string' ? field : '')
  const label = matched ? SECTION_LABELS[matched[1]] : null
  if (!label) return ''
  return matched[2] == null ? label : `${label} ${Number(matched[2]) + 1}번째 항목`
}
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
