<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route  = useRoute()
const auth   = useAuthStore()

const email    = ref('')
const password = ref('')
const errorMsg = ref('')
const loading  = ref(false)

async function handleLogin() {
  errorMsg.value = ''
  loading.value  = true
  try {
    await auth.login(email.value, password.value)
    const redirect = route.query.redirect || '/'
    router.push(redirect)
  } catch (err) {
    errorMsg.value = err.response?.data?.detail || '이메일 또는 비밀번호를 확인해주세요.'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-card">
      <div class="auth-header">
        <p class="auth-sub">AI 재무 진단 서비스</p>
        <h1 class="auth-title brand">Journey <span>Builder</span></h1>
      </div>

      <form class="auth-form" @submit.prevent="handleLogin">
        <div class="field">
          <label for="email">이메일</label>
          <input
            id="email"
            v-model="email"
            type="email"
            placeholder="example@email.com"
            autocomplete="email"
            required
          />
        </div>

        <div class="field">
          <label for="password">비밀번호</label>
          <input
            id="password"
            v-model="password"
            type="password"
            placeholder="비밀번호를 입력하세요"
            autocomplete="current-password"
            required
          />
        </div>

        <p v-if="errorMsg" class="error-msg">{{ errorMsg }}</p>

        <button type="submit" class="btn-submit" :disabled="loading">
          {{ loading ? '로그인 중...' : '로그인' }}
        </button>
      </form>

      <p class="auth-footer">
        아직 계정이 없으신가요?
        <RouterLink to="/signup">회원가입</RouterLink>
      </p>
    </div>
  </div>
</template>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #f8f7ff 0%, #f0f4ff 100%);
  padding: 24px;
}

.auth-card {
  background: #fff;
  border: 1px solid #ebebf5;
  border-radius: 20px;
  padding: 48px 40px;
  width: 100%;
  max-width: 420px;
  box-shadow: 0 8px 40px rgba(94, 92, 230, 0.1);
}

.auth-header {
  text-align: center;
  margin-bottom: 36px;
}

.brand {
  font-size: 15px;
  font-weight: 700;
  color: #1a1a2e;
  margin-bottom: 20px;
}

.brand span {
  color: #5e5ce6;
}

.auth-title {
  font-size: 26px;
  font-weight: 800;
  color: #1a1a2e;
  margin: 0 0 8px;
}

.auth-sub {
  font-size: 14px;
  color: #888;
  margin: 0;
}

.auth-form {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.field label {
  font-size: 13px;
  font-weight: 600;
  color: #444;
}

.field input {
  padding: 12px 14px;
  border: 1.5px solid #e0dff7;
  border-radius: 10px;
  font-size: 14px;
  color: #1a1a2e;
  outline: none;
  transition: border-color 0.2s;
}

.field input:focus {
  border-color: #5e5ce6;
}

.field input::placeholder {
  color: #bbb;
}

.error-msg {
  font-size: 13px;
  color: #e53e3e;
  margin: 0;
  padding: 10px 12px;
  background: #fff5f5;
  border-radius: 8px;
  border: 1px solid #fed7d7;
}

.btn-submit {
  margin-top: 4px;
  padding: 13px;
  background: #5e5ce6;
  color: #fff;
  border: none;
  border-radius: 10px;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s, transform 0.15s;
}

.btn-submit:hover:not(:disabled) {
  background: #4b49c8;
  transform: translateY(-1px);
}

.btn-submit:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.auth-footer {
  text-align: center;
  margin-top: 24px;
  font-size: 14px;
  color: #777;
}

.auth-footer a {
  color: #5e5ce6;
  font-weight: 600;
  margin-left: 4px;
}

.auth-footer a:hover {
  text-decoration: underline;
}
</style>
