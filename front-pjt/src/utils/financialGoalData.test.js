import assert from 'node:assert/strict'
import test from 'node:test'

import { toGoalPhases } from './financialGoalData.js'

// 백엔드 calculate_financial_goal_result 가 실제로 내려주는 pre_retirement 모양(구간 없는 객체)
// — 커뮤니티 글 53번에 첨부된 스냅샷과 같은 구조다
function livePreRetirement(overrides = {}) {
  return {
    achieved_funds: [],
    desired_expenses: [
      {
        from_age: 38,
        to_age: 60,
        items: [
          { name: '생활비', amount: 4_500_000, is_fixed: true },
          { name: '자녀 교육비', amount: 900_000, is_fixed: false },
        ],
        current_total: 5_400_000,
        future_total: 5_400_000,
      },
    ],
    expected_expenses: [
      { age: 48, content: '첫째 대학 등록금', note: '4년 합산 기준', current_amount: 80_000_000, future_amount: 97_519_553 },
      { age: 51, content: '둘째 대학 등록금', note: '4년 합산 기준', current_amount: 80_000_000, future_amount: 103_488_530 },
      { age: 55, content: '주택담보대출 조기상환 목표', note: '잔액 일부 조기상환', current_amount: 100_000_000, future_amount: 140_024_141 },
    ],
    ...overrides,
  }
}

// 목업(src/mock/aiData.js)·은퇴 후 본문이 쓰는 구간 배열 모양
function phaseArray() {
  return [
    {
      phase_name: '활동기',
      start_age: 60,
      end_age: 70,
      desired_expenses: [{ from_age: 60, to_age: 70, items: [{ name: '생활비', amount: 3_200_000 }] }],
      expected_expenses: [],
      achieved_funds: [{ age: 65, content: '국민연금 수령 시작', current_amount: 1_300_000 }],
    },
    {
      phase_name: '회상기',
      start_age: 71,
      end_age: 80,
      desired_expenses: [{ from_age: 71, to_age: 80, items: [{ name: '생활비', amount: 2_800_000 }] }],
      expected_expenses: [],
      achieved_funds: [],
    },
  ]
}

test('객체 형태의 은퇴 전 본문을 구간 배열로 옮긴다', () => {
  const phases = toGoalPhases(livePreRetirement())

  assert.equal(phases.length, 1)
  assert.equal(phases[0].start_age, 38)
  assert.equal(phases[0].end_age, 60)
  assert.equal(phases[0].phase_name, '38~60세')
  assert.deepEqual(
    phases[0].desired_expenses.flatMap(d => d.items).map(i => i.name),
    ['생활비', '자녀 교육비'],
  )
  assert.deepEqual(
    phases[0].expected_expenses.map(e => [e.age, e.content, e.current_amount]),
    [
      [48, '첫째 대학 등록금', 80_000_000],
      [51, '둘째 대학 등록금', 80_000_000],
      [55, '주택담보대출 조기상환 목표', 100_000_000],
    ],
  )
  assert.deepEqual(phases[0].achieved_funds, [])
})

test('구간 배열은 모양을 바꾸지 않고 그대로 돌려준다', () => {
  const source = phaseArray()

  assert.deepEqual(toGoalPhases(source), source)
})

test('기간이 여러 개면 기간마다 구간을 만들고 마일스톤을 나이에 맞춰 나눈다', () => {
  const phases = toGoalPhases({
    desired_expenses: [
      { from_age: 50, to_age: 60, items: [{ name: '생활비', amount: 3_000_000 }] },
      { from_age: 38, to_age: 49, items: [{ name: '생활비', amount: 4_500_000 }] },
    ],
    expected_expenses: [{ age: 55, content: '조기상환', current_amount: 100_000_000 }],
    achieved_funds: [{ age: 45, content: '성과급', current_amount: 10_000_000 }],
  })

  assert.deepEqual(phases.map(p => [p.start_age, p.end_age]), [[38, 49], [50, 60]])
  assert.deepEqual(phases[0].achieved_funds.map(e => e.age), [45])
  assert.deepEqual(phases[0].expected_expenses, [])
  assert.deepEqual(phases[1].expected_expenses.map(e => e.age), [55])
})

test('기간 밖의 마일스톤도 타임라인 양끝을 넓혀 담는다', () => {
  const phases = toGoalPhases({
    desired_expenses: [{ from_age: 40, to_age: 50, items: [] }],
    expected_expenses: [{ age: 58, content: '조기상환', current_amount: 1 }],
    achieved_funds: [{ age: 35, content: '성과급', current_amount: 1 }],
  })

  assert.equal(phases[0].start_age, 35)
  assert.equal(phases[0].end_age, 58)
  assert.deepEqual(phases[0].expected_expenses.map(e => e.age), [58])
  assert.deepEqual(phases[0].achieved_funds.map(e => e.age), [35])
})

test('시작·끝 나이가 뒤집힌 기간도 작은 쪽을 시작으로 세워 그린다', () => {
  // 상담 세션에 from_age 73 / to_age 61 로 저장된 기간이 실제로 있다(대시보드 /dashboard/goals)
  const [phase] = toGoalPhases({
    desired_expenses: [{ from_age: 73, to_age: 61, items: [{ name: '생활비', amount: 3_670_000 }] }],
    expected_expenses: [{ age: 61, content: '주택 구입', current_amount: 101_860_000 }],
    achieved_funds: [],
  })

  assert.deepEqual([phase.start_age, phase.end_age], [61, 73])
  assert.equal(phase.phase_name, '61~73세')
  assert.deepEqual(phase.expected_expenses.map(e => e.age), [61])
})

test('기간 없이 마일스톤만 있어도 구간 하나로 그린다', () => {
  const phases = toGoalPhases({
    desired_expenses: [],
    expected_expenses: [{ age: 48, content: '등록금', current_amount: 1 }],
    achieved_funds: [{ age: 60, content: '퇴직금', current_amount: 1 }],
  })

  assert.equal(phases.length, 1)
  assert.deepEqual([phases[0].start_age, phases[0].end_age], [48, 60])
  assert.deepEqual(phases[0].desired_expenses, [])
})

test('빈 본문·없는 본문은 빈 배열이라 은퇴 전 구획이 그려지지 않는다', () => {
  const empty = { achieved_funds: [], desired_expenses: [], expected_expenses: [] }

  assert.deepEqual(toGoalPhases(empty), [])
  assert.deepEqual(toGoalPhases(undefined), [])
  assert.deepEqual(toGoalPhases(null), [])
  assert.deepEqual(toGoalPhases([]), [])
})

test('세 배열 중 빠진 것이 있어도 배열로 채워 돌려준다', () => {
  const [phase] = toGoalPhases([{ phase_name: '활동기', start_age: 60, end_age: 70 }])

  assert.deepEqual(phase.desired_expenses, [])
  assert.deepEqual(phase.expected_expenses, [])
  assert.deepEqual(phase.achieved_funds, [])
})
