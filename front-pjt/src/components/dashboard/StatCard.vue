<template>
  <div class="stat-card" :class="{ 'highlight': isHighlight }">
    <div class="stat-label">{{ label }}</div>
    <div class="value-container">
      <div class="stat-value" :class="colorType">{{ formatAmount(amount) }}</div>
      <div class="stat-unit">원</div>
    </div>
  </div>
</template>


<script setup>
defineProps({
  label: String,
  amount: { type: Number, required: true },
  colorType: { type: String, default: 'positive' }, // positive, negative, accent
  isHighlight: { type: Boolean, default: false }
})

function formatAmount(value) {
  if (value >= 100000000) {
    const eok = Math.floor(value / 100000000)
    const man = Math.floor((value % 100000000) / 10000)
    return man > 0 ? `${eok}억 ${man.toLocaleString()}만` : `${eok}억`
  }
  return `${(value / 10000).toLocaleString()}만`
}
</script>


<style scoped>
.stat-card {
  background: #ffffff;
  border: 1px solid #e9eaf2;
  border-radius: 12px;
  padding: 20px 24px;
  box-shadow: 0 1px 3px rgba(20, 20, 43, 0.06);
  display: flex;
  flex-direction: column;
}

.stat-card.highlight {
  border-color: #e0e0fd;
  background: linear-gradient(135deg, #fafafe 0%, #f0f0fd 100%);
}

.stat-label {
  font-size: 13px;
  font-weight: 600;
  color: #6b7280;
  letter-spacing: 0.2px;
  margin-bottom: 10px;
}

.value-container {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.stat-value {
  font-size: 26px;
  font-weight: 800;
  line-height: 1.1;
  letter-spacing: -0.3px;
}

.stat-value.positive { color: #1a1a1a; }
.stat-value.negative { color: #e53935; }
.stat-value.accent { color: #5e5ce6; }

.stat-unit {
  font-size: 14px;
  font-weight: 600;
  color: #aaa;
}
</style>