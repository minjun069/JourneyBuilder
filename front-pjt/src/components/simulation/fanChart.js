// 자산 궤적 팬 차트의 좌표 계산만 담당한다.
// SVG 마크업(SimulationFanChart.vue)과 분리한 이유는 축·고갈 시점 계산에
// 빈 배열·한 줄짜리 입력·전부 음수 같은 경계 조건이 많아 따로 테스트해야 하기 때문이다.
// node --test 로도 돌리는 모듈이라 별칭 대신 상대 경로를 쓴다.
import { fmtKRW } from '../../utils/format.js'

const EOK = 100_000_000
const MAN = 10_000
// 축 눈금 후보. 억 단위로 읽히도록 1·2·2.5·5·10 배수만 쓴다.
const STEP_MULTIPLIERS = [1, 2, 2.5, 5, 10]
const AGE_STRIDES = [1, 2, 5, 10, 20, 25, 50]

function isFiniteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value)
}

// asset_percentiles 를 그릴 수 있는 형태로만 추린다. 서버 계약상 채워져 있지만
// 화면이 응답 형식 하나로 깨지면 안 되므로 여기서 한 번 더 거른다.
export function sanitizeRows(rows) {
  if (!Array.isArray(rows)) return []
  const byAge = new Map()
  for (const row of rows) {
    if (!row || typeof row !== 'object') continue
    const age = row.age
    // 서버 형태({age, balance})와 이미 한 번 걸러진 형태({age, p10, p50, p90})를 모두 받는다.
    // 패널과 차트가 각각 걸러도 결과가 같아야 하기 때문이다.
    const balance = row.balance ?? row
    if (!isFiniteNumber(age) || !balance || typeof balance !== 'object') continue
    if (!isFiniteNumber(balance.p10) || !isFiniteNumber(balance.p50) || !isFiniteNumber(balance.p90)) continue
    // 같은 나이가 두 번 오면 뒤엣것을 쓴다.
    byAge.set(age, { age, p10: balance.p10, p50: balance.p50, p90: balance.p90 })
  }
  return [...byAge.values()].sort((a, b) => a.age - b.age)
}

function niceStep(rough) {
  if (!isFiniteNumber(rough) || rough <= 0) return EOK
  const exponent = Math.floor(Math.log10(rough))
  const base = 10 ** exponent
  for (const multiplier of STEP_MULTIPLIERS) {
    if (base * multiplier >= rough) return base * multiplier
  }
  return base * 10
}

// 금액 축은 "억" 로 읽는다. 1억이 안 되는 구간은 만원으로 떨어뜨린다.
export function formatAxisAmount(value) {
  if (!isFiniteNumber(value)) return ''
  if (value === 0) return '0'
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (abs >= EOK) {
    const eok = abs / EOK
    const text = eok >= 10 ? Math.round(eok).toString() : (Math.round(eok * 10) / 10).toString()
    return `${sign}${text}억`
  }
  const man = Math.round(abs / MAN)
  return `${sign}${man.toLocaleString('ko-KR')}만`
}

function buildYTicks(min, max, step) {
  const ticks = []
  const first = Math.ceil(min / step) * step
  for (let value = first; value <= max + step * 1e-6 && ticks.length < 12; value += step) {
    // 부동소수 누적 오차를 눈금 단위로 되돌린다.
    ticks.push(Math.round(value / step) * step)
  }
  if (ticks.length === 0) ticks.push(0)
  return ticks
}

function buildAgeTicks(ages) {
  const first = ages[0]
  const last = ages[ages.length - 1]
  if (first === last) return [first]
  const span = last - first
  const stride = AGE_STRIDES.find((candidate) => span / candidate <= 6) ?? Math.ceil(span / 6)
  const ticks = [first]
  const start = Math.ceil(first / stride) * stride
  for (let age = start; age < last; age += stride) {
    if (age > first) ticks.push(age)
  }
  ticks.push(last)
  // 끝 눈금과 겹치는 안쪽 눈금은 뺀다. (예: 38~90세에서 40세와 38세)
  const gap = Math.max(1, Math.round(span / 12))
  return ticks.filter((age, index) => {
    if (index === 0 || index === ticks.length - 1) return true
    return age - first >= gap && last - age >= gap
  })
}

function linePath(points) {
  if (points.length === 0) return ''
  // 점이 하나면 stroke-linecap="round" 로 점 하나를 찍는다.
  if (points.length === 1) {
    const [only] = points
    return `M${only.x} ${only.y} L${only.x} ${only.y}`
  }
  return points.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x} ${point.y}`).join(' ')
}

function areaPath(upper, lower) {
  if (upper.length === 0) return ''
  if (upper.length === 1) {
    // 한 줄짜리 결과도 범위가 보이도록 폭 12짜리 막대로 그린다.
    const [top] = upper
    const [bottom] = lower
    const half = 6
    return `M${top.x - half} ${top.y} L${top.x + half} ${top.y} L${bottom.x + half} ${bottom.y} L${bottom.x - half} ${bottom.y} Z`
  }
  const forward = upper.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x} ${point.y}`).join(' ')
  const backward = [...lower].reverse().map((point) => `L${point.x} ${point.y}`).join(' ')
  return `${forward} ${backward} Z`
}

function round(value) {
  return Math.round(value * 100) / 100
}

// p50 이 처음으로 0원 아래로 내려가는 나이를 선형 보간으로 찾는다.
// 여러 번 교차해도 처음 한 번만 알린다. 시작부터 음수면 교차가 아니라 "이미 바닥"이다.
export function findDepletion(points) {
  if (points.length === 0) return null
  if (points[0].value < 0) return { age: points[0].age, fromStart: true }
  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1]
    const current = points[index]
    if (current.value < 0 && previous.value >= 0) {
      const drop = previous.value - current.value
      const ratio = drop === 0 ? 0 : previous.value / drop
      return { age: previous.age + (current.age - previous.age) * ratio, fromStart: false }
    }
  }
  return null
}

/**
 * 팬 차트 좌표 모델을 만든다. 입력이 비어 있으면 empty: true 만 돌려주고
 * 나머지 필드는 그리지 않아도 되는 기본값으로 채운다. NaN 좌표는 내보내지 않는다.
 */
export function buildFanChart({
  baselineRows = [],
  comparisonRows = [],
  retirementAge = null,
  comparisonRetirementAge = null,
  width = 860,
  height = 360,
  fontSize = 12,
} = {}) {
  const base = sanitizeRows(baselineRows)
  const compare = sanitizeRows(comparisonRows)

  const left = Math.round(26 + fontSize * 3.6)
  const right = width - Math.round(fontSize * 5.8)
  const top = Math.round(fontSize * 2.6)
  const bottom = height - Math.round(fontSize * 2.4)

  const view = { width, height, left, right, top, bottom, fontSize }
  const emptyModel = {
    empty: true,
    view,
    gridLines: [],
    yTicks: [],
    xTicks: [],
    bandPath: '',
    medianPath: '',
    comparisonPath: '',
    zeroY: null,
    retirement: null,
    comparisonRetirement: null,
    depletion: null,
    medianEnd: null,
    comparisonEnd: null,
    ageRange: null,
  }
  if (base.length === 0) return emptyModel

  // x 축은 두 계열을 모두 담는다. 비교안의 기간이 달라도 잘리지 않게 한다.
  const ages = [...base.map((row) => row.age), ...compare.map((row) => row.age)]
  const ageMin = Math.min(...ages)
  const ageMax = Math.max(...ages)
  const ageSpan = ageMax - ageMin
  const centerX = (left + right) / 2
  const scaleX = (age) => (ageSpan === 0 ? centerX : left + ((age - ageMin) / ageSpan) * (right - left))

  // y 축은 0원 선이 항상 보이도록 0 을 포함시킨다.
  const values = [0]
  for (const row of base) values.push(row.p10, row.p50, row.p90)
  for (const row of compare) values.push(row.p10, row.p50, row.p90)
  let valueMin = Math.min(...values)
  let valueMax = Math.max(...values)
  if (valueMax - valueMin < 1) {
    // 전부 0원인 경우에도 축이 무너지지 않게 최소 폭을 준다.
    const pad = Math.max(Math.abs(valueMax) * 0.1, MAN)
    valueMin -= pad
    valueMax += pad
  }
  const rawSpan = valueMax - valueMin
  const step = niceStep(rawSpan / 4)
  const paddedMin = Math.min(valueMin - rawSpan * 0.08, 0)
  const paddedMax = Math.max(valueMax + rawSpan * 0.08, 0)
  const domainSpan = paddedMax - paddedMin
  const scaleY = (value) => (
    domainSpan === 0 ? (top + bottom) / 2 : bottom - ((value - paddedMin) / domainSpan) * (bottom - top)
  )

  const upper = base.map((row) => ({ x: round(scaleX(row.age)), y: round(scaleY(row.p90)) }))
  const lower = base.map((row) => ({ x: round(scaleX(row.age)), y: round(scaleY(row.p10)) }))
  const medianPoints = base.map((row) => ({
    age: row.age,
    value: row.p50,
    x: round(scaleX(row.age)),
    y: round(scaleY(row.p50)),
  }))
  const comparisonPoints = compare.map((row) => ({
    age: row.age,
    value: row.p50,
    x: round(scaleX(row.age)),
    y: round(scaleY(row.p50)),
  }))

  const tickValues = buildYTicks(paddedMin, paddedMax, step)
  const yTicks = tickValues.map((value) => ({
    value,
    y: round(scaleY(value)),
    label: formatAxisAmount(value),
  }))
  const zeroY = round(scaleY(0))
  const gridLines = yTicks.filter((tick) => tick.value !== 0).map((tick) => tick.y)

  const xTicks = buildAgeTicks([...new Set([...base.map((r) => r.age), ...compare.map((r) => r.age)])].sort((a, b) => a - b))
    .map((age) => ({ age, x: round(scaleX(age)), label: `${age}세` }))

  const insideRange = (age) => isFiniteNumber(age) && age >= ageMin && age <= ageMax
  const retirement = insideRange(retirementAge)
    ? { age: retirementAge, x: round(scaleX(retirementAge)) }
    : null
  const comparisonRetirement = (
    insideRange(comparisonRetirementAge) && comparisonRetirementAge !== retirementAge
      ? { age: comparisonRetirementAge, x: round(scaleX(comparisonRetirementAge)) }
      : null
  )

  const found = findDepletion(medianPoints)
  const depletion = found
    ? { age: found.age, fromStart: found.fromStart, x: round(scaleX(found.age)), y: zeroY }
    : null

  const medianEnd = medianPoints.length ? medianPoints[medianPoints.length - 1] : null
  const comparisonEnd = comparisonPoints.length ? comparisonPoints[comparisonPoints.length - 1] : null

  return {
    empty: false,
    view,
    gridLines,
    yTicks,
    xTicks,
    bandPath: areaPath(upper, lower),
    medianPath: linePath(medianPoints),
    comparisonPath: linePath(comparisonPoints),
    zeroY,
    retirement,
    retirementOutside: isFiniteNumber(retirementAge) && !insideRange(retirementAge),
    comparisonRetirement,
    depletion,
    medianEnd,
    comparisonEnd,
    ageRange: { from: ageMin, to: ageMax },
  }
}

// 그래프를 볼 수 없는 사람에게 같은 내용을 문장으로 준다(role="img" 의 aria-label).
export function describeFanChart(model, { hasComparison = false } = {}) {
  if (!model || model.empty) return '표시할 자산 궤적 데이터가 없습니다.'
  const { from, to } = model.ageRange
  const parts = [`${from}세부터 ${to}세까지 금융자산 전망 그래프입니다.`]
  if (model.medianEnd) {
    parts.push(
      model.depletion
        ? `중앙값은 ${Math.round(model.depletion.age)}세 무렵 0원이 되고 ${to}세에 ${fmtKRW(model.medianEnd.value)}이 됩니다.`
        : `중앙값은 ${to}세까지 0원 아래로 내려가지 않고 ${fmtKRW(model.medianEnd.value)}이 남습니다.`,
    )
  }
  if (model.retirement) parts.push(`은퇴 시점 ${model.retirement.age}세를 세로선으로 표시했습니다.`)
  if (hasComparison && model.comparisonEnd) {
    parts.push(`바꾼 조건의 중앙값은 ${to}세에 ${fmtKRW(model.comparisonEnd.value)}입니다.`)
  }
  return parts.join(' ')
}
