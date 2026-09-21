import { createSecureUuid } from '../utils/secureUuid.js'
import { buildDomainBaseURL } from './index.js'

export const AI_CONTRACT_VERSION = '2.0'

export function createIdempotencyKey(cryptoProvider = globalThis.crypto) {
  return createSecureUuid(cryptoProvider)
}

export async function createPresentationEntryIdempotencyKey(
  { sessionId, stepId, reportVersion },
  cryptoProvider = globalThis.crypto,
) {
  const material = JSON.stringify({ version: 2, sessionId, stepId, reportVersion })
  if (!cryptoProvider?.subtle || typeof TextEncoder === 'undefined') {
    throw new Error('결정적 Presentation 안내 키를 생성할 수 없습니다.')
  }
  const digest = await cryptoProvider.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(material),
  )
  const hex = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
  return `presentation-entry-v2-${hex}`
}

export function deriveV2BaseURL(root) {
  return buildDomainBaseURL('consulting', 'v2', root)
}
