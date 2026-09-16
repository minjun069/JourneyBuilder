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
let accessRefreshPromise = null

async function redirectToLogin() {
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().clearTokens()
  window.location.href = '/login'
}

export async function refreshAccessToken() {
  if (accessRefreshPromise) return accessRefreshPromise
  const refresh = localStorage.getItem('refresh_token')
  if (!refresh) {
    await redirectToLogin()
    throw new Error('로그인이 만료되었습니다.')
  }

  accessRefreshPromise = (async () => {
    try {
      const { data } = await axios.post(
        `${buildDomainBaseURL('auth', 'v1')}/token/refresh/`,
        { refresh },
      )
      localStorage.setItem('access_token', data.access)
      return data.access
    } catch (error) {
      await redirectToLogin()
      throw error
    } finally {
      accessRefreshPromise = null
    }
  })()
  return accessRefreshPromise
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

      original._retry = true
      try {
        const newAccess = await refreshAccessToken()
        original.headers.Authorization = `Bearer ${newAccess}`
        return api(original)
      } catch (err) {
        return Promise.reject(err)
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
