<script setup>
// 나이별 금융자산 전망(팬 차트). 표 대신 범위를 보여 주는 것이 몬테카를로의 핵심이라
// 전체 asset_percentiles 를 그대로 그린다. 차트 라이브러리 없이 인라인 SVG 만 쓴다.
import { computed } from 'vue'
import { buildFanChart, describeFanChart } from '@/components/simulation/fanChart.js'
import { fmtKRW } from '@/utils/format'

const props = defineProps({
  baselineRows: { type: Array, default: () => [] },
  comparisonRows: { type: Array, default: () => [] },
  retirementAge: { type: Number, default: null },
  comparisonRetirementAge: { type: Number, default: null },
  compact: { type: Boolean, default: false },
})

// 슬라이드(compact)는 폭이 좁아 같은 viewBox 를 줄여 그리면 글자가 읽히지 않는다.
// 그래서 viewBox 좌표계 안에서 글자를 키우고 여백을 다시 잡는다.
const fontSize = computed(() => (props.compact ? 19 : 12))
const hasComparison = computed(() => props.comparisonRows.length > 0)

const model = computed(() => buildFanChart({
  baselineRows: props.baselineRows,
  comparisonRows: props.comparisonRows,
  retirementAge: props.retirementAge,
  comparisonRetirementAge: props.comparisonRetirementAge,
  fontSize: fontSize.value,
}))

const description = computed(() => describeFanChart(model.value, { hasComparison: hasComparison.value }))
const view = computed(() => model.value.view)
const fs = computed(() => view.value.fontSize)

// 끝쪽 라벨이 그림 밖으로 나가지 않도록 기준점을 바꾼다.
function anchorFor(x, margin = 70) {
  if (x < view.value.left + margin) return 'start'
  if (x > view.value.right - margin) return 'end'
  return 'middle'
}
function anchorOffset(anchor, x, gap) {
  if (anchor === 'start') return x + gap
  if (anchor === 'end') return x - gap
  return x
}

const depletionLabel = computed(() => {
  const depletion = model.value.depletion
  if (!depletion) return null
  const age = Math.round(depletion.age)
  const anchor = anchorFor(depletion.x, fs.value * 5)
  return {
    text: hasComparison.value ? `지금 계획 ${age}세 고갈` : `${age}세 고갈`,
    anchor,
    x: anchorOffset(anchor, depletion.x, fs.value * 0.6),
    y: model.value.zeroY + fs.value * 2.6,
  }
})

// 고갈 지점이 없으면 "얼마가 남는지" 를 끝점에 적는다.
const survivalLabel = computed(() => {
  const end = model.value.medianEnd
  if (!end || model.value.depletion || hasComparison.value) return null
  return {
    text: `${end.age}세 ${fmtKRW(end.value)} 남음`,
    x: end.x - fs.value * 0.5,
    y: end.y - fs.value * 1.2,
  }
})

const comparisonEndLabel = computed(() => {
  const end = model.value.comparisonEnd
  if (!end || !hasComparison.value) return null
  return {
    text: end.value >= 0 ? `${end.age}세 ${fmtKRW(end.value)} 남음` : `${end.age}세 ${fmtKRW(end.value)}`,
    x: end.x - fs.value * 0.5,
    y: end.y - fs.value * 1.2,
  }
})

const retirementLabel = computed(() => {
  const marker = model.value.retirement
  if (!marker) return null
  const anchor = marker.x > view.value.right - fs.value * 7 ? 'end' : 'start'
  return {
    text: hasComparison.value ? `지금 은퇴 ${marker.age}세` : `은퇴 ${marker.age}세`,
    anchor,
    x: anchorOffset(anchor, marker.x, fs.value * 0.45),
    y: view.value.top + fs.value,
  }
})

const comparisonRetirementLabel = computed(() => {
  const marker = model.value.comparisonRetirement
  if (!marker) return null
  const anchor = marker.x > view.value.right - fs.value * 7 ? 'end' : 'start'
  return {
    text: `바꾼 은퇴 ${marker.age}세`,
    anchor,
    x: anchorOffset(anchor, marker.x, fs.value * 0.45),
    y: view.value.top + fs.value * 2.6,
  }
})
</script>

<template>
  <section class="chart-card" :class="{ compact }">
    <div class="chart-head">
      <h3>나이별 금융자산 전망</h3>
      <div class="legend">
        <span class="legend-item">
          <span class="swatch band" :class="{ muted: hasComparison }"></span>10~90% 범위
        </span>
        <span class="legend-item">
          <span class="swatch line" :class="{ muted: hasComparison }"></span>{{ hasComparison ? '지금 계획' : '중앙값' }}
        </span>
        <span v-if="hasComparison" class="legend-item">
          <span class="swatch line changed"></span>바꾼 조건
        </span>
      </div>
    </div>

    <p v-if="model.empty" class="chart-empty">
      나이별 자산 전망을 그릴 데이터가 아직 없습니다.
    </p>

    <svg
      v-else
      class="chart-svg"
      :viewBox="`0 0 ${view.width} ${view.height}`"
      preserveAspectRatio="xMidYMid meet"
      role="img"
      :aria-label="description"
    >
      <title>{{ description }}</title>

      <!-- 가로 기준선 -->
      <line
        v-for="(y, index) in model.gridLines"
        :key="`grid-${index}`"
        :x1="view.left"
        :y1="y"
        :x2="view.right"
        :y2="y"
        stroke="#eef0f6"
        stroke-width="1"
      />

      <!-- 10~90% 범위 -->
      <path :d="model.bandPath" :fill="hasComparison ? '#e4e5ef' : '#dcdcf7'" :fill-opacity="hasComparison ? 0.8 : 0.75" />

      <!-- 0원 선 -->
      <line
        :x1="view.left"
        :y1="model.zeroY"
        :x2="view.right"
        :y2="model.zeroY"
        stroke="#b4232a"
        stroke-width="1.5"
        stroke-dasharray="5 4"
      />
      <text :x="view.left + 4" :y="model.zeroY + fs * 1.2" class="zero-label" :style="{ fontSize: `${fs}px` }">0원</text>

      <!-- 은퇴 시점 -->
      <template v-if="model.retirement">
        <line
          :x1="model.retirement.x"
          :y1="view.top"
          :x2="model.retirement.x"
          :y2="view.bottom"
          stroke="#8b90a6"
          stroke-width="1"
          stroke-dasharray="3 4"
        />
        <text
          :x="retirementLabel.x"
          :y="retirementLabel.y"
          :text-anchor="retirementLabel.anchor"
          class="marker-label"
          :style="{ fontSize: `${fs}px` }"
        >{{ retirementLabel.text }}</text>
      </template>

      <template v-if="model.comparisonRetirement">
        <line
          :x1="model.comparisonRetirement.x"
          :y1="view.top"
          :x2="model.comparisonRetirement.x"
          :y2="view.bottom"
          stroke="#4e9468"
          stroke-width="1"
          stroke-dasharray="3 4"
        />
        <text
          :x="comparisonRetirementLabel.x"
          :y="comparisonRetirementLabel.y"
          :text-anchor="comparisonRetirementLabel.anchor"
          class="marker-label changed"
          :style="{ fontSize: `${fs}px` }"
        >{{ comparisonRetirementLabel.text }}</text>
      </template>

      <!-- 중앙값 (비교가 있으면 뒤로 물러난다) -->
      <path
        :d="model.medianPath"
        fill="none"
        :stroke="hasComparison ? '#7e83a0' : '#5e5ce6'"
        :stroke-width="hasComparison ? 2.5 : 3"
        :stroke-dasharray="hasComparison ? '7 5' : undefined"
        stroke-linecap="round"
        stroke-linejoin="round"
      />

      <!-- 바꾼 조건 중앙값 -->
      <path
        v-if="hasComparison"
        :d="model.comparisonPath"
        fill="none"
        stroke="#1f7a44"
        stroke-width="3.5"
        stroke-linecap="round"
        stroke-linejoin="round"
      />

      <!-- 고갈 지점 -->
      <template v-if="model.depletion">
        <circle :cx="model.depletion.x" :cy="model.depletion.y" r="5.5" fill="#fff" stroke="#b4232a" stroke-width="2.5" />
        <text
          :x="depletionLabel.x"
          :y="depletionLabel.y"
          :text-anchor="depletionLabel.anchor"
          class="depletion-label"
          :style="{ fontSize: `${fs * 1.05}px` }"
        >{{ depletionLabel.text }}</text>
      </template>

      <!-- 고갈 없이 버틴 경우 끝점 금액 -->
      <template v-if="survivalLabel">
        <circle :cx="model.medianEnd.x" :cy="model.medianEnd.y" r="5.5" fill="#fff" stroke="#5e5ce6" stroke-width="2.5" />
        <text
          :x="survivalLabel.x"
          :y="survivalLabel.y"
          text-anchor="end"
          class="survival-label"
          :style="{ fontSize: `${fs * 1.05}px` }"
        >{{ survivalLabel.text }}</text>
      </template>

      <template v-if="comparisonEndLabel">
        <circle :cx="model.comparisonEnd.x" :cy="model.comparisonEnd.y" r="5.5" fill="#fff" stroke="#1f7a44" stroke-width="2.5" />
        <text
          :x="comparisonEndLabel.x"
          :y="comparisonEndLabel.y"
          text-anchor="end"
          class="marker-label changed"
          :style="{ fontSize: `${fs * 1.05}px` }"
        >{{ comparisonEndLabel.text }}</text>
      </template>

      <!-- 세로축 -->
      <text
        v-for="tick in model.yTicks"
        :key="`y-${tick.value}`"
        :x="view.left - 8"
        :y="tick.y + fs * 0.34"
        text-anchor="end"
        class="axis-label"
        :style="{ fontSize: `${fs}px` }"
      >{{ tick.label }}</text>

      <!-- 가로축 -->
      <text
        v-for="tick in model.xTicks"
        :key="`x-${tick.age}`"
        :x="tick.x"
        :y="view.bottom + fs * 1.55"
        text-anchor="middle"
        class="axis-label"
        :style="{ fontSize: `${fs}px` }"
      >{{ tick.label }}</text>
    </svg>

    <p v-if="model.retirementOutside" class="chart-note">
      은퇴 나이가 그래프에 표시된 기간 밖이라 은퇴 시점 표시를 생략했습니다.
    </p>
  </section>
</template>

<style scoped>
.chart-card {
  box-sizing: border-box;
  padding: 24px 28px 20px;
  border: 1px solid #e9eaf2;
  border-radius: 16px;
  background: #fff;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.chart-head { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.chart-head h3 { margin: 0; font-size: 17px; font-weight: 800; color: #1a1a2e; }
.legend { display: flex; align-items: center; gap: 18px; font-size: 13px; color: #5b6076; }
.legend-item { display: inline-flex; align-items: center; gap: 7px; }
.swatch { flex: none; }
.swatch.band { width: 18px; height: 10px; border-radius: 3px; background: #dcdcf7; }
.swatch.band.muted { background: #d3d5e2; }
.swatch.line { width: 18px; height: 3px; border-radius: 2px; background: #5e5ce6; }
.swatch.line.muted { background: #7e83a0; }
.swatch.line.changed { background: #1f7a44; }
/* 카드가 세로 flex 라 flex: none 이 없으면 좁은 상자 안에서 그래프가 찌그러진다. */
.chart-svg { flex: none; width: 100%; height: 340px; display: block; }
.chart-empty { margin: 0; padding: 40px 0; text-align: center; color: #5b6076; font-size: 14px; }
.chart-note { margin: 0; font-size: 12px; color: #5b6076; }
/* 축 글자는 흰 배경에서 4.5:1 을 넘기려고 시안의 #8b90a6 보다 어둡게 잡았다. */
.axis-label { fill: #6e738c; }
/* 주석 글자가 범위 면이나 선 위에 얹혀도 읽히도록 흰 테두리를 먼저 칠한다. */
.zero-label, .marker-label, .depletion-label, .survival-label {
  paint-order: stroke fill;
  stroke: #fff;
  stroke-width: 3px;
  stroke-linejoin: round;
}
.zero-label { fill: #b4232a; font-weight: 700; }
.marker-label { fill: #5b6076; font-weight: 700; }
.marker-label.changed { fill: #176035; }
.depletion-label { fill: #b4232a; font-weight: 800; }
.survival-label { fill: #4543b8; font-weight: 800; }

.compact { padding: 12px 14px 10px; gap: 8px; border-radius: 12px; }
.compact .chart-head h3 { font-size: 14px; }
.compact .legend { gap: 10px; font-size: 11px; }
.compact .chart-svg { height: 200px; }
</style>
