<template>
  <div class="card score-card">
    <div class="score-left">
      <div class="score-label">Score</div>
      <div class="score-value">
        <span class="score-number">{{ score }}</span>
        <span class="score-divider">/</span>
        <span class="score-total">100</span>
        <span class="score-grade" :class="gradeClass">{{ grade }}</span>
      </div>
    </div>
    <div class="score-right">
      <div class="ai-label">AI 상담사 Comment</div>
      <p class="ai-comment">{{ aiComment }}</p>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  score: { type: Number, required: true },
  grade: { type: String, required: true },
  aiComment: { type: String, required: true },
})

const gradeClass = computed(() => {
  const g = props.grade
  if (g.startsWith('A')) return 'grade-good'
  if (g.startsWith('B')) return 'grade-fair'
  return 'grade-poor'
})
</script>

<style scoped>
.card {
  background: #ffffff;
  border: 1px solid #e9eaf2;
  border-radius: 12px;
  box-shadow: 0 1px 3px rgba(20, 20, 43, 0.06);
}

.score-card {
  display: flex;
  gap: 32px;
  padding: 24px 28px;
  align-items: flex-start;
}

.score-left {
  flex-shrink: 0;
  min-width: 160px;
}

.score-label {
  font-size: 12px;
  font-weight: 600;
  color: #888;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 10px;
}

.score-value {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.score-number {
  font-size: 52px;
  font-weight: 800;
  color: #1a1a1a;
  line-height: 1;
}

.score-divider {
  font-size: 24px;
  color: #ccc;
  font-weight: 300;
}

.score-total {
  font-size: 20px;
  color: #aaa;
  font-weight: 500;
}

.score-grade {
  margin-left: 8px;
  font-size: 22px;
  font-weight: 700;
  padding: 2px 10px;
  border-radius: 8px;
}

.grade-good {
  color: #2e7d32;
  background-color: #e8f5e9;
}

.grade-fair {
  color: #e65100;
  background-color: #fff3e0;
}

.grade-poor {
  color: #c62828;
  background-color: #ffebee;
}

.score-right {
  flex: 1;
  border-left: 1px solid #f0f0f0;
  padding-left: 28px;
}

.ai-label {
  font-size: 12px;
  font-weight: 700;
  color: #5e5ce6;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 8px;
}

.ai-comment {
  font-size: 14px;
  color: #444;
  line-height: 1.75;
  margin: 0;
}
</style>
