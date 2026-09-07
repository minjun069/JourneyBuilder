export const AI_CONTRACT_VERSION = '2.0'
export const AI_POLLING_INTERVAL_MS = 3_000
export const AI_PROCESSING_TIMEOUT_MS = 90_000

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'expired'])

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

function requireRequestIdentity(data, expectedRequestId = null) {
  if (typeof data.request_id !== 'string' || !data.request_id) {
    throw invalidContract('AI 응답의 request_id가 올바르지 않습니다.')
  }
  if (expectedRequestId != null && data.request_id !== expectedRequestId) {
    throw invalidContract('AI 응답의 request_id가 원래 요청과 다릅니다.')
  }
}

export function validateAcceptedEnvelope(payload) {
  const data = requireSuccessEnvelope(payload)
  requireRequestIdentity(data)
  if (
    data.status !== 'processing'
    || typeof data.processing_deadline_at !== 'string'
    || !data.processing_deadline_at
  ) {
    throw invalidContract('AI 접수 응답 형식이 올바르지 않습니다.')
  }
  return data
}

export function validatePollingEnvelope(payload, expectedRequestId) {
  const data = requireSuccessEnvelope(payload)
  requireRequestIdentity(data, expectedRequestId)
  if (data.status === 'processing') {
    if (typeof data.processing_deadline_at !== 'string' || !data.processing_deadline_at) {
      throw invalidContract('AI 처리 상태 응답 형식이 올바르지 않습니다.')
    }
    return data
  }
  if (!TERMINAL_STATUSES.has(data.status)) {
    throw invalidContract('알 수 없는 AI 처리 상태입니다.')
  }
  if (data.status === 'completed') {
    const message = data.message
    if (
      message == null
      || typeof message !== 'object'
      || !Number.isInteger(message.id)
      || message.role !== 'assistant'
      || typeof message.content !== 'string'
    ) {
      throw invalidContract('완료된 AI 메시지 형식이 올바르지 않습니다.')
    }
  } else if (
    data.error == null
    || typeof data.error !== 'object'
    || typeof data.error.code !== 'string'
    || typeof data.error.message !== 'string'
    || typeof data.error.retryable !== 'boolean'
  ) {
    throw invalidContract('실패한 AI 상태의 오류 형식이 올바르지 않습니다.')
  }
  return data
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

export function terminalLifecycleError(terminal) {
  return new AILifecycleError(terminal.error.code, terminal.error.message, {
    retryable: terminal.error.retryable,
    data: { request_id: terminal.request_id, status: terminal.status },
  })
}

export function abortableDelay(milliseconds, signal) {
  if (signal?.aborted) {
    const error = new Error('AI 요청이 취소되었습니다.')
    error.name = 'AbortError'
    return Promise.reject(error)
  }
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer)
      const error = new Error('AI 요청이 취소되었습니다.')
      error.name = 'AbortError'
      reject(error)
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, milliseconds)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

export async function pollUntilTerminal({
  requestId,
  poll,
  signal,
  intervalMs = AI_POLLING_INTERVAL_MS,
  timeoutMs = AI_PROCESSING_TIMEOUT_MS,
  delay = abortableDelay,
}) {
  if (!Number.isFinite(intervalMs) || intervalMs <= 0) {
    throw new TypeError('intervalMs는 양수여야 합니다.')
  }
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new TypeError('timeoutMs는 양수여야 합니다.')
  }
  const maxAttempts = Math.ceil(timeoutMs / intervalMs)
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    await delay(intervalMs, signal)
    const state = validatePollingEnvelope(await poll(requestId, { signal }), requestId)
    if (state.status !== 'processing') return state
  }
  throw new AILifecycleError(
    'AI_REQUEST_EXPIRED',
    'AI 작업 시간이 만료되었습니다. 다시 요청해주세요.',
    {
      retryable: true,
      data: { request_id: requestId, status: 'expired' },
    },
  )
}

export function createIdempotencyKey() {
  if (typeof globalThis.crypto?.randomUUID !== 'function') {
    throw new Error('안전한 idempotency key를 생성할 수 없습니다.')
  }
  return globalThis.crypto.randomUUID()
}

export function deriveV2BaseURL(baseURL) {
  const normalized = String(baseURL ?? '').replace(/\/+$/, '')
  if (!normalized) return '/api/v2'
  if (/\/v2$/.test(normalized)) return normalized
  if (/\/v1$/.test(normalized)) return normalized.replace(/\/v1$/, '/v2')
  if (/\/api$/.test(normalized)) return `${normalized}/v2`
  return `${normalized}/api/v2`
}
