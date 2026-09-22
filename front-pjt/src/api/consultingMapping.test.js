import assert from 'node:assert/strict'
import test from 'node:test'

import { mapMonthlyIncome, wrapCurrentListForm } from './consultingMapping.js'

// 화면·서버·AI 계약(ai-pjt/core/field_registry.py)이 모두 원 단위다.
// 이전에는 String() 으로 감싸 number 분기를 우회해 만 단위로 해석했고,
// 그 짝으로 formChanges.uiValue() 가 monthly_income 을 /10000 해서 내렸다. 둘 다 제거했다.
test('기본정보 소득은 원 단위로 저장한다', () => {
  assert.equal(mapMonthlyIncome(3_900_000), 3_900_000)
  assert.equal(mapMonthlyIncome('3900000'), 3_900_000)
  assert.equal(mapMonthlyIncome('3,900,000'), 3_900_000)
  assert.equal(mapMonthlyIncome('390만'), 3_900_000)
  assert.equal(mapMonthlyIncome(null), null)
  assert.equal(mapMonthlyIncome(''), null)
})

test('반복 폼의 canonical 행은 v2 form_data 객체 안에 둔다', () => {
  const rows = [{ name: '급여 통장', client_item_id: 'asset-client-01' }]

  assert.deepEqual(wrapCurrentListForm(rows), { assets: rows })
})
