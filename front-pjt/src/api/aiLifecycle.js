import { createSecureUuid } from '../utils/secureUuid.js'

export const AI_CONTRACT_VERSION = '2.0'

export class AILifecycleError extends Error {
  constructor(code, message, { retryable = false, status = null, data = null } = {}) {
    super(message)
    this.name = 'AILifecycleError'
    this.code = code
    this.retryable = retryable
    this.status = status
    this.data = data
  }
}

function invalidContract(message = 'AI 응답 형식이 올바르지 않습니다.') {
  return new AILifecycleError('RESPONSE_SCHEMA_INVALID', message)
}

function requireSuccessEnvelope(payload) {
  if (
    payload == null
    || typeof payload !== 'object'
    || payload.contract_version !== AI_CONTRACT_VERSION
    || payload.success !== true
    || payload.data == null
    || typeof payload.data !== 'object'
    || Array.isArray(payload.data)
  ) {
    throw invalidContract()
  }
  return payload.data
}

export function validateDecisionEnvelope(payload, {
  actionId,
  changeId,
  decision,
}) {
  const data = requireSuccessEnvelope(payload)
  const expectedDecision = decision === 'approve' ? 'approved' : 'rejected'
  if (
    data.action_id !== actionId
    || data.change_id !== changeId
    || data.decision_status !== expectedDecision
    || !['pending', 'partially_resolved', 'resolved'].includes(data.action_status)
  ) {
    throw invalidContract('AI 변경 결정 응답 형식이 올바르지 않습니다.')
  }
  if (
    expectedDecision === 'approved'
    && (data.change == null || typeof data.change !== 'object' || Array.isArray(data.change))
  ) {
    throw invalidContract('승인된 AI 변경 내용이 누락되었습니다.')
  }
  return data
}

export function lifecycleErrorFromResponse(error, fallbackMessage = 'AI 요청에 실패했습니다.') {
  if (error instanceof AILifecycleError || error?.name === 'AbortError') return error
  const response = error?.response
  const payload = response?.data
  const publicError = payload?.success === false ? payload.error : null
  if (
    publicError
    && typeof publicError.code === 'string'
    && typeof publicError.message === 'string'
    && typeof publicError.retryable === 'boolean'
  ) {
    return new AILifecycleError(publicError.code, publicError.message, {
      retryable: publicError.retryable,
      status: response.status ?? null,
      data: payload.data ?? null,
    })
  }
  return new AILifecycleError('AI_REQUEST_FAILED', fallbackMessage, {
    retryable: true,
    status: response?.status ?? null,
  })
}

export function createIdempotencyKey(cryptoProvider = globalThis.crypto) {
  return createSecureUuid(cryptoProvider)
}

export function deriveV2BaseURL(baseURL) {
  const normalized = String(baseURL ?? '').replace(/\/+$/, '')
  if (!normalized) return '/api/v2'
  if (/\/v2$/.test(normalized)) return normalized
  if (/\/v1$/.test(normalized)) return normalized.replace(/\/v1$/, '/v2')
  if (/\/api$/.test(normalized)) return `${normalized}/v2`
  return `${normalized}/api/v2`
}
