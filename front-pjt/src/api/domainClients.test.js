import assert from 'node:assert/strict'
import test from 'node:test'

import { buildDomainBaseURL } from './index.js'

test('모든 client base URL은 domain 다음에 version을 둔다', () => {
  assert.equal(buildDomainBaseURL('auth', 'v1', '/api'), '/api/auth/v1')
  assert.equal(buildDomainBaseURL('consulting', 'v2', '/api'), '/api/consulting/v2')
  assert.equal(
    buildDomainBaseURL('simulations', 'v1', 'https://example.test/api/'),
    'https://example.test/api/simulations/v1',
  )
})

test('도메인명과 버전을 검증해 잘못된 경계 조합을 거절한다', () => {
  assert.throws(() => buildDomainBaseURL('../auth', 'v1'), /domain/)
  assert.throws(() => buildDomainBaseURL('auth', '1'), /version/)
})
