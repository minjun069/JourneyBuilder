<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth   = useAuthStore()

const form = ref({
  email:      '',
  password:   '',
  name:       '',
  phone:      '',
  birth_year: '',
})
const errorMsg  = ref('')
const loading   = ref(false)

async function handleRegister() {
  errorMsg.value = ''
  loading.value  = true
  try {
    const payload = {
      email:    form.value.email,
      password: form.value.password,
      name:     form.value.name,
    }
    if (form.value.phone)      payload.phone      = form.value.phone
    if (form.value.birth_year) payload.birth_year = Number(form.value.birth_year)

    await auth.register(payload)
  } catch (err) {
    const data = err.response?.data
    if (data && typeof data === 'object') {
      const firstKey = Object.keys(data)[0]
      const msg = data[firstKey]
      errorMsg.value = Array.isArray(msg) ? msg[0] : msg
    } else {
      errorMsg.value = '회원가입 중 오류가 발생했습니다.'
    }
    loading.value = false
    return
  }

  try {
    await auth.login(form.value.email, form.value.password)
    router.push('/')
  } catch {
    // 가입은 됐지만 자동 로그인 실패 → 로그인 페이지로
    router.push('/login')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-card">
      <div class="auth-header">
        <h1 class="auth-title">회원가입</h1>
      </div>

      <form class="auth-form" @submit.prevent="handleRegister">
        <div class="field">
          <label for="name">이름 <span class="required">*</span></label>
          <input
            id="name"
            v-model="form.name"
            type="text"
            placeholder="홍길동"
            autocomplete="name"
            required
          />
        </div>

        <div class="field">
          <label for="email">이메일 <span class="required">*</span></label>
          <input
            id="email"
            v-model="form.email"
            type="email"
            placeholder="example@email.com"
            autocomplete="email"
            required
          />
        </div>

        <div class="field">
          <label for="password">비밀번호 <span class="required">*</span></label>
          <input
            id="password"
            v-model="form.password"
            type="password"
            placeholder="8자 이상 입력하세요"
            autocomplete="new-password"
            minlength="8"
            required
          />
        </div>

        <div class="field-row">
          <div class="field">
            <label for="phone">전화번호 <span class="optional">(선택)</span></label>
            <input
              id="phone"
              v-model="form.phone"
              type="tel"
              placeholder="010-0000-0000"
              autocomplete="tel"
            />
          </div>

          <div class="field">
            <label for="birth_year">출생연도 <span class="optional">(선택)</span></label>
            <input
              id="birth_year"
              v-model="form.birth_year"
              type="number"
              placeholder="1990"
              min="1900"
              :max="new Date().getFullYear()"
            />
          </div>
        </div>

        <p v-if="errorMsg" class="error-msg">{{ errorMsg }}</p>

        <button type="submit" class="btn-submit" :disabled="loading">
          {{ loading ? '처리 중...' : '가입하기' }}
        </button>
      </form>

      <p class="auth-footer">
        이미 계정이 있으신가요?
        <RouterLink to="/login">로그인</RouterLink>
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
  max-width: 460px;
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
  flex: 1;
}

.field-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

.field label {
  font-size: 13px;
  font-weight: 600;
  color: #444;
}

.required {
  color: #e53e3e;
}

.optional {
  font-weight: 400;
  color: #aaa;
}

.field input {
  padding: 12px 14px;
  border: 1.5px solid #e0dff7;
  border-radius: 10px;
  font-size: 14px;
  color: #1a1a2e;
  outline: none;
  transition: border-color 0.2s;
  width: 100%;
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
