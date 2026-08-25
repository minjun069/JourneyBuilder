import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import api from '@/api'

const ACCESS_KEY  = 'access_token'
const REFRESH_KEY = 'refresh_token'

export const useAuthStore = defineStore('auth', () => {
  const accessToken  = ref(localStorage.getItem(ACCESS_KEY)  || null)
  const refreshToken = ref(localStorage.getItem(REFRESH_KEY) || null)

  const isAuthenticated = computed(() =>
    import.meta.env.DEV || !!accessToken.value
  )
  const hasSession = computed(() => !!accessToken.value)

  function setTokens(access, refresh) {
    accessToken.value  = access
    refreshToken.value = refresh
    localStorage.setItem(ACCESS_KEY,  access)
    localStorage.setItem(REFRESH_KEY, refresh)
  }

  function clearTokens() {
    accessToken.value  = null
    refreshToken.value = null
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
  }

  async function login(email, password) {
    const { data } = await api.post('/auth/login/', { email, password })
    setTokens(data.access, data.refresh)
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
      const { useAiStore } = await import('@/stores/ai')
      useAiStore().clearSession()
    }
  }

  return { accessToken, refreshToken, isAuthenticated, hasSession, login, register, logout, clearTokens }
})
