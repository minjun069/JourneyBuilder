<template>
  <div class="slide">
    <div class="gauge-area">
      <svg viewBox="0 0 200 110" class="gauge">
        <path d="M20 100 A80 80 0 0 1 180 100" stroke="#e5e7eb" stroke-width="18" fill="none" stroke-linecap="round"/>
        <path d="M20 100 A80 80 0 0 1 180 100" stroke="#5e5ce6" stroke-width="18" fill="none"
          stroke-linecap="round"
          stroke-dasharray="251.2"
          stroke-dashoffset="181"
        />
        <text x="100" y="88" text-anchor="middle" font-size="22" font-weight="800" fill="#1a1f3c">28%</text>
        <text x="100" y="104" text-anchor="middle" font-size="9" fill="#6b7280">현재 준비율</text>
      </svg>
    </div>
    <div class="details">
      <div class="rd-row"><span class="rd-k">현재 준비자금</span><span class="rd-v">1억 5,000만원</span></div>
      <div class="rd-row"><span class="rd-k">목표 은퇴자금</span><span class="rd-v">5억 2,300만원</span></div>
      <div class="rd-row deficit"><span class="rd-k">부족액</span><span class="rd-v red">3억 7,300만원</span></div>
      <div class="rd-row"><span class="rd-k">은퇴 시 예상 준비율</span><span class="rd-v">67%</span></div>
    </div>
  </div>
</template>

<style scoped>
.slide {
  width: 100%;
  height: 100%;
  padding: 14px 20px;
  display: flex;
  align-items: center;
  gap: 20px;
  box-sizing: border-box;
  overflow: hidden;
}

.gauge-area { flex: 0 0 180px; }
.gauge { width: 180px; height: 100px; display: block; }

.details {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rd-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  padding: 5px 0;
  border-bottom: 1px solid #f0f0f8;
}

.rd-k { color: #6b7280; }
.rd-v { font-weight: 700; color: #1a1f3c; }
.rd-v.red { color: #b83232; }
.rd-row.deficit .rd-k { font-weight: 700; }
</style>
