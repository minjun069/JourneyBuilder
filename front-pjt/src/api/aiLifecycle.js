import { createSecureUuid } from '../utils/secureUuid.js'
import { buildDomainBaseURL } from './index.js'

export const AI_CONTRACT_VERSION = '2.0'

export function createIdempotencyKey(cryptoProvider = globalThis.crypto) {
  return createSecureUuid(cryptoProvider)
}

export async function createPresentationEntryIdempotencyKey(
  { sessionId, stepId, reportVersion, visitId },
  cryptoProvider = globalThis.crypto,
) {
  if (!visitId) throw new Error('Presentation 안내 방문 식별자가 필요합니다.')
  const material = JSON.stringify({ version: 3, sessionId, stepId, reportVersion, visitId })
  if (!cryptoProvider?.subtle || typeof TextEncoder === 'undefined') {
    throw new Error('결정적 Presentation 안내 키를 생성할 수 없습니다.')
  }
  const digest = await cryptoProvider.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(material),
  )
  const hex = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
  return `presentation-entry-v3-${hex}`
}

export function deriveV2BaseURL(root) {
  return buildDomainBaseURL('consulting', 'v2', root)
}
