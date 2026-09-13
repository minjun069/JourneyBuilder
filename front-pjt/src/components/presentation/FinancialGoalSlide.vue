<script setup>
import { computed } from 'vue'
import { useReportStore } from '@/stores/report'
import { fmtKRW } from '@/utils/format'

const reportStore = useReportStore()
const gd = computed(() => reportStore.reports?.['financial-goal-result'])

const prePhases  = computed(() => gd.value?.pre_retirement ?? [])
const postPhases = computed(() => gd.value?.post_retirement_phases ?? [])

function makeTimeline(phases) {
  if (!phases.length) return null
  const minAge    = phases[0].start_age
  const maxAge    = phases[phases.length - 1].end_age
  const totalSpan = maxAge - minAge

  function agePct(age) {
    return ((age - minAge) / totalSpan) * 100
  }

  const innerBoundaries = phases.slice(1).map(p => p.start_age)

  const ageMarkers = [...new Set([
    ...phases.map(p => p.start_age),
    ...phases.flatMap(p => [
      ...p.expected_expenses.map(e => e.age),
      ...p.achieved_funds.map(e => e.age),
    ]),
  ])].sort((a, b) => a - b)

  const allEvents = phases
    .flatMap(p => [
      ...p.expected_expenses.map(e => ({ ...e, kind: 'expense' })),
      ...p.achieved_funds.map(e => ({ ...e, kind: 'income' })),
    ])
    .sort((a, b) => a.age - b.age)

  // flex 값: 다음 구간 start까지의 거리 (마지막은 end-start)
  // → agePct 기준(totalSpan = maxAge-minAge)과 일치
  const phasesWithSpan = phases.map((p, i) => ({
    ...p,
    span: i < phases.length - 1
      ? phases[i + 1].start_age - p.start_age
      : p.end_age - p.start_age,
  }))

  return { phases: phasesWithSpan, agePct, innerBoundaries, ageMarkers, allEvents }
}

const preTl  = computed(() => makeTimeline(prePhases.value))
const postTl = computed(() => makeTimeline(postPhases.value))

function style(i, isPre, total) {
  const hueStart = isPre ? 215 : 270
  const hueEnd   = isPre ? 185 : 220
  const hue = total <= 1 ? hueStart : hueStart + (hueEnd - hueStart) * (i / (total - 1))
  return {
    bg:     `hsl(${hue}, 65%, 94%)`,
    color:  `hsl(${hue}, 75%, 35%)`,
    border: `hsl(${hue}, 55%, 78%)`,
  }
}

function phaseItems(phase) {
  return phase.desired_expenses.flatMap(d => d.items)
}

const fmt = fmtKRW
</script>

<template>
  <div class="slide">

    <!-- 은퇴 전 -->
    <div v-if="preTl" class="section">
      <div class="section-label">은퇴 전</div>
      <div class="tl-root">
        <div class="divider-overlay" aria-hidden="true">
          <!-- <div
            v-for="age in preTl.innerBoundaries" :key="age"
            class="v-divider"
            :style="{ left: preTl.agePct(age) + '%' }"
          /> -->
        </div>

        <div class="events-zone">
          <div
            v-for="ev in preTl.allEvents" :key="`${ev.age}-${ev.content}`"
            class="event-col"
            :style="{ left: preTl.agePct(ev.age) + '%' }"
          >
            <div class="event-chip" :class="ev.kind === 'income' ? 'chip--income' : 'chip--expense'">
              <span class="chip-content">{{ ev.content }}</span>
              <span class="chip-amount">{{ ev.kind === 'expense' ? '-' : '+' }} {{ fmt(ev.current_amount) }}</span>
            </div>
            <div class="event-line" :class="ev.kind === 'income' ? 'line--income' : 'line--expense'"></div>
          </div>
        </div>

        <!-- <div class="ages-zone">
          <div
            v-for="age in preTl.ageMarkers" :key="age"
            class="age-label"
            :style="{ left: preTl.agePct(age) + '%' }"
          >{{ age }}세</div>
        </div> -->

        <div class="tl-bar">
          <div
            v-for="(phase, i) in preTl.phases" :key="phase.phase_name + '-s'"
            class="tl-seg"
            :style="{ flex: phase.span, background: style(i, true, preTl.phases.length).bg }"
          >
            <span class="seg-label" :style="{ color: style(i, true, preTl.phases.length).color }">{{ phase.phase_name }}</span>
          </div>
          <div class="dots-overlay">
            <div
              v-for="ev in preTl.allEvents" :key="`dot-${ev.age}-${ev.content}`"
              class="event-dot"
              :class="ev.kind === 'income' ? 'dot--income' : 'dot--expense'"
              :style="{ left: preTl.agePct(ev.age) + '%' }"
            >{{ ev.age }}</div>
          </div>
        </div>

        <div class="popup-row">
          <div
            v-for="(phase, i) in preTl.phases" :key="phase.phase_name + '-c'"
            class="popup-cell"
            :style="{ flex: phase.span }"
          >
            <div class="popup-tail" :style="{ borderBottomColor: style(i, true, preTl.phases.length).border }"></div>
            <div class="expense-card" :style="{ borderColor: style(i, true, preTl.phases.length).border }">
              <div class="ec-title" :style="{ color: style(i, true, preTl.phases.length).color, background: style(i, true, preTl.phases.length).bg }">
                예상 지출 (월별)
              </div>
              <div class="ec-items">
                <template v-for="item in phaseItems(phase)" :key="item.name">
                  <span class="ec-name">{{ item.name }}</span>
                  <span class="ec-amount">{{ fmt(item.amount) }}</span>
                </template>
              </div>
              <div v-if="!phaseItems(phase).length" class="ec-empty">항목 없음</div>
            </div>
          </div>
        </div>

      </div>
    </div>

    <div class="divider-h"></div>

    <!-- 은퇴 후 -->
    <div v-if="postTl" class="section">
      <div class="section-label">은퇴 후</div>
      <div class="tl-root">
        <div class="divider-overlay" aria-hidden="true">
          <!-- <div
            v-for="age in postTl.innerBoundaries" :key="age"
            class="v-divider"
            :style="{ left: postTl.agePct(age) + '%' }"
          /> -->
        </div>

        <div class="events-zone">
          <div
            v-for="ev in postTl.allEvents" :key="`${ev.age}-${ev.content}`"
            class="event-col"
            :style="{ left: postTl.agePct(ev.age) + '%' }"
          >
            <div class="event-chip" :class="ev.kind === 'income' ? 'chip--income' : 'chip--expense'">
              <span class="chip-content">{{ ev.content }}</span>
              <span class="chip-amount">{{ ev.kind === 'expense' ? '-' : '+' }} {{ fmt(ev.current_amount) }}</span>
            </div>
            <div class="event-line" :class="ev.kind === 'income' ? 'line--income' : 'line--expense'"></div>
          </div>
        </div>

        <!-- <div class="ages-zone">
          <div
            v-for="age in postTl.ageMarkers" :key="age"
            class="age-label"
            :style="{ left: postTl.agePct(age) + '%' }"
          >{{ age }}세</div>
        </div> -->

        <div class="tl-bar">
          <div
            v-for="(phase, i) in postTl.phases" :key="phase.phase_name + '-s'"
            class="tl-seg"
            :style="{ flex: phase.span, background: style(i, false, postTl.phases.length).bg }"
          >
            <span class="seg-label" :style="{ color: style(i, false, postTl.phases.length).color }">{{ phase.phase_name }}</span>
          </div>
          <div class="dots-overlay">
            <div
              v-for="ev in postTl.allEvents" :key="`dot-${ev.age}-${ev.content}`"
              class="event-dot"
              :class="ev.kind === 'income' ? 'dot--income' : 'dot--expense'"
              :style="{ left: postTl.agePct(ev.age) + '%' }"
            >{{ ev.age }}</div>
          </div>
        </div>

        <div class="popup-row">
          <div
            v-for="(phase, i) in postTl.phases" :key="phase.phase_name + '-c'"
            class="popup-cell"
            :style="{ flex: phase.span }"
          >
            <div class="popup-tail" :style="{ borderBottomColor: style(i, false, postTl.phases.length).border }"></div>
            <div class="expense-card" :style="{ borderColor: style(i, false, postTl.phases.length).border }">
              <div class="ec-title" :style="{ color: style(i, false, postTl.phases.length).color, background: style(i, false, postTl.phases.length).bg }">
                예상 지출 (월별)
              </div>
              <div class="ec-items">
                <template v-for="item in phaseItems(phase)" :key="item.name">
                  <span class="ec-name">{{ item.name }}</span>
                  <span class="ec-amount">{{ fmt(item.amount) }}</span>
                </template>
              </div>
              <div v-if="!phaseItems(phase).length" class="ec-empty">항목 없음</div>
            </div>
          </div>
        </div>

      </div>
    </div>

  </div>
</template>

<style scoped>
.slide {
  width: 100%; height: 100%;
  padding: 12px 20px;
  box-sizing: border-box;
  background: #f5f5f2;
  display: flex; flex-direction: column; gap: 0;
  overflow: hidden;
}

.section {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  justify-content: center;
}

.section-label {
  font-size: 20px; font-weight: 800; letter-spacing: 0.08em;
  text-transform: uppercase; color: #474646;
  white-space: nowrap;
  margin-bottom: 8px;
  flex-shrink: 0;
}

.divider-h { height: 1px; background: #e5e7eb; margin: 8px 0; flex-shrink: 0; }

.tl-root { position: relative; width: 100%; display: flex; flex-direction: column; padding: 0 40px; box-sizing: border-box; }

/* 점선 오버레이 */
.divider-overlay { position: absolute; inset: 0 40px; pointer-events: none; z-index: 1; }
.v-divider {
  position: absolute; top: 0; bottom: 0;
  border-left: 1.5px dashed #d1d5db;
  transform: translateX(-50%);
}

/* 카드 */
.popup-row { margin-top: 10px; display: flex; gap: 0; position: relative; z-index: 2; }
.popup-cell { display: flex; flex-direction: column; align-items: center; min-width: 0; padding: 0 3px; }

.expense-card {
  width: max-content;
  background: #fff; border: 1.5px solid; border-radius: 8px; overflow: hidden;
}
.ec-title { font-size: 10px; font-weight: 800; padding: 8px 20px 6px; text-align: center; }
.ec-items {
  display: grid; grid-template-columns: auto 1fr;
  column-gap: 16px; row-gap: 0;
  padding: 8px 20px 8px; font-size: 9.5px; color: #374151; line-height: 1.8;
}
.ec-name   { text-align: left; white-space: nowrap; }
.ec-amount { text-align: right; white-space: nowrap; }
.ec-empty  { font-size: 9.5px; color: #9ca3af; padding: 6px 12px 8px; text-align: center; }

.popup-tail {
  width: 0; height: 0;
  border-left: 6px solid transparent;
  border-right: 6px solid transparent;
  border-bottom: 6px solid;
}

/* 이벤트 */
.events-zone { position: relative; height: 50px; z-index: 2; }
.event-col {
  position: absolute; transform: translateX(-50%);
  display: flex; flex-direction: column; align-items: center;
  top: 0; height: 100%;
}
.event-chip {
  border-radius: 5px; overflow: hidden;
  font-size: 9px; text-align: center;
  white-space: nowrap; flex-shrink: 0;
  display: flex; flex-direction: column;
}
.chip-amount  { font-weight: 700; padding: 2px 6px; line-height: 1.5; }
.chip-content { padding: 2px 6px; line-height: 1.5; background: #fffbfb; color: #333; font-weight: 600; }
.chip--expense { background: #fde8e8; color: #555; }
.chip--income  { background: #e0f5e9; color: #555; }
.event-line { width: 2px; flex: 1; }
.line--expense { background: #e8a09a; }
.line--income  { background: #7ec89a; }

/* 타임라인 바 */
.tl-bar { display: flex; height: 36px; border-radius: 3px; overflow: visible; position: relative; z-index: 2; }
.tl-seg { display: flex; align-items: center; justify-content: center; overflow: hidden; }
.tl-seg + .tl-seg { border-left: 1px solid rgba(255,255,255,0.5); }
.seg-label { font-size: 13px; font-weight: 800; letter-spacing: 0.04em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dots-overlay { position: absolute; inset: 0; pointer-events: none; }
.event-dot {
  position: absolute; top: 0; transform: translate(-50%, -50%);
  width: 20px; height: 20px; border-radius: 50%;
  border: 2px solid #fff; box-shadow: 0 0 0 1px rgba(0,0,0,0.1);
  display: flex; align-items: center; justify-content: center;
  font-size: 9px; font-weight: 800; color: #ffffff;
}
.dot--expense { background: #e8a09a; }
.dot--income  { background: #7ec89a; }

/* 나이 레이블 */
.ages-zone { position: relative; height: 18px; margin-top: 3px; }
.age-label {
  position: absolute; transform: translateX(-50%);
  font-size: 10px; font-weight: 600; color: #6b7280; white-space: nowrap;
}
</style>
