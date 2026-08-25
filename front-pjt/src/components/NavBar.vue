<template>
    <nav class="navbar">
        <div class="navbar-container">

        <div class="logo-area">
            <RouterLink to="/" class="logo-link">
                <img src="@/assets/images/jb_logo.svg" class="logo-img" alt="Journey Builder" />
                <div class="logo-text">
                    <span class="logo-journey">Journey</span>
                    <span class="logo-builder">Builder</span>
                </div>
            </RouterLink>
        </div>

        <div class="menu-area">
            <RouterLink
            v-for="item in menuItems"
            :key="item.path"
            :to="item.path"
            class="nav-item"
            active-class="active"
            >
            {{ item.name }}
            </RouterLink>
        </div>

        <div v-if="auth.hasSession" class="auth-area">
            <button type="button" class="auth-link auth-button" @click="handleLogout">
                로그아웃
            </button>
        </div>
        <div v-else class="auth-area">
            <RouterLink to="/login" class="auth-link">로그인</RouterLink>
            <span class="divider">|</span>
            <RouterLink to="/signup" class="auth-link">회원가입</RouterLink>
        </div>

        </div>
    </nav>
</template>


<script setup>
import { ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

const menuItems = ref([
  { name: 'AI 상담', path: '/ai' },
  { name: '대시보드', path: '/dashboard' },
  { name: '커뮤니티', path: '/community' },
])

async function handleLogout() {
  await auth.logout()
  router.push('/login')
}

// if (import.meta.env.DEV) {
//   menuItems.value.push({ name: '(임시) Form Dev', path: '/dev/forms' })
//   menuItems.value.push({ name: '(임시) Presentation Dev', path: '/dev/presentation' })
// }
</script>


<style scoped>

/* 폰트 및 전체 정렬 세팅 */
.navbar {
  width: 100%;
  height: 80px; /* 상단바 높이 */
  background-color: #ffffff;
  border-bottom: 1px solid #f0f0f0; /* 하단 얇은 회색 선 */

  display: flex;
  align-items: center;
  justify-content: center;

  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}

.navbar-container {
  width: 100%;
  max-width: 800px; /* 모니터가 커도 일정 크기 이상 벌어지지 않게 가둠 */
  padding: 0 20px;

  height: 100%;
  
  display: flex;
  align-items: center;
}

/* 1. 로고 스타일 */
.logo-area {
  margin-right: 40px;

  .logo-link {
    display: flex;
    align-items: center;
    text-decoration: none;
    gap: 10px;
  }

  .logo-img {
    height: 36px;
    width: auto;
    flex-shrink: 0;
  }

  .logo-text {
    display: flex;
    flex-direction: column;
    line-height: 1.15;
  }

  .logo-journey {
    font-size: 15px;
    font-weight: 800;
    letter-spacing: -0.3px;
    color: #5e5ce6;
  }

  .logo-builder {
    font-size: 15px;
    font-weight: 800;
    letter-spacing: -0.3px;
    color: #2c2c2c;
  }
}

/* 2. 중앙 메뉴 스타일 */
.menu-area {
  display: flex;
  gap: 4px;
  align-items: center;
  flex-grow: 1;

  .nav-item {
    position: relative;
    z-index: 0;
    display: flex;
    align-items: center;
    color: #4a4a4a;
    text-decoration: none;
    font-size: 15px;
    font-weight: 500;
    padding: 7px 18px;
    border-radius: 8px;
    transition: color 0.18s ease;

    &::before {
      content: '';
      position: absolute;
      inset: 0;
      border-radius: 8px;
      background: transparent;
      transition: background 0.18s ease;
      z-index: -1;
    }

    &:hover {
      color: #5e5ce6;
      &::before { background: #f0effd; }
    }

    &.active {
      color: #fff;
      font-weight: 600;
      &::before { background: #5e5ce6; }
    }
  }
}

/* 4. 회원 기능 스타일 */
.auth-area {
  display: flex;
  align-items: center;
  gap: 12px;

  .auth-link {
    border: 0;
    background: transparent;
    color: #666666;
    text-decoration: none;
    font-size: 14px;
    font-weight: 400;
    font-family: inherit;
    cursor: pointer;
    padding: 0;

    &:hover {
      color: #333;
    }
  }

  .auth-button {
    line-height: 1;
  }

  .divider {
    color: #e0e0e0;
    font-size: 12px;
  }
}
</style>
