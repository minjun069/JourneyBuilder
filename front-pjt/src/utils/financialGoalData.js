// ── 재무목표 리포트 본문 → 슬라이드가 쓰는 '구간 배열' ──────────────
// FinancialGoalSlide 는 구간(phase) 배열을 그린다. 구간 하나는
// { start_age, end_age, phase_name, desired_expenses[], expected_expenses[], achieved_funds[] } 다.
//
// 그런데 본문이 두 가지 모양으로 들어온다.
//  · post_retirement_phases: 서버도 목업도 처음부터 구간 배열이다.
//  · pre_retirement: 서버(backend-pjt/reports/calculators.py calculate_financial_goal_result)는
//    구간으로 쪼개지 않고 { desired_expenses, expected_expenses, achieved_funds } 객체 하나로 내려준다.
//    반면 목업(src/mock/aiData.js)은 구간 배열이다.
// 객체는 length 가 없어 그대로 두면 '은퇴 전'이 통째로 조용히 빠진다.
// 그리기 직전에 두 모양을 구간 배열로 맞춘다 — 이미 저장된 글도 다시 만들지 않고 그대로 그려진다.

function toArray(value) {
  return Array.isArray(value) ? value : []
}

function toAge(value) {
  const age = Number(value)
  return Number.isFinite(age) ? age : null
}

// 구간 배열은 그대로 쓰되, 세 배열은 빠져 있어도 배열로 채운다
// (makeTimeline·phaseItems 가 옵셔널 체이닝 없이 바로 펼친다)
function withPhaseArrays(phase) {
  return {
    ...phase,
    desired_expenses: toArray(phase?.desired_expenses),
    expected_expenses: toArray(phase?.expected_expenses),
    achieved_funds: toArray(phase?.achieved_funds),
  }
}

// 나이는 데이터에서만 뽑는다 — 기간은 from_age/to_age, 마일스톤은 age
function collectAges(periods, milestones) {
  return [
    ...periods.flatMap(period => [toAge(period?.from_age), toAge(period?.to_age)]),
    ...milestones.map(milestone => toAge(milestone?.age)),
  ].filter(age => age !== null)
}

// 마일스톤은 자기 나이가 속한 구간에 붙인다 — 시작 나이가 자기 이하인 마지막 구간
function phaseIndexForAge(phases, age) {
  const target = toAge(age)
  if (target === null) return 0

  let index = 0
  for (let i = 0; i < phases.length; i += 1) {
    if (phases[i].start_age <= target) index = i
  }
  return index
}

export function toGoalPhases(source) {
  // 이미 구간 배열인 본문(목업·대시보드·AI 경로·은퇴 후)은 모양을 바꾸지 않는다
  if (Array.isArray(source)) return source.map(withPhaseArrays)
  if (!source || typeof source !== 'object') return []

  const periods = toArray(source.desired_expenses)
  const expectedExpenses = toArray(source.expected_expenses)
  const achievedFunds = toArray(source.achieved_funds)
  const milestones = [...expectedExpenses, ...achievedFunds]

  if (!periods.length && !milestones.length) return []

  const ages = collectAges(periods, milestones)
  const minAge = ages.length ? Math.min(...ages) : 0
  const maxAge = ages.length ? Math.max(...ages) : 0

  // 기간이 있으면 기간마다 구간을 만들고, 기간 없이 마일스톤만 있으면 전체를 한 구간으로 둔다
  // (from_age/to_age 가 뒤집혀 들어온 기간도 있어 작은 쪽을 시작으로 둔다)
  const phases = periods.length
    ? [...periods]
      .map(period => {
        const from = toAge(period?.from_age) ?? minAge
        const to = toAge(period?.to_age) ?? maxAge
        return {
          start_age: Math.min(from, to),
          end_age: Math.max(from, to),
          desired_expenses: [period],
          expected_expenses: [],
          achieved_funds: [],
        }
      })
      .sort((a, b) => a.start_age - b.start_age)
    : [{
      start_age: minAge,
      end_age: maxAge,
      desired_expenses: [],
      expected_expenses: [],
      achieved_funds: [],
    }]

  // 구간 밖에 놓인 마일스톤이 타임라인에서 잘려 나가지 않게 양끝을 넓힌다
  const first = phases[0]
  const last = phases[phases.length - 1]
  first.start_age = Math.min(first.start_age, minAge)
  last.end_age = Math.max(last.end_age, maxAge)

  // 구간 이름은 서버가 주지 않는다 — 넓히고 난 최종 나이 범위로 붙인다
  for (const phase of phases) {
    phase.phase_name = `${phase.start_age}~${phase.end_age}세`
  }

  for (const milestone of expectedExpenses) {
    phases[phaseIndexForAge(phases, milestone?.age)].expected_expenses.push(milestone)
  }
  for (const milestone of achievedFunds) {
    phases[phaseIndexForAge(phases, milestone?.age)].achieved_funds.push(milestone)
  }

  return phases
}
