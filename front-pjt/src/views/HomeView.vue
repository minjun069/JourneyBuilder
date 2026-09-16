<script setup>
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import mainPageImg from '@/assets/images/main_page.jpg'

// 로그인 상태에서 /signup은 guestOnly 가드에 막혀 클릭이 무시되므로 AI 상담으로 보낸다
const auth = useAuthStore()
const ctaTarget = computed(() => (auth.hasSession ? '/ai' : '/signup'))
</script>

<template>
  <div class="home">

    <!-- Hero Section -->
    <section class="hero">
      <div class="hero-content">
        <div class="hero-text">
          <div class="hero-badge">재무 건강 진단 서비스</div>
          <h1 class="hero-title">
            내 재무 상태,<br />
            <span class="highlight">정확하게</span> 파악하세요
          </h1>
          <p class="hero-desc">
            복잡한 자산·부채·현금흐름을 한눈에 파악하고<br />
            AI가 전문적으로 진단해 드립니다.
          </p>
          <div class="hero-actions">
            <RouterLink to="/ai" class="btn-primary">무료로 시작하기</RouterLink>
          </div>
          <ul class="hero-checklist">
            <li>JB은행 연계 서비스</li>
            <li>100% 무료 재무진단</li>
            <li>전문가 자료 기반 AI 전문성 보장</li>
            <li>개인정보 보호 및 규제사항 준수</li>
          </ul>
        </div>

        <div class="hero-image-wrap">
          <div class="hero-image-bg" />
          <img :src="mainPageImg" alt="재무 분석 일러스트" class="hero-image" />
        </div>
      </div>
    </section>

    <!-- Features Section -->
    <section class="features">
      <div class="features-inner">
        <div class="section-header">
          <h2 class="section-title"><span class="brand-journey">Journey</span> <span class="brand-builder">Builder</span>가 제공하는 것</h2>
          <p class="section-desc">재무 관리의 모든 것을 한 곳에서</p>
        </div>

        <div class="feature-grid">
          <div class="feature-card">
            <div class="feature-icon" style="background: #eef2ff;">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
                <rect x="3" y="3" width="7" height="7" rx="1.5" fill="#5e5ce6"/>
                <rect x="14" y="3" width="7" height="7" rx="1.5" fill="#5e5ce6" opacity=".5"/>
                <rect x="3" y="14" width="7" height="7" rx="1.5" fill="#5e5ce6" opacity=".5"/>
                <rect x="14" y="14" width="7" height="7" rx="1.5" fill="#5e5ce6"/>
              </svg>
            </div>
            <h3 class="feature-title">재무 대시보드</h3>
            <p class="feature-desc">자산·부채·현금흐름·재무 목표를 시각화된 차트로 한눈에 파악하세요.</p>
            <RouterLink to="/dashboard" class="feature-link">
              대시보드 보기
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </RouterLink>
          </div>

          <div class="feature-card feature-card--accent">
            <div class="feature-icon" style="background: #fff7ed;">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="9" stroke="#f97316" stroke-width="2"/>
                <path d="M12 8v4l3 3" stroke="#f97316" stroke-width="2" stroke-linecap="round"/>
              </svg>
            </div>
            <h3 class="feature-title">AI 재무 상담</h3>
            <p class="feature-desc">입력한 재무 데이터를 바탕으로 AI가 맞춤 전략과 개선 방향을 제시합니다.</p>
            <RouterLink to="/ai" class="feature-link feature-link--orange">
              AI 상담 시작
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </RouterLink>
          </div>

          <div class="feature-card">
            <div class="feature-icon" style="background: #f0fdf4;">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" stroke="#22c55e" stroke-width="2" stroke-linecap="round"/>
                <circle cx="9" cy="7" r="4" stroke="#22c55e" stroke-width="2"/>
                <path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" stroke="#22c55e" stroke-width="2" stroke-linecap="round"/>
              </svg>
            </div>
            <h3 class="feature-title">커뮤니티</h3>
            <p class="feature-desc">같은 고민을 가진 사람들과 재무 경험과 노하우를 나누고 함께 성장하세요.</p>
            <RouterLink to="/community" class="feature-link feature-link--green">
              커뮤니티 가기
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </RouterLink>
          </div>
        </div>
      </div>
    </section>

    <!-- How It Works Section -->
    <section class="steps">
      <div class="steps-inner">
        <div class="section-header">
          <h2 class="section-title">시작은 단 3단계</h2>
          <p class="section-desc">복잡한 설정 없이 바로 재무 진단을 받으세요</p>
        </div>

        <div class="step-list">
          <div class="step-item">
            <div class="step-number">01</div>
            <div class="step-body">
              <h4 class="step-title">재무 정보 입력</h4>
              <p class="step-desc">자산, 부채, 수입, 지출 등 기본 재무 정보를 입력합니다.</p>
            </div>
          </div>
          <div class="step-arrow">→</div>
          <div class="step-item">
            <div class="step-number">02</div>
            <div class="step-body">
              <h4 class="step-title">AI 재무 분석</h4>
              <p class="step-desc">입력된 데이터를 기반으로 AI가 재무 상태를 분석합니다.</p>
            </div>
          </div>
          <div class="step-arrow">→</div>
          <div class="step-item">
            <div class="step-number">03</div>
            <div class="step-body">
              <h4 class="step-title">맞춤 전략 확인</h4>
              <p class="step-desc">개인화된 재무 개선 전략과 목표 달성 플랜을 확인합니다.</p>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- CTA Section -->
    <section class="cta">
      <div class="cta-inner">
        <h2 class="cta-title">지금 바로 재무 건강을 점검해 보세요</h2>
        <p class="cta-desc">5분이면 나의 재무 점수와 개선 방향을 알 수 있습니다</p>
        <RouterLink :to="ctaTarget" class="cta-btn">무료로 시작하기</RouterLink>
      </div>
    </section>

  </div>
</template>

<style scoped>
.home {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  color: #1a1a2e;
}

/* ── Hero ── */
.hero {
  min-height: calc(100vh - 80px);
  display: flex;
  align-items: center;
  background: linear-gradient(135deg, #f8f7ff 0%, #f0f4ff 100%);
  padding: 60px 24px;
}

.hero-content {
  max-width: 1100px;
  margin: 0 auto;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 60px;
  align-items: center;
  width: 100%;
}

.hero-badge {
  display: inline-block;
  background: #eef2ff;
  color: #5e5ce6;
  font-size: 13px;
  font-weight: 600;
  padding: 6px 14px;
  border-radius: 20px;
  margin-bottom: 20px;
  letter-spacing: 0.3px;
}

.hero-title {
  font-size: 48px;
  font-weight: 800;
  line-height: 1.25;
  color: #1a1a2e;
  margin: 0 0 20px;
}

.highlight {
  color: #5e5ce6;
  position: relative;
}

.hero-desc {
  font-size: 17px;
  color: #555;
  line-height: 1.75;
  margin: 0 0 36px;
}

.hero-actions {
  display: flex;
  gap: 14px;
  margin-bottom: 48px;
}

.btn-primary {
  background: #5e5ce6;
  color: #fff;
  padding: 14px 32px;
  border-radius: 10px;
  text-decoration: none;
  font-size: 15px;
  font-weight: 600;
  transition: background 0.2s, transform 0.15s;
}
.btn-primary:hover {
  background: #4b49c8;
  transform: translateY(-1px);
}

.btn-secondary {
  background: #fff;
  color: #5e5ce6;
  padding: 14px 32px;
  border-radius: 10px;
  text-decoration: none;
  font-size: 15px;
  font-weight: 600;
  border: 1.5px solid #d4d2f7;
  transition: border-color 0.2s, transform 0.15s;
}
.btn-secondary:hover {
  border-color: #5e5ce6;
  transform: translateY(-1px);
}

.hero-checklist {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.hero-checklist li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: #555;
}

.hero-checklist li::before {
  content: '✓';
  color: #5e5ce6;
  font-weight: 700;
  font-size: 15px;
  flex-shrink: 0;
}

.hero-image-wrap {
  position: relative;
  display: flex;
  justify-content: center;
  align-items: center;
  perspective: 1200px;
}

.hero-image-bg {
  position: absolute;
  width: 90%;
  height: 90%;
  background: radial-gradient(ellipse at center, #d4d2f7 0%, transparent 70%);
  border-radius: 50%;
  filter: blur(40px);
  opacity: 0.6;
}

.hero-image {
  position: relative;
  width: 100%;
  max-width: 520px;
  border-radius: 20px;
  transform: perspective(1200px) rotateY(-4deg) rotateX(1deg);
  box-shadow:
    0 4px 12px rgba(0, 0, 0, 0.07),
    0 20px 48px rgba(94, 92, 230, 0.16),
    12px 28px 56px rgba(94, 92, 230, 0.08);
}

/* ── Features ── */
.features {
  padding: 100px 24px;
  background: #fff;
}

.features-inner {
  max-width: 1100px;
  margin: 0 auto;
}

.section-header {
  text-align: center;
  margin-bottom: 56px;
}

.section-title {
  font-size: 34px;
  font-weight: 800;
  color: #1a1a2e;
  margin: 0 0 12px;
}

.brand-journey {
  color: #5e5ce6;
}

.brand-builder {
  color: #1a1a2e;
}

.section-desc {
  font-size: 16px;
  color: #666;
  margin: 0;
}

.feature-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 24px;
}

.feature-card {
  background: #f9f9fc;
  border: 1px solid #ebebf5;
  border-radius: 16px;
  padding: 32px 28px;
  transition: box-shadow 0.2s, transform 0.2s;
}

.feature-card:hover {
  box-shadow: 0 12px 36px rgba(94, 92, 230, 0.1);
  transform: translateY(-4px);
}

.feature-card--accent {
  background: linear-gradient(135deg, #fff8f3, #fff3eb);
  border-color: #fed7aa;
}

.feature-icon {
  width: 56px;
  height: 56px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 20px;
}

.feature-title {
  font-size: 19px;
  font-weight: 700;
  color: #1a1a2e;
  margin: 0 0 10px;
}

.feature-desc {
  font-size: 14px;
  color: #666;
  line-height: 1.7;
  margin: 0 0 24px;
}

.feature-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #5e5ce6;
  font-size: 14px;
  font-weight: 600;
  text-decoration: none;
  transition: gap 0.2s;
}
.feature-link:hover { gap: 10px; }

.feature-link--orange { color: #f97316; }
.feature-link--green  { color: #22c55e; }

/* ── Steps ── */
.steps {
  padding: 100px 24px;
  background: #f8f7ff;
}

.steps-inner {
  max-width: 1100px;
  margin: 0 auto;
}

.step-list {
  display: flex;
  align-items: center;
  gap: 16px;
}

.step-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  background: #fff;
  border-radius: 16px;
  padding: 36px 24px;
  box-shadow: 0 4px 20px rgba(94, 92, 230, 0.07);
}

.step-number {
  font-size: 40px;
  font-weight: 900;
  color: #e8e7f9;
  line-height: 1;
  margin-bottom: 16px;
}

.step-title {
  font-size: 17px;
  font-weight: 700;
  color: #1a1a2e;
  margin: 0 0 8px;
}

.step-desc {
  font-size: 13px;
  color: #777;
  line-height: 1.65;
  margin: 0;
}

.step-arrow {
  font-size: 28px;
  color: #c5c3f0;
  flex-shrink: 0;
}

/* ── CTA ── */
.cta {
  padding: 100px 24px;
  background: linear-gradient(135deg, #5e5ce6, #7b79f0);
  text-align: center;
}

.cta-inner {
  max-width: 640px;
  margin: 0 auto;
}

.cta-title {
  font-size: 32px;
  font-weight: 800;
  color: #fff;
  margin: 0 0 16px;
}

.cta-desc {
  font-size: 16px;
  color: rgba(255,255,255,0.82);
  margin: 0 0 36px;
  line-height: 1.6;
}

.cta-btn {
  display: inline-block;
  background: #fff;
  color: #5e5ce6;
  padding: 16px 40px;
  border-radius: 12px;
  font-size: 16px;
  font-weight: 700;
  text-decoration: none;
  transition: transform 0.15s, box-shadow 0.2s;
}
.cta-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(0,0,0,0.15);
}

/* ── Responsive ── */
@media (max-width: 900px) {
  .hero-content {
    grid-template-columns: 1fr;
    gap: 40px;
  }
  .hero-image-wrap { order: -1; }
  .hero-title { font-size: 36px; }
  .feature-grid { grid-template-columns: 1fr; }
  .step-list { flex-direction: column; }
  .step-arrow { transform: rotate(90deg); }
}

@media (max-width: 600px) {
  .hero { padding: 48px 20px; }
  .hero-title { font-size: 28px; }
  .hero-actions { flex-direction: column; }
  .section-title { font-size: 26px; }
  .cta-title { font-size: 24px; }
}
</style>
