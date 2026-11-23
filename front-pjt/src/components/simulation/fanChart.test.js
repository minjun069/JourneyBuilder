import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildFanChart,
  describeFanChart,
  findDepletion,
  formatAxisAmount,
  sanitizeRows,
} from './fanChart.js'

const EOK = 100_000_000

function rows(values, startAge = 40) {
  return values.map((p50, index) => ({
    age: startAge + index,
    balance: { p10: p50 - EOK, p50, p90: p50 + EOK },
  }))
}

// 좌표에 NaN 이 섞이면 SVG 가 통째로 사라진다. 모든 경계 조건에서 이것부터 본다.
function assertNoNaN(model) {
  const numbers = []
  const walk = (value) => {
    if (typeof value === 'number') numbers.push(value)
    else if (Array.isArray(value)) value.forEach(walk)
    else if (value && typeof value === 'object') Object.values(value).forEach(walk)
  }
  walk(model)
  for (const number of numbers) assert.ok(Number.isFinite(number), `유한하지 않은 좌표: ${number}`)
  for (const path of [model.bandPath, model.medianPath, model.comparisonPath]) {
    assert.ok(!String(path).includes('NaN'), `경로에 NaN: ${path}`)
    assert.ok(!String(path).includes('Infinity'), `경로에 Infinity: ${path}`)
  }
}

test('sanitizeRows 는 형식이 깨진 행을 버리고 나이순으로 정렬한다', () => {
  const cleaned = sanitizeRows([
    { age: 50, balance: { p10: 1, p50: 2, p90: 3 } },
    { age: 40, balance: { p10: 1, p50: 2, p90: 3 } },
    { age: 45, balance: { p10: 1, p50: null, p90: 3 } },
    { age: Number.NaN, balance: { p10: 1, p50: 2, p90: 3 } },
    null,
  ])
  assert.deepEqual(cleaned.map((row) => row.age), [40, 50])
})

test('sanitizeRows 는 두 번 걸러도 같은 결과를 낸다', () => {
  const once = sanitizeRows(rows([1, 2, 3].map((n) => n * EOK)))
  assert.deepEqual(sanitizeRows(once), once)
})

test('빈 asset_percentiles 는 빈 모델을 돌려주고 축이 무너지지 않는다', () => {
  const model = buildFanChart({ baselineRows: [] })
  assert.equal(model.empty, true)
  assert.equal(model.medianPath, '')
  assertNoNaN(model)
  assert.equal(describeFanChart(model), '표시할 자산 궤적 데이터가 없습니다.')
})

test('행이 하나뿐이어도 좌표를 만들고 범위를 폭 있는 도형으로 그린다', () => {
  const model = buildFanChart({ baselineRows: rows([3 * EOK], 65), retirementAge: 65 })
  assert.equal(model.empty, false)
  assertNoNaN(model)
  assert.ok(model.bandPath.includes('Z'))
  assert.ok(model.medianPath.includes('L'))
  assert.equal(model.xTicks.length, 1)
  assert.ok(model.yTicks.length >= 2)
})

test('전부 양수면 고갈 표시가 없다', () => {
  const model = buildFanChart({ baselineRows: rows([1, 2, 3, 4].map((n) => n * EOK)) })
  assert.equal(model.depletion, null)
  assertNoNaN(model)
})

test('전부 음수면 시작부터 고갈로 보고 축에 0원 선이 남는다', () => {
  const model = buildFanChart({ baselineRows: rows([-1, -2, -3].map((n) => n * EOK)) })
  assert.equal(model.depletion.fromStart, true)
  assert.equal(model.depletion.age, 40)
  assert.ok(model.zeroY >= model.view.top && model.zeroY <= model.view.bottom)
  assertNoNaN(model)
})

test('0원을 여러 번 지나가도 처음 교차한 나이만 보간해서 알린다', () => {
  const points = [
    { age: 60, value: 2 * EOK },
    { age: 61, value: -2 * EOK },
    { age: 62, value: 1 * EOK },
    { age: 63, value: -1 * EOK },
  ]
  const depletion = findDepletion(points)
  assert.equal(depletion.fromStart, false)
  assert.equal(depletion.age, 60.5)
})

test('은퇴 나이가 그래프 기간 밖이면 표시를 생략하고 그 사실을 알린다', () => {
  const model = buildFanChart({ baselineRows: rows([EOK, EOK], 60), retirementAge: 30 })
  assert.equal(model.retirement, null)
  assert.equal(model.retirementOutside, true)
  assertNoNaN(model)
})

test('비교안 기간이 기준안과 달라도 두 계열이 모두 축 안에 들어온다', () => {
  const model = buildFanChart({
    baselineRows: rows([EOK, 2 * EOK, 3 * EOK], 60),
    comparisonRows: rows([EOK, 2 * EOK, 3 * EOK, 4 * EOK, 5 * EOK], 58),
    retirementAge: 60,
    comparisonRetirementAge: 65,
  })
  assert.deepEqual(model.ageRange, { from: 58, to: 62 })
  assert.equal(model.comparisonEnd.age, 62)
  assertNoNaN(model)
  // 은퇴 나이 65세는 그린 기간(58~62) 밖이라 표시하지 않는다.
  assert.equal(model.comparisonRetirement, null)
})

test('기간이 몇 해뿐이어도 나이 눈금이 중복 없이 나온다', () => {
  const model = buildFanChart({ baselineRows: rows([EOK, EOK, EOK], 64) })
  const ages = model.xTicks.map((tick) => tick.age)
  assert.deepEqual(ages, [...new Set(ages)])
  assert.equal(ages[0], 64)
  assert.equal(ages[ages.length - 1], 66)
  assertNoNaN(model)
})

test('값이 전부 0이어도 축 폭이 0이 되지 않는다', () => {
  const model = buildFanChart({
    baselineRows: [
      { age: 60, balance: { p10: 0, p50: 0, p90: 0 } },
      { age: 61, balance: { p10: 0, p50: 0, p90: 0 } },
    ],
  })
  assertNoNaN(model)
  assert.ok(model.yTicks.length >= 1)
  assert.notEqual(model.view.top, model.view.bottom)
})

test('금액 눈금은 억 단위로 읽힌다', () => {
  assert.equal(formatAxisAmount(0), '0')
  assert.equal(formatAxisAmount(10 * EOK), '10억')
  assert.equal(formatAxisAmount(-5 * EOK), '-5억')
  assert.equal(formatAxisAmount(2.5 * EOK), '2.5억')
  assert.equal(formatAxisAmount(5_000_000), '500만')
})

test('설명 문장은 고갈 여부에 따라 달라진다', () => {
  const depleting = buildFanChart({ baselineRows: rows([2 * EOK, -2 * EOK], 60), retirementAge: 60 })
  assert.match(describeFanChart(depleting), /0원이 되고/)
  const surviving = buildFanChart({ baselineRows: rows([2 * EOK, 3 * EOK], 60) })
  assert.match(describeFanChart(surviving), /0원 아래로 내려가지 않고/)
})
