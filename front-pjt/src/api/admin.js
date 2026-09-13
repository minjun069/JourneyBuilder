// PB 관리자 API (/api/admin/v1/*) — 전부 조회 전용, is_staff 토큰 필요
import { adminApi } from '@/api'

// params: { search, status, need, age_band, net_worth_band, ordering, page, page_size }
export async function getCustomers(params = {}) {
  const { data } = await adminApi.get('/customers/', { params })
  return data // { success, data: rows[], meta: { total, page, page_size, total_pages, generated_at } }
}

export async function getCustomer(userId) {
  const { data } = await adminApi.get(`/customers/${userId}/`)
  return data
}

export async function getSummary() {
  const { data } = await adminApi.get('/analytics/summary/')
  return data
}

export async function getSegments() {
  const { data } = await adminApi.get('/analytics/segments/')
  return data
}
