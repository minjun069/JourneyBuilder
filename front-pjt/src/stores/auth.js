import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import api from '@/api'

const ACCESS_KEY  = 'access_token'
const REFRESH_KEY = 'refresh_token'

export const useAuthStore = defineStore('auth', () => {
  const accessToken  = ref(localStorage.getItem(ACCESS_KEY)  || null)
  const refreshToken = ref(localStorage.getItem(REFRESH_KEY) || null)
  const user         = ref(null) // /auth/me/ 응답 {id, email, name, is_staff}

  const isAuthenticated = computed(() =>
    import.meta.env.DEV || !!accessToken.value
  )
  const hasSession = computed(() => !!accessToken.value)
  const isStaff = computed(() => !!user.value?.is_staff)

  // FR-01: 로그인 직후·앱 부팅 시 프로필을 조회해 PB 여부를 판단한다
  async function fetchMe() {
    const { data } = await api.get('/auth/me/')
    user.value = data
    return data
  }

  async function ensureUser() {
    if (user.value) return user.value
    if (!accessToken.value) return null
    try {
      return await fetchMe()
    } catch {
      return null
    }
  }

  function setTokens(access, refresh) {
    accessToken.value  = access
    refreshToken.value = refresh
    localStorage.setItem(ACCESS_KEY,  access)
    localStorage.setItem(REFRESH_KEY, refresh)
  }

  function clearTokens() {
    accessToken.value  = null
    refreshToken.value = null
    user.value         = null
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
  }

  async function login(email, password) {
    const { data } = await api.post('/auth/login/', { email, password })
    setTokens(data.access, data.refresh)
    try {
      await fetchMe()
    } catch {
      // 프로필 조회 실패는 로그인 자체를 막지 않는다 (가드에서 재시도)
    }
  }

  async function register(payload) {
    await api.post('/auth/register/', payload)
  }

  async function logout() {
    try {
      if (refreshToken.value) {
        await api.post('/auth/logout/', { refresh: refreshToken.value })
      }
    } finally {
      clearTokens()
      const [{ useConsultationStore }, { useReportStore }] = await Promise.all([
        import('@/stores/consultation'),
        import('@/stores/report'),
      ])
      useConsultationStore().clearConsultation()
      useReportStore().clearReports()
    }
  }

  return {
    accessToken, refreshToken, user,
    isAuthenticated, hasSession, isStaff,
    fetchMe, ensureUser, login, register, logout, clearTokens,
  }
})
