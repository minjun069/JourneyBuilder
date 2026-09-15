import { createSecureUuid } from '../utils/secureUuid.js'
import { buildDomainBaseURL } from './index.js'

export const AI_CONTRACT_VERSION = '2.0'

export function createIdempotencyKey(cryptoProvider = globalThis.crypto) {
  return createSecureUuid(cryptoProvider)
}

export function deriveV2BaseURL(root) {
  return buildDomainBaseURL('consulting', 'v2', root)
}
