import { reportsApi } from '@/api'

export const getReports = (sessionId) => reportsApi.get(`/consultations/${sessionId}/reports/`)
export const createReports = (sessionId) => reportsApi.post(`/consultations/${sessionId}/reports/`)
export const getReport = (sessionId, reportType) => (
  reportsApi.get(`/consultations/${sessionId}/reports/${reportType}/`)
)
export const createReport = (sessionId, reportType) => (
  reportsApi.post(`/consultations/${sessionId}/reports/${reportType}/`)
)
