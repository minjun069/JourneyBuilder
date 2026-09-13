import { createSecureUuid } from '../utils/secureUuid.js'

export const AI_CONTRACT_VERSION = '2.0'

export function createIdempotencyKey(cryptoProvider = globalThis.crypto) {
  return createSecureUuid(cryptoProvider)
}

export function deriveV2BaseURL(baseURL) {
  const normalized = String(baseURL ?? '').replace(/\/+$/, '')
  if (!normalized) return '/api/v2'
  if (normalized.endsWith('/v2')) return normalized
  if (normalized.endsWith('/v1')) return `${normalized.slice(0, -3)}/v2`
  if (normalized.endsWith('/api')) return `${normalized}/v2`
  return `${normalized}/api/v2`
}
