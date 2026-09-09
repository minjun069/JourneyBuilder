import assert from 'node:assert/strict'
import test from 'node:test'

import { mapMonthlyIncome } from './consultingMapping.js'

test('기본정보 숫자 draft 소득은 화면의 만원 단위로 저장한다', () => {
  assert.equal(mapMonthlyIncome(300), 3_000_000)
})
