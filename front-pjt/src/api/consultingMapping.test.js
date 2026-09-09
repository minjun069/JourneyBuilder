import assert from 'node:assert/strict'
import test from 'node:test'

import { mapMonthlyIncome, wrapCurrentListForm } from './consultingMapping.js'

test('기본정보 숫자 draft 소득은 화면의 만원 단위로 저장한다', () => {
  assert.equal(mapMonthlyIncome(300), 3_000_000)
  assert.equal(mapMonthlyIncome(null), null)
})

test('반복 폼의 canonical 행은 v2 form_data 객체 안에 둔다', () => {
  const rows = [{ name: '급여 통장', client_item_id: 'asset-client-01' }]

  assert.deepEqual(wrapCurrentListForm(rows), { assets: rows })
})
