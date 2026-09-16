import assert from 'node:assert/strict'
import test from 'node:test'

import {
  completedStepsForSession,
  mapStoredForm,
  replaceFormState,
  resolveResumeStep,
} from './consultationState.js'

test('저장된 기본 정보와 목록을 화면 폼 형태로 복원한다', () => {
  assert.deepEqual(mapStoredForm('basic-info', {
    age: 35, job: '직장인', marital_status: '미혼', monthly_income: 3_000_000,
    retirement_age: 60, end_age: 90,
  }), {
    age: 35, job: '직장인', marital: '미혼',
    dependents: { children: [], parents: [], others: [] }, tendency: [],
    interest: '', need: '', earnings: 300, retirementAge: 60, endAge: 90,
  })
  assert.deepEqual(mapStoredForm('income', [
    { id: 3, type: '근로소득', name: '급여', amount: 4_000_000, is_fixed: true },
  ]), {
    assets: [{ id: 3, type: '근로소득', name: '급여', amount: 4_000_000, fixed: '고정' }],
  })
})

test('section completion 기준으로 실제 재개 단계와 완료 상태를 계산한다', () => {
  const session = {
    status: 'in_progress',
    section_completion: {
      basic_info: true,
      financial_mindset: true,
      financial_satisfaction: false,
      financial_assets: false,
    },
  }
  assert.equal(resolveResumeStep(session), 'financial-satisfaction')
  assert.deepEqual(
    [...completedStepsForSession(session)],
    ['intro', 'basic-info', 'financial-mindset', 'collection'],
  )
  assert.equal(resolveResumeStep({ status: 'completed' }), 'balance-sheet')
})

test('반응형 폼 객체의 오래된 키를 제거한 뒤 복원한다', () => {
  const target = { stale: true, assets: [] }
  replaceFormState(target, { assets: [{ id: 1 }] })
  assert.deepEqual(target, { assets: [{ id: 1 }] })
})
