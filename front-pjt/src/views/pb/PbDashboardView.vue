<template>
  <div class="pb-layout">
    <aside class="sidebar">
      <div class="sidebar-header">
        <span class="sidebar-eyebrow">RetireWise</span>
        <span class="sidebar-title">PB 대시보드</span>
        <span class="sidebar-desc">고객 재무 통합 관리</span>
      </div>

      <nav class="sidebar-nav">
        <RouterLink to="/pb/overview" class="nav-item" active-class="active">
          <svg class="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6">
            <rect x="3" y="3" width="6" height="6" rx="1.5" />
            <rect x="11" y="3" width="6" height="6" rx="1.5" />
            <rect x="3" y="11" width="6" height="6" rx="1.5" />
            <rect x="11" y="11" width="6" height="6" rx="1.5" />
          </svg>
          개요
        </RouterLink>
        <RouterLink to="/pb/customers" class="nav-item" active-class="active">
          <svg class="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6">
            <circle cx="7.5" cy="6.5" r="3" />
            <path d="M2.5 16.5c0-2.8 2.2-5 5-5s5 2.2 5 5" />
            <path d="M13.5 4.2a3 3 0 0 1 0 4.6M15.2 11.9c1.4.8 2.3 2.3 2.3 4.1" />
          </svg>
          고객 리스트
        </RouterLink>
        <RouterLink to="/pb/segments" class="nav-item" active-class="active">
          <svg class="nav-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6">
            <path d="M3.5 16.5v-6M8.5 16.5v-10M13.5 16.5v-4M17 16.5H3" stroke-linecap="round" />
          </svg>
          세그먼트 분석
        </RouterLink>
      </nav>

      <div class="sidebar-foot" v-if="auth.user">
        <span class="foot-avatar" :style="avatarStyle(auth.user.name)">{{ initialOf(auth.user.name) }}</span>
        <span class="foot-info">
          <span class="foot-name">{{ auth.user.name }} <em class="foot-role">PB</em></span>
          <span class="foot-email">{{ auth.user.email }}</span>
        </span>
      </div>
    </aside>

    <main class="pb-content">
      <RouterView />
    </main>
  </div>
</template>

<script setup>
import { useAuthStore } from '@/stores/auth'
import { avatarStyle, initialOf } from './pbTheme'

const auth = useAuthStore()
</script>

<style scoped>
.pb-layout {
  display: flex;
  min-height: calc(100vh - 80px);
  max-width: 1440px;
  margin: 0 auto;
  background-color: #f7f8fc;
}

.pb-content {
  flex: 1;
  overflow-y: auto;
  padding: 28px 32px 48px;
  min-width: 0;
}

.sidebar {
  width: 250px;
  flex-shrink: 0;
  background-color: #ffffff;
  border-right: 1px solid #ebebf2;
  padding: 36px 0 20px;
  display: flex;
  flex-direction: column;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

.sidebar-header {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 0 24px 22px;
  border-bottom: 1px solid #f3f3f8;
  margin-bottom: 14px;
}

.sidebar-eyebrow {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #5e5ce6;
}

.sidebar-title {
  font-size: 21px;
  font-weight: 800;
  letter-spacing: -0.3px;
  color: #191a2b;
}

.sidebar-desc {
  font-size: 12px;
  color: #9a9cb0;
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 12px;
  flex: 1;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 10px 13px;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 600;
  color: #565a6e;
  text-decoration: none;
  transition: background-color 0.15s ease, color 0.15s ease;
}

.nav-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  opacity: 0.75;
}

.nav-item:hover {
  background-color: #f5f5fd;
  color: #5e5ce6;
}

.nav-item.active {
  background-color: #efeffd;
  color: #4b49c8;
}

.nav-item.active .nav-icon { opacity: 1; }

.sidebar-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 14px 12px 0;
  padding: 12px 13px;
  border-top: 1px solid #f3f3f8;
}

.foot-avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 700;
  flex-shrink: 0;
}

.foot-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.foot-name {
  font-size: 13px;
  font-weight: 700;
  color: #191a2b;
}

.foot-role {
  font-style: normal;
  font-size: 10px;
  font-weight: 700;
  color: #5e5ce6;
  background: #eeeefc;
  border-radius: 4px;
  padding: 1px 5px;
  margin-left: 4px;
  vertical-align: 1px;
}

.foot-email {
  font-size: 11px;
  color: #9a9cb0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
