import assert from 'node:assert/strict'
import test from 'node:test'

import { toCanonicalMoney } from './canonicalMoney.js'

test('numeric canonical KRW is preserved without screen-unit inference', () => {
  assert.equal(toCanonicalMoney(0), 0)
  assert.equal(toCanonicalMoney(5000), 5000)
  assert.equal(toCanonicalMoney(5000.6), 5001)
  assert.equal(toCanonicalMoney(9999, 'eok'), 9999)
  assert.equal(toCanonicalMoney(10000), 10000)
})

test('raw screen strings use explicit or screen-default units', () => {
  assert.equal(toCanonicalMoney('5000원'), 5000)
  assert.equal(toCanonicalMoney('5000.6원'), 5001)
  assert.equal(toCanonicalMoney('300만'), 3000000)
  assert.equal(toCanonicalMoney('3억5000만'), 350000000)
  assert.equal(toCanonicalMoney('500'), 5000000)
  assert.equal(toCanonicalMoney('3', 'eok'), 300000000)
})
