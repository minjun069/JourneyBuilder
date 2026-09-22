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

test('bare screen strings are KRW — the same unit the server and AI use', () => {
  assert.equal(toCanonicalMoney('500'), 500)
  assert.equal(toCanonicalMoney('5000000'), 5_000_000)
  assert.equal(toCanonicalMoney('5,000,000'), 5_000_000)
  assert.equal(toCanonicalMoney('480000000'), 480_000_000)
})

test('explicit 만/억/원 suffixes still parse', () => {
  assert.equal(toCanonicalMoney('5000원'), 5000)
  assert.equal(toCanonicalMoney('5000.6원'), 5001)
  assert.equal(toCanonicalMoney('300만'), 3_000_000)
  assert.equal(toCanonicalMoney('3억5000만'), 350_000_000)
  assert.equal(toCanonicalMoney('4억8000만'), 480_000_000)
  assert.equal(toCanonicalMoney('8800만'), 88_000_000)
})

test('the three ways a user may type the same amount agree', () => {
  const expected = 5_000_000
  for (const input of ['5000000', '5,000,000', '500만']) {
    assert.equal(toCanonicalMoney(input), expected, `${input} 가 ${expected} 로 읽혀야 한다`)
  }
})

test('empty and unparseable values stay null', () => {
  assert.equal(toCanonicalMoney(''), null)
  assert.equal(toCanonicalMoney(null), null)
  assert.equal(toCanonicalMoney(undefined), null)
  assert.equal(toCanonicalMoney(Number.NaN), null)
})

// 서버 → 폼 하이드레이션(consultationState)은 원 단위 값을 변환 없이 넣는다.
// 사용자가 그 칸을 편집하면 v-model 이 문자열로 바꾸는데, 그때도 크기가 유지돼야 한다.
// 기본 단위가 만이던 시절에는 이 지점에서 10,000배가 됐다.
test('server value survives a hydrate → edit → save round trip', () => {
  const stored = 15_000_000
  assert.equal(toCanonicalMoney(stored), stored)

  const editedByUser = String(stored)
  assert.equal(toCanonicalMoney(editedByUser), stored)

  const retyped = '15,000,000'
  assert.equal(toCanonicalMoney(retyped), stored)
})
