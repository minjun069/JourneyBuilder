import axios from 'axios'

export const API_ROOT = (import.meta.env?.VITE_API_BASE_URL || '/api').replace(/\/+$/, '')

export function buildDomainBaseURL(domain, version, root = API_ROOT) {
  const normalizedRoot = String(root || '/api').replace(/\/+$/, '')
  if (!/^[a-z][a-z0-9-]*$/.test(domain)) throw new Error('API domain이 올바르지 않습니다.')
  if (!/^v[1-9][0-9]*$/.test(version)) throw new Error('API version이 올바르지 않습니다.')
  return `${normalizedRoot}/${domain}/${version}`
}

// ── Request: access token 자동 첨부 ──────────────────────────
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

function installAuthInterceptors(api) {
  api.interceptors.request.use(config => {
    const token = localStorage.getItem('access_token')
    if (token) config.headers.Authorization = `Bearer ${token}`
    return config
  })

  api.interceptors.response.use(
    res => res,
    async error => {
      const original = error.config

      if (error.response?.status !== 401 || !original || original._retry) {
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
        const { data } = await axios.post(
          `${buildDomainBaseURL('auth', 'v1')}/token/refresh/`,
          { refresh },
        )
        const newAccess = data.access
        localStorage.setItem('access_token', newAccess)
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
    },
  )
}

export function createDomainApi(domain, version, options = {}) {
  const api = axios.create({
    baseURL: buildDomainBaseURL(domain, version),
    timeout: options.timeout ?? 10000,
    headers: { 'Content-Type': 'application/json' },
  })
  installAuthInterceptors(api)
  return api
}

export const authApi = createDomainApi('auth', 'v1')
export const consultingApi = createDomainApi('consulting', 'v1')
export const reportsApi = createDomainApi('reports', 'v1')
export const simulationsApi = createDomainApi('simulations', 'v1')
export const communityApi = createDomainApi('community', 'v1')
export const adminApi = createDomainApi('admin', 'v1')
