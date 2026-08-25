import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_BASE_URL

const api = axios.create({
  baseURL: BASE_URL,
  timeout: 10000,
  headers: { 'Content-Type': 'application/json' },
})

// ── Request: access token 자동 첨부 ──────────────────────────
api.interceptors.request.use(config => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// ── Response: 401 시 토큰 갱신 후 재시도 ─────────────────────
let isRefreshing = false
let pendingQueue = []   // { resolve, reject }[]

function flushQueue(error, token) {
  pendingQueue.forEach(p => error ? p.reject(error) : p.resolve(token))
  pendingQueue = []
}

async function redirectToLogin() {
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().clearTokens()
  window.location.href = '/login'
}

api.interceptors.response.use(
  res => res,
  async error => {
    const original = error.config

    if (error.response?.status !== 401 || original._retry) {
      return Promise.reject(error)
    }

    const refresh = localStorage.getItem('refresh_token')
    if (!refresh) {
      await redirectToLogin()
      return Promise.reject(error)
    }

    if (isRefreshing) {
      return new Promise((resolve, reject) => {
        pendingQueue.push({ resolve, reject })
      }).then(token => {
        original.headers.Authorization = `Bearer ${token}`
        return api(original)
      })
    }

    original._retry = true
    isRefreshing = true

    try {
      const { data } = await axios.post(`${BASE_URL}/auth/token/refresh/`, { refresh })
      const newAccess = data.access
      localStorage.setItem('access_token', newAccess)
      api.defaults.headers.common.Authorization = `Bearer ${newAccess}`
      original.headers.Authorization = `Bearer ${newAccess}`
      flushQueue(null, newAccess)
      return api(original)
    } catch (err) {
      flushQueue(err, null)
      await redirectToLogin()
      return Promise.reject(err)
    } finally {
      isRefreshing = false
    }
  }
)

export default api
