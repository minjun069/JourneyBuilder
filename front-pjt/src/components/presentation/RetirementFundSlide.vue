<template>
  <div class="slide">
    <div class="card">
      <div class="rows">
        <div class="row"><span class="k">은퇴 연령</span><span class="v">60세</span></div>
        <div class="row"><span class="k">예상 수명</span><span class="v">90세</span></div>
        <div class="row"><span class="k">은퇴 기간</span><span class="v">30년</span></div>
        <div class="row"><span class="k">월 필요 생활비</span><span class="v">250만원</span></div>
        <div class="row"><span class="k">물가상승률 가정</span><span class="v">2.5% / 년</span></div>
      </div>
      <div class="divider"></div>
      <div class="result-row">
        <span class="result-k">필요 총 은퇴자금</span>
        <span class="result-v">5억 2,300만원</span>
      </div>
      <div class="sub-row">
        <span class="sub-k">현재 가치 기준</span>
        <span class="sub-v">4억 800만원</span>
      </div>
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
  box-sizing: border-box;
  overflow: hidden;
}

.card {
  width: 100%;
  background: #fff;
  border: 1px solid #e6e8f0;
  border-radius: 14px;
  padding: 14px 18px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rows { display: flex; flex-direction: column; gap: 4px; }

.row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #4a5070;
  padding: 3px 0;
  border-bottom: 1px solid #f0f0f8;
}

.k { color: #6b7280; }
.v { font-weight: 700; color: #1a1f3c; }

.divider { height: 2px; background: #1a1f3c; }

.result-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.result-k { font-size: 12px; font-weight: 700; color: #4a5070; }
.result-v { font-size: 22px; font-weight: 800; color: #5e5ce6; }

.sub-row {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: #9498b5;
}
</style>
