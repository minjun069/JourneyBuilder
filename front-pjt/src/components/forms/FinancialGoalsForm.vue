<script setup>
import { reactive, ref, computed } from 'vue'
import { createSecureUuid } from '@/utils/secureUuid'

const props = defineProps({
  currentAge:    { type: Number, default: 30 },
  retirementAge: { type: Number, default: 60 },
  endAge:        { type: Number, default: 90 },
})

// 지출 카테고리 (ExpensesForm과 동일)
const EXPENSE_CATS = ['주거비', '식비/생활비', '교통비', '통신비', '보험료', '교육비', '의료비', '여가/문화', '외식', '쇼핑', '대출상환', '기타']

const activeTab = ref('before')
const activeMode = reactive({ before: 'periods', after: 'periods' })
const curMode = computed({
  get: () => activeMode[activeTab.value],
  set: v => { activeMode[activeTab.value] = v },
})

const selectedPeriodIdx = reactive({ before: null, after: null })
const pendingPeriod     = reactive({ before: { from: '', to: '' }, after: { from: '', to: '' } })
const periodError       = ref('')

const form = reactive({
  before: { periods: [], milestones: [] },
  after:  { periods: [], milestones: [] },
})

const tabData = computed(() => form[activeTab.value])
const selIdx  = computed({
  get: () => selectedPeriodIdx[activeTab.value],
  set: v => { selectedPeriodIdx[activeTab.value] = v },
})
const pending = computed(() => pendingPeriod[activeTab.value])

const validAgeStart = computed(() => props.currentAge)
const validAgeEnd = computed(() => props.endAge)

const timelineStart = computed(() =>
  activeTab.value === 'before' ? props.currentAge : props.retirementAge
)
const timelineEnd = computed(() =>
  activeTab.value === 'before' ? props.retirementAge : props.endAge
)

const ticks = computed(() => {
  const s = timelineStart.value, e = timelineEnd.value
  const step = (e - s) > 30 ? 10 : 5
  const result = []
  const first = Math.ceil(s / step) * step
  for (let age = first; age < e; age += step) {
    if (age !== s) result.push(age)
  }
  return result
})

function toPercent(age) {
  const s = timelineStart.value, e = timelineEnd.value
  if (e <= s) return 0
  return Math.max(0, Math.min(100, (age - s) / (e - s) * 100))
}

function segWidthPct(p) {
  return toPercent(p.toAge) - toPercent(p.fromAge)
}

function overlaps(from, to) {
  return tabData.value.periods.some(p => from < p.toAge && to > p.fromAge)
}

function confirmPeriod() {
  const from = Number(pending.value.from)
  const to   = Number(pending.value.to)
  const s = validAgeStart.value, e = validAgeEnd.value
  periodError.value = ''
  if (!from || !to || from >= to) {
    periodError.value = '시작 나이와 종료 나이를 올바르게 입력해주세요.'
    return
  }
  if (from < s || to > e) {
    periodError.value = `${s}세부터 ${e}세까지 입력할 수 있습니다.`
    return
  }
  if (overlaps(from, to)) {
    periodError.value = '이미 입력한 기간과 겹칩니다.'
    return
  }
  tabData.value.periods.push({
    clientItemId: createSecureUuid(),
    fromAge: from,
    toAge: to,
    items: [],
  })
  selIdx.value = tabData.value.periods.length - 1
  pending.value.from = ''
  pending.value.to   = ''
}

function selectPeriod(i) {
  selIdx.value = i
  curMode.value = 'periods'
}

function deletePeriod(i) {
  tabData.value.periods.splice(i, 1)
  const len = tabData.value.periods.length
  selIdx.value = len === 0 ? null : Math.min(i, len - 1)
}

function addPeriodItem() {
  if (selIdx.value === null) return
  tabData.value.periods[selIdx.value].items.push({
    clientItemId: createSecureUuid(),
    name: EXPENSE_CATS[0],
    name__sel: EXPENSE_CATS[0],
    amount: '',
    type: '고정',
  })
}

function removePeriodItem(i) {
  tabData.value.periods[selIdx.value].items.splice(i, 1)
}

function onItemNameChange(item, val) {
  item.name__sel = val
  item.name = val === '기타' ? '' : val
}

function clearItemNameEtc(item) {
  item.name__sel = EXPENSE_CATS[0]
  item.name = EXPENSE_CATS[0]
}

function addMilestone() {
  tabData.value.milestones.push({
    clientItemId: createSecureUuid(),
    age: '',
    category: '소득',
    content: '',
    amount: '',
    note: '',
    committed: false,
  })
}

function commitMilestone(i) {
  const m = tabData.value.milestones[i]
  if (m.age) m.committed = true
}

function unlockMilestone(i) {
  tabData.value.milestones[i].committed = false
}

function removeMilestone(i) {
  tabData.value.milestones.splice(i, 1)
}

defineExpose({ form })
</script>

<template>
  <div class="goals-form">

    <!-- 상단 탭 -->
    <div class="top-tab-row">
      <button class="top-tab" :class="{ active: activeTab === 'before' }" @click="activeTab = 'before'">은퇴 전</button>
      <button class="top-tab" :class="{ active: activeTab === 'after' }"  @click="activeTab = 'after'">은퇴 후</button>
    </div>

    <!-- 타임라인 -->
    <div class="timeline-outer">
      <div class="timeline-track">
        <div
          v-for="(p, i) in tabData.periods"
          :key="'seg' + i"
          class="period-seg"
          :class="{ 'period-seg--active': selIdx === i }"
          :style="{ left: toPercent(p.fromAge) + '%', width: (toPercent(p.toAge) - toPercent(p.fromAge)) + '%' }"
          :title="`${p.fromAge}세 ~ ${p.toAge}세`"
          @click="selectPeriod(i)"
        >
          <span v-if="segWidthPct(p) >= 10" class="seg-label">{{ p.fromAge }}~{{ p.toAge }}</span>
        </div>
        <div
          v-for="(m, i) in tabData.milestones.filter(m => m.committed && m.age)"
          :key="'dot' + i"
          class="milestone-dot"
          :style="{ left: toPercent(Number(m.age)) + '%' }"
        />
      </div>
      <div class="milestone-label-row">
        <span
          v-for="(m, i) in tabData.milestones.filter(m => m.committed && m.age)"
          :key="'ml' + i"
          class="ms-age-tag"
          :style="{ left: toPercent(Number(m.age)) + '%' }"
        >{{ m.age }}세</span>
      </div>
      <div class="timeline-labels">
        <span class="label-edge label-edge--l">{{ timelineStart }}세</span>
        <span v-for="t in ticks" :key="t" class="label-tick" :style="{ left: toPercent(t) + '%' }">{{ t }}</span>
        <span class="label-edge label-edge--r">{{ timelineEnd }}세</span>
      </div>
    </div>

    <!-- 모드 탭 -->
    <div class="mode-row">
      <button class="mode-btn" :class="{ active: curMode === 'periods' }"    @click="curMode = 'periods'">기간별 지출</button>
      <button class="mode-btn" :class="{ active: curMode === 'milestones' }" @click="curMode = 'milestones'">예상 달성자금 / 예상 지출</button>
    </div>

    <!-- 기간별 지출 -->
    <template v-if="curMode === 'periods'">

      <div class="period-input-row">
        <input class="age-input" type="number" v-model="pending.from" :placeholder="String(timelineStart)" />
        <span class="age-sep">세 ~</span>
        <input class="age-input" type="number" v-model="pending.to"   :placeholder="String(timelineEnd)" />
        <span class="age-sep">세</span>
        <button class="confirm-btn" @click="confirmPeriod">확인</button>
      </div>
      <p v-if="periodError" class="period-error">{{ periodError }}</p>

      <template v-if="selIdx !== null && tabData.periods[selIdx]">
        <div class="sub-label-row">
          <span class="sub-label">{{ tabData.periods[selIdx].fromAge }}세 ~ {{ tabData.periods[selIdx].toAge }}세 지출 항목</span>
          <button class="period-del-btn" @click="deletePeriod(selIdx)">× 기간 삭제</button>
        </div>

        <div class="table-wrap">
          <table class="inner-table">
            <colgroup>
              <col /><col style="width:7em" /><col style="width:7em" /><col style="width:28px" />
            </colgroup>
            <thead>
              <tr><th>항목</th><th>금액</th><th>고정 / 변동</th><th></th></tr>
            </thead>
            <tbody>
              <tr v-if="tabData.periods[selIdx].items.length === 0" class="empty-row">
                <td colspan="4">행 추가 버튼으로 항목을 입력하세요</td>
              </tr>
              <tr v-for="(item, j) in tabData.periods[selIdx].items" :key="j">
                <td>
                  <div class="cell-select-wrap">
                    <template v-if="item.name__sel === '기타'">
                      <span class="etc-back" @click="clearItemNameEtc(item)">◂</span>
                      <input class="cell-input cell-input--etc" v-model="item.name" placeholder="직접 입력" />
                    </template>
                    <template v-else>
                      <select class="cell-select" :value="item.name__sel" @change="onItemNameChange(item, $event.target.value)">
                        <option v-for="cat in EXPENSE_CATS" :key="cat" :value="cat">{{ cat }}</option>
                      </select>
                      <span class="cell-select-arrow">▾</span>
                    </template>
                  </div>
                </td>
                <td><input class="cell-input cell-r" v-model="item.amount" placeholder="예) 2,000,000" /></td>
                <td>
                  <div class="toggle-group">
                    <span class="tog" :class="{ on: item.type === '고정' }" @click="item.type = '고정'">고정</span>
                    <span class="tog" :class="{ on: item.type === '변동' }" @click="item.type = '변동'">변동</span>
                  </div>
                </td>
                <td class="td-act"><button class="rm-btn" @click="removePeriodItem(j)">×</button></td>
              </tr>
            </tbody>
          </table>
          <button class="add-btn" @click="addPeriodItem">+ 행 추가</button>
        </div>
      </template>

      <div v-else class="empty-hint">기간을 입력하고 확인을 눌러 지출 항목을 추가하세요</div>

    </template>

    <!-- 예상 달성자금 / 예상 지출 -->
    <template v-else>
      <div class="table-wrap">
        <div class="table-scroll">
          <table class="inner-table">
            <thead>
              <tr>
                <th style="min-width:5em">시점 (세)</th>
                <th style="min-width:5em">구분</th>
                <th style="min-width:8em">내용</th>
                <th style="min-width:6em">금액</th>
                <th style="min-width:6em">비고</th>
                <th style="width:42px"></th>
                <th style="width:28px"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="tabData.milestones.length === 0" class="empty-row">
                <td colspan="7">행 추가 버튼으로 항목을 입력하세요</td>
              </tr>
              <tr v-for="(m, i) in tabData.milestones" :key="i" :class="{ 'row-done': m.committed }">
                <td><input class="cell-input" type="number" v-model="m.age" placeholder="45" :disabled="m.committed" /></td>
                <td>
                  <div class="cell-select-wrap">
                    <select class="cell-select" v-model="m.category" :disabled="m.committed">
                      <option>소득</option>
                      <option>지출</option>
                    </select>
                    <span v-if="!m.committed" class="cell-select-arrow">▾</span>
                  </div>
                </td>
                <td><input class="cell-input" v-model="m.content"  placeholder="예) 주택 구입" :disabled="m.committed" /></td>
                <td><input class="cell-input cell-r" v-model="m.amount" placeholder="예) 300,000,000" :disabled="m.committed" /></td>
                <td><input class="cell-input" v-model="m.note" placeholder="" :disabled="m.committed" /></td>
                <td class="td-act">
                  <button v-if="!m.committed" class="commit-btn"  @click="commitMilestone(i)">입력</button>
                  <button v-else              class="unlock-btn"  @click="unlockMilestone(i)" title="수정">✎</button>
                </td>
                <td class="td-act"><button class="rm-btn" @click="removeMilestone(i)">×</button></td>
              </tr>
            </tbody>
          </table>
        </div>
        <button class="add-btn" @click="addMilestone">+ 행 추가</button>
      </div>
    </template>

  </div>
</template>

<style scoped>
.goals-form { display: flex; flex-direction: column; gap: 10px; }

/* 상단 탭 */
.top-tab-row { display: flex; gap: 4px; }
.top-tab {
  flex: 1; padding: 5px 0; border: 1.5px solid #d0ccc6; border-radius: 4px;
  background: none; font: inherit; font-size: 12px; font-weight: 700;
  color: #9498b5; cursor: pointer; transition: all 0.12s;
}
.top-tab.active { border-color: #5e5ce6; background: #5e5ce6; color: #fff; }

/* 타임라인 */
.timeline-outer { user-select: none; }
.timeline-track { position: relative; height: 28px; background: #eae5de; border-radius: 4px; margin-bottom: 2px; }
.period-seg {
  position: absolute; top: 0; height: 100%;
  background: rgba(94, 92, 230, 0.18); border: 1.5px solid rgba(94, 92, 230, 0.45);
  border-radius: 3px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  overflow: hidden; transition: background 0.12s; box-sizing: border-box;
}
.period-seg:hover { background: rgba(94, 92, 230, 0.3); }
.period-seg--active { background: rgba(94, 92, 230, 0.48); border-color: #5e5ce6; }
.seg-label { font-size: 9px; font-weight: 800; color: #5e5ce6; white-space: nowrap; pointer-events: none; }
.milestone-dot {
  position: absolute; top: 50%; transform: translate(-50%, -50%);
  width: 10px; height: 10px; border-radius: 50%;
  background: #e05c5c; border: 2px solid #fff; box-shadow: 0 0 0 1.5px #e05c5c;
}
.milestone-label-row { position: relative; height: 13px; }
.ms-age-tag { position: absolute; transform: translateX(-50%); font-size: 9px; font-weight: 700; color: #e05c5c; white-space: nowrap; }
.timeline-labels { position: relative; height: 15px; font-size: 10px; color: #9498b5; font-weight: 600; }
.label-edge--l { position: absolute; left: 0; }
.label-edge--r { position: absolute; right: 0; }
.label-tick { position: absolute; transform: translateX(-50%); color: #b0b4cc; font-size: 9px; }

/* 모드 탭 */
.mode-row { display: flex; gap: 6px; }
.mode-btn {
  padding: 3px 11px; border: 1px solid #d0ccc6; border-radius: 12px;
  background: none; font: inherit; font-size: 11px; font-weight: 700;
  color: #9498b5; cursor: pointer; white-space: nowrap; transition: all 0.12s;
}
.mode-btn.active { border-color: #5e5ce6; background: #5e5ce6; color: #fff; }

/* 기간 입력 */
.period-input-row { display: flex; align-items: center; gap: 6px; padding-bottom: 8px; border-bottom: 1px solid #eae5de; }
.period-error { margin-top: -4px; font-size: 11px; color: #d14343; }
.age-input {
  width: 48px; border: none; border-bottom: 1.5px solid #d0ccc6;
  background: transparent; padding: 2px 0 3px; font: inherit;
  font-size: 14px; color: #1a1f3c; outline: none; text-align: center;
  -moz-appearance: textfield; appearance: textfield;
}
.age-input::-webkit-inner-spin-button, .age-input::-webkit-outer-spin-button { -webkit-appearance: none; }
.age-input:focus { border-bottom-color: #5e5ce6; }
.age-sep { font-size: 12px; color: #9498b5; font-weight: 600; }
.confirm-btn {
  margin-left: auto; padding: 3px 12px; border: 1.5px solid #5e5ce6; border-radius: 3px;
  background: none; font: inherit; font-size: 12px; font-weight: 700;
  color: #5e5ce6; cursor: pointer; transition: all 0.12s;
}
.confirm-btn:hover { background: #5e5ce6; color: #fff; }

.sub-label-row { display: flex; align-items: center; justify-content: space-between; }
.sub-label { font-size: 11px; font-weight: 800; color: #5e5ce6; letter-spacing: 0.06em; }
.period-del-btn {
  font-size: 10px; font-weight: 700; color: #c4c0ba;
  background: none; border: 1px solid #d0ccc6; border-radius: 3px;
  padding: 1px 7px; cursor: pointer; transition: all 0.12s;
}
.period-del-btn:hover { border-color: #e05c5c; color: #e05c5c; }
.empty-hint { font-size: 12px; color: #c4c0ba; font-style: italic; text-align: center; padding: 18px 0; }

/* 표 공통 */
.table-wrap { display: flex; flex-direction: column; gap: 8px; }
.table-scroll { overflow-x: auto; }
.inner-table {
  width: 100%; border-collapse: collapse; table-layout: auto; border: 1px solid #d0ccc6;
}
.inner-table thead th {
  font-size: 10px; font-weight: 800; letter-spacing: 0.07em; color: #9498b5;
  text-align: center; padding: 5px 8px; background: #f7f5f2;
  border-bottom: 1.5px solid #5e5ce6; border-right: 1px solid #d0ccc6; white-space: nowrap;
}
.inner-table thead th:last-child { border-right: none; }
.inner-table tbody tr { border-bottom: 1px solid #d0ccc6; }
.inner-table tbody tr:last-child { border-bottom: none; }
.inner-table tbody td { padding: 5px 8px 4px; vertical-align: middle; border-right: 1px solid #eae5de; }
.inner-table tbody td:last-child { border-right: none; }
.empty-row td { text-align: center; font-size: 12px; color: #c4c0ba; font-style: italic; padding: 14px 0; }
.row-done { background: #fafafa; }

.cell-input {
  display: block; width: 1px; min-width: 100%;
  border: none; border-bottom: 1px solid #d0ccc6; background: transparent;
  padding: 2px 0 3px; font: inherit; font-size: 13px; color: #1a1f3c;
  outline: none; border-radius: 0; box-sizing: border-box; transition: border-color 0.15s;
}
.cell-input:focus { border-bottom-color: #5e5ce6; }
.cell-input::placeholder { color: #c4c0ba; font-style: italic; font-size: 11px; }
.cell-input:disabled { color: #9498b5; border-bottom-color: transparent; }
.cell-r { text-align: right; }
.cell-input--etc { font-style: italic; }

/* cell select */
.cell-select-wrap { position: relative; display: flex; align-items: center; border-bottom: 1px solid #d0ccc6; }
.cell-select {
  flex: 1; border: none; background: transparent; appearance: none;
  padding: 2px 16px 3px 0; font: inherit; font-size: 13px; color: #1a1f3c;
  outline: none; cursor: pointer; min-width: 0;
}
.cell-select:disabled { color: #9498b5; cursor: default; border-bottom-color: transparent; }
.cell-select-wrap:focus-within { border-bottom-color: #5e5ce6; }
.cell-select-arrow { position: absolute; right: 2px; font-size: 9px; color: #9498b5; pointer-events: none; }
.etc-back { font-size: 10px; color: #b0b4cc; cursor: pointer; flex-shrink: 0; padding-right: 4px; transition: color 0.12s; }
.etc-back:hover { color: #5e5ce6; }

/* 고정/변동 */
.toggle-group { display: flex; gap: 3px; justify-content: center; }
.tog {
  font-size: 10px; font-weight: 700; padding: 2px 7px;
  border: 1px solid #d0ccc6; border-radius: 2px;
  color: #9498b5; cursor: pointer; user-select: none; transition: all 0.1s;
}
.tog.on { border-color: #5e5ce6; background: #5e5ce6; color: #fff; }

/* 액션 */
.td-act { text-align: center; vertical-align: middle; }
.rm-btn { font-size: 14px; color: #c4c0ba; background: none; border: none; cursor: pointer; padding: 0 2px; transition: color 0.15s; }
.rm-btn:hover { color: #e05c5c; }
.commit-btn {
  font-size: 10px; font-weight: 700; color: #5e5ce6;
  background: none; border: 1px solid #5e5ce6; border-radius: 2px;
  padding: 2px 6px; cursor: pointer; white-space: nowrap; transition: all 0.12s;
}
.commit-btn:hover { background: #5e5ce6; color: #fff; }
.unlock-btn {
  font-size: 12px; color: #9498b5; background: none;
  border: 1px solid #d0ccc6; border-radius: 2px;
  padding: 1px 5px; cursor: pointer; transition: all 0.12s;
}
.unlock-btn:hover { border-color: #5e5ce6; color: #5e5ce6; }
.add-btn {
  align-self: flex-start; font-size: 11px; font-weight: 700;
  color: #5e5ce6; background: none; border: 1px solid #5e5ce6;
  border-radius: 10px; padding: 2px 10px; cursor: pointer;
  letter-spacing: 0.04em; transition: background 0.15s, color 0.15s;
}
.add-btn:hover { background: #5e5ce6; color: #fff; }
</style>
