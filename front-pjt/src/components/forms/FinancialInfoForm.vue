<template>
  <div class="overview">
    <p class="overview-desc">아래 세부 항목별로 재무 정보를 입력해주세요.<br/>좌측 프로세스 목록에서 각 항목을 선택할 수 있습니다.</p>
    <div class="items">
      <div class="item"><span class="item-num">01</span><span>금융자산 — 예금, 주식, 펀드 등</span></div>
      <div class="item"><span class="item-num">02</span><span>연금자산 — 국민연금, 퇴직연금, 개인연금</span></div>
      <div class="item"><span class="item-num">03</span><span>부동산자산 — 자가, 전세, 임대 자산</span></div>
      <div class="item"><span class="item-num">04</span><span>부채 — 대출, 할부, 기타 채무</span></div>
      <div class="item"><span class="item-num">05</span><span>소득 — 근로, 사업, 기타 소득</span></div>
      <div class="item"><span class="item-num">06</span><span>지출 — 고정 및 변동 지출 내역</span></div>
    </div>
  </div>
</template>

<style scoped>
.overview {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.overview-desc {
  font-size: 13px;
  color: #6b7280;
  line-height: 1.7;
  border-left: 3px solid #5e5ce6;
  padding-left: 12px;
}

.items {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 9px 0;
  border-bottom: 1px solid #eae5de;
  font-size: 13px;
  color: #31374a;
}

.item:last-child { border-bottom: none; }

.item-num {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.1em;
  color: #5e5ce6;
  flex-shrink: 0;
  width: 24px;
}
</style>
