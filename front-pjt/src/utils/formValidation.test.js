import assert from 'node:assert/strict'
import test from 'node:test'

import { validateConsultationForm } from './formValidation.js'

test('기본 정보의 공란과 연령 순서를 결정론적으로 차단한다', () => {
  const blank = validateConsultationForm('basic-info', {})
  assert.equal(blank.valid, false)
  assert.ok(blank.errors.length >= 5)

  const invalidOrder = validateConsultationForm('basic-info', {
    age: 40, job: '직장인', marital: '미혼', earnings: 0,
    retirementAge: 35, endAge: 90,
  })
  assert.equal(invalidOrder.valid, false)
  assert.match(invalidOrder.errors.join(' '), /은퇴 나이/)
})

test('재무 만족도는 다섯 항목이 모두 있어야 한다', () => {
  assert.equal(validateConsultationForm('financial-satisfaction', {}).valid, false)
  assert.equal(validateConsultationForm('financial-satisfaction', {
    cashflow: 1, riskManagement: 2, investment: 3, management: 4, inheritance: 5,
  }).valid, true)
})

test('빈 반복 폼은 경고 후 명시적인 해당 없음 확인으로 진행할 수 있다', () => {
  for (const step of ['pension-assets', 'real-estate', 'liabilities', 'income', 'expenses']) {
    const warning = validateConsultationForm(step, { assets: [] })
    assert.equal(warning.valid, false, step)
    assert.equal(warning.code, 'empty_form', step)
    assert.equal(warning.canConfirmEmpty, true, step)
    assert.match(warning.errors.join(' '), /해당 없음으로 진행하시겠습니까/)
    assert.equal(validateConsultationForm(step, { assets: [] }, { allowEmpty: true }).valid, true)
    assert.equal(validateConsultationForm(step, { assets: [{ name: '입력 항목' }] }).canConfirmEmpty, false)
  }
  const financialAssets = validateConsultationForm('financial-assets', { assets: [] })
  assert.equal(financialAssets.canConfirmEmpty, true)
  assert.equal(financialAssets.confirmationLabel, '보유 금융자산 없음')
  assert.match(financialAssets.errors.join(' '), /실제로 보유한 금융자산이 없다면/)
})

test('빈 재무목표는 명시적인 없음 확인을 요청하며 필수 설문은 건너뛰지 않는다', () => {
  const form = { before: { periods: [], milestones: [] }, after: { periods: [], milestones: [] } }
  assert.equal(validateConsultationForm('financial-goals', form).canConfirmEmpty, true)
  assert.equal(validateConsultationForm('financial-goals', form, { allowEmpty: true }).valid, true)
  for (const step of ['basic-info', 'financial-mindset', 'financial-satisfaction']) {
    const result = validateConsultationForm(step, {}, { allowEmpty: true })
    assert.equal(result.valid, false)
    assert.equal(result.canConfirmEmpty, false)
  }
})

test('반복 폼의 음수 금액과 비정상 범위를 차단한다', () => {
  const result = validateConsultationForm('pension-assets', {
    assets: [{ name: '연금', value: '-1만', rate: 25, age: 50 }],
  })
  assert.equal(result.valid, false)
  assert.equal(result.errors.length, 3)
})
