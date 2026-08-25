<template>
  <div class="slide">
    <div class="timeline">
      <div class="tl-row">
        <div class="tl-age">55세~</div>
        <div class="tl-bar-wrap"><div class="tl-bar personal" style="width:80%"></div></div>
        <div class="tl-info"><span class="tl-name">개인연금</span><span class="tl-amount">45만원/월</span></div>
      </div>
      <div class="tl-row">
        <div class="tl-age">62세~</div>
        <div class="tl-bar-wrap"><div class="tl-bar retirement" style="width:70%"></div></div>
        <div class="tl-info"><span class="tl-name">퇴직연금</span><span class="tl-amount">60만원/월</span></div>
      </div>
      <div class="tl-row">
        <div class="tl-age">65세~</div>
        <div class="tl-bar-wrap"><div class="tl-bar national" style="width:100%"></div></div>
        <div class="tl-info"><span class="tl-name">국민연금</span><span class="tl-amount">95만원/월</span></div>
      </div>
    </div>
    <div class="summary">
      65세 이후 연금 합계 <strong>200만원/월</strong>
      — 필요 생활비 250만원의 <strong>80%</strong>
    </div>
  </div>
</template>

<style scoped>
.slide {
  width: 100%;
  height: 100%;
  padding: 14px 20px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 14px;
  box-sizing: border-box;
  overflow: hidden;
}

.timeline { display: flex; flex-direction: column; gap: 12px; }

.tl-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.tl-age { width: 36px; font-size: 11px; font-weight: 700; color: #6b7280; flex-shrink: 0; }

.tl-bar-wrap {
  flex: 1;
  height: 18px;
  background: #f0f0f8;
  border-radius: 4px;
  overflow: hidden;
}

.tl-bar { height: 100%; border-radius: 4px; transition: width 0.5s; }
.tl-bar.personal   { background: #a78bfa; }
.tl-bar.retirement { background: #60a5fa; }
.tl-bar.national   { background: #5e5ce6; }

.tl-info { width: 120px; display: flex; justify-content: space-between; flex-shrink: 0; }
.tl-name   { font-size: 11px; color: #4a5070; }
.tl-amount { font-size: 11px; font-weight: 700; color: #1a1f3c; }

.summary {
  font-size: 12px;
  color: #6b7280;
  background: #f4f5ff;
  border-radius: 8px;
  padding: 8px 12px;
}
</style>
