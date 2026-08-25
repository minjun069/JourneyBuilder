import api from '@/api'

export const getReports = (sessionId)             => api.get(`/consultations/${sessionId}/reports/`)
export const getReport  = (sessionId, reportType) => api.get(`/consultations/${sessionId}/reports/${reportType}/`)
