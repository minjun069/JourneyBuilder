<template>
  <div class="report-view">

    <!-- 헤더 -->
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">정기 리포트</h1>
        <select class="date-select" v-model="selectedDate">
          <option v-for="d in availableDates" :key="d.value" :value="d.value">{{ d.label }}</option>
        </select>
      </div>
    </div>

    <div class="section-rule"></div>

    <!-- 핵심 요약 -->
    <div class="section">
      <div class="section-header">
        <span class="section-label">이번주 요약</span>
      </div>
      <div class="summary-card">
        <div class="summary-icon">📝</div>
        <p class="summary-text">{{ report.summary }}</p>
      </div>
    </div>

    <div class="section-rule"></div>

    <!-- 뉴스 기반 섹션 카드 -->
    <div class="section">
      <div class="section-header">
        <span class="section-label">뉴스 분석</span>
      </div>
      <div class="news-grid">
        <div
          v-for="(sec, idx) in report.sections"
          :key="idx"
          class="news-card"
          :class="getCategoryClass(sec.category)"
        >
          <div class="news-card-header">
            <span class="category-icon">{{ getCategoryIcon(sec.category) }}</span>
            <span class="category-tag">{{ sec.category }}</span>
          </div>
          <h3 class="news-headline">{{ sec.headline }}</h3>
          <p class="news-detail">{{ sec.detail }}</p>
          <div class="news-footer">
            <span
              v-for="id in relatedNewsIds(idx)"
              :key="id"
              class="news-tag"
            >#{{ id }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="section-rule"></div>

    <!-- 이번 주 미션 -->
    <div class="section">
      <div class="section-header">
        <span class="section-label">제안 내용</span>
      </div>
      <div class="action-card">
        <ul class="action-list">
          <li
            v-for="(item, idx) in report.action_items"
            :key="idx"
            class="action-item"
            :class="{ done: checkedItems[idx] }"
            @click="toggleItem(idx)"
          >
            <span class="action-check">{{ checkedItems[idx] ? '☑' : '☐' }}</span>
            <span class="action-text">{{ item }}</span>
          </li>
        </ul>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const report = {
  summary: '이번 주에는 금융시장이 높은 변동성을 보이며, 특히 증시의 큰 하락이 주목받고 있습니다.\n이러한 환경 속에서 포트폴리오 관리를 위한 연금 및 투자 전략 재검토가 필요할 것입니다.',
  news_ids: [1, 2, 3, 4, 5, 6 ,7 ,8],
  sections: [
    {
      detail: '김성주 국민연금 이사장의 발언은 퇴직연금 자산운용의 중요성을 강조합니다. 은퇴 준비를 위해 올바른 연금 상품에 참여하고 관리하는 것이 필요합니다.',
      category: '연금·은퇴',
      headline: '기금형 퇴직연금 운용 참여 필요성 증가',
    },
    {
      detail: '최근 코스피가 급락하면서 개인 투자자들의 매매 패턴도 변화하고 있습니다. 투자 전략을 재조정하여 고위험 자산은 줄이고 안정적인 투자처를 찾는 것이 중요합니다.',
      category: '시장동향',
      headline: '증시 폭락, 개인 투자자에게 큰 충격',
    },
  ],
  action_items: [
    '국민연금 및 기금형 퇴직연금 옵션을 검토하고 필요시 전문가와 상담하기',
    '변동성이 큰 시장에서의 투자 위험을 줄이기 위해 포트폴리오 재구성하기',
  ],
  generated_by: 'data-pjt/Report',
}

function getWeeklyDates(count) {
  const dates = []
  const base = new Date()
  base.setHours(0, 0, 0, 0)
  const day = base.getDay()
  base.setDate(base.getDate() - ((day + 6) % 7))  // 이번 주 월요일
  for (let i = 0; i < count; i++) {
    const d = new Date(base)
    d.setDate(base.getDate() - i * 7)
    dates.push({
      value: d.toISOString().slice(0, 10),
      label: d.toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' }) + ' 주간 리포트',
    })
  }
  return dates
}

const availableDates = getWeeklyDates(8)
const selectedDate = ref(availableDates[0].value)

const checkedItems = ref(report.action_items.map(() => false))

function toggleItem(idx) {
  checkedItems.value[idx] = !checkedItems.value[idx]
}

const doneCount = computed(() => checkedItems.value.filter(Boolean).length)
const progressWidth = computed(() => `${(doneCount.value / report.action_items.length) * 100}%`)

const categoryMeta = {
  '연금·은퇴': { icon: '🛡️', cls: 'card-pension' },
  '시장동향':  { icon: '📉', cls: 'card-market' },
}

function getCategoryIcon(cat) {
  return categoryMeta[cat]?.icon ?? '📌'
}

function getCategoryClass(cat) {
  return categoryMeta[cat]?.cls ?? ''
}

// 섹션별로 news_ids를 균등 분배해서 태그로 노출
function relatedNewsIds(sectionIdx) {
  const perSection = Math.ceil(report.news_ids.length / report.sections.length)
  return report.news_ids.slice(sectionIdx * perSection, sectionIdx * perSection + perSection)
}
</script>

<style scoped>
.report-view {
  display: flex;
  flex-direction: column;
  gap: 20px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

/* 헤더 */
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 14px;
}

.page-title {
  font-size: 35px;
  font-weight: 800;
  color: #1a1a1a;
  margin: 0;
}

.date-select {
  font-size: 13px;
  color: #555;
  background: #f5f5f5;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  padding: 5px 10px;
  cursor: pointer;
  outline: none;
  font-family: inherit;
}

.date-select:hover {
  border-color: #5e5ce6;
}

.date-select:focus {
  border-color: #5e5ce6;
  box-shadow: 0 0 0 2px rgba(94, 92, 230, 0.15);
}

.weather-badge {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #fff8e1;
  border: 1px solid #ffe082;
  border-radius: 12px;
  padding: 10px 16px;
}

.weather-icon {
  font-size: 24px;
}

.weather-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.weather-label {
  font-size: 11px;
  font-weight: 700;
  color: #f59e0b;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.weather-status {
  font-size: 13px;
  font-weight: 600;
  color: #92400e;
}

/* 섹션 공통 */
.section {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 12px;
}

.section-label {
  font-size: 30px;
  font-weight: 700;
  color: #5e5ce6;
  white-space: nowrap;
  letter-spacing: 0.2px;
}

.section-rule {
  height: 1px;
  background-color: #e4e4f0;
}

/* 요약 카드 */
.summary-card {
  display: flex;
  align-items: flex-start;
  gap: 20px;
  background: linear-gradient(135deg, #f0f0fd 0%, #e8e8ff 100%);
  border: 1px solid #d4d4f8;
  border-radius: 14px;
  padding: 32px;
}

.summary-icon {
  font-size: 36px;
  flex-shrink: 0;
  margin-top: 2px;
}

.summary-text {
  font-size: 18px;
  line-height: 1.8;
  color: #3a3a6e;
  font-weight: 500;
  margin: 0;
  white-space: pre-line;
}

/* 뉴스 섹션 카드 그리드 */
.news-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
}

.news-card {
  background: #ffffff;
  border: 1px solid #f0f0f0;
  border-radius: 14px;
  padding: 28px 32px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
  display: flex;
  flex-direction: column;
  gap: 14px;
  border-top: 4px solid #e0e0e0;
  transition: box-shadow 0.2s ease;
}

.news-card:hover {
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.1);
}

.news-card.card-pension {
  border-top-color: #5e5ce6;
}

.news-card.card-market {
  border-top-color: #ef4444;
}

.news-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.category-icon {
  font-size: 26px;
}

.category-tag {
  font-size: 13px;
  font-weight: 700;
  color: #888;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.news-headline {
  font-size: 20px;
  font-weight: 700;
  color: #1a1a1a;
  line-height: 1.4;
  margin: 0;
}

.news-detail {
  font-size: 16px;
  color: #555;
  line-height: 1.75;
  margin: 0;
  flex: 1;
}

.news-footer {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
}

.news-tag {
  font-size: 13px;
  color: #7c7c7c;
  background: #f5f5f5;
  border-radius: 6px;
  padding: 4px 10px;
}

/* 액션 아이템 카드 */
.action-card {
  background: #ffffff;
  border: 1px solid #f0f0f0;
  border-radius: 14px;
  padding: 28px 32px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.action-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.action-item {
  display: flex;
  align-items: center;
  gap: 14px;
  cursor: pointer;
  padding: 16px 20px;
  border-radius: 10px;
  border: 1px solid #ebebeb;
  transition: background-color 0.15s ease, border-color 0.15s ease;
  user-select: none;
}

.action-item:hover {
  background-color: #f5f5ff;
  border-color: #c4c4f8;
}

.action-item.done {
  background-color: #f0fdf4;
  border-color: #bbf7d0;
}

.action-item.done .action-text {
  text-decoration: line-through;
  color: #aaa;
}

.action-check {
  font-size: 22px;
  flex-shrink: 0;
  line-height: 1.4;
  color: #5e5ce6;
}

.action-item.done .action-check {
  color: #22c55e;
}

.action-text {
  font-size: 17px;
  color: #333;
  line-height: 1.65;
}

/* 진행률 바 */
.action-progress {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.progress-label {
  font-size: 12px;
  color: #888;
  font-weight: 600;
}

.progress-bar {
  height: 6px;
  background: #ebebeb;
  border-radius: 99px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #5e5ce6, #8b8bff);
  border-radius: 99px;
  transition: width 0.3s ease;
}
</style>
