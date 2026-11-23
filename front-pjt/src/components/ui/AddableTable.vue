<script setup>
import { createSecureUuid } from '@/utils/secureUuid'
import { toCanonicalMoney } from '@/utils/canonicalMoney'

const props = defineProps({
  modelValue: { type: Array, required: true },
  columns: { type: Array, required: true },
  addLabel: { type: String, default: '+ 행 추가' },
})

const emit = defineEmits(['update:modelValue'])

function addRow() {
  const blank = { clientItemId: createSecureUuid() }
  props.columns.forEach(col => {
    if (col.type === 'frequency') {
      blank[col.key] = { none: true, unit: '월', count: '' }
    } else if (col.type === 'toggle') {
      blank[col.key] = col.options?.[0] ?? ''
    } else if (col.type === 'select') {
      const first = col.options?.[0] ?? ''
      blank[col.key] = first
      blank[col.key + '__sel'] = first
    } else {
      blank[col.key] = ''
    }
  })
  emit('update:modelValue', [...props.modelValue, blank])
}

function onSelectChange(row, col, val) {
  row[col.key + '__sel'] = val
  row[col.key] = val === '기타' ? '' : val
}

function clearEtc(row, col) {
  row[col.key + '__sel'] = ''
  row[col.key] = ''
}

function removeRow(i) {
  const next = [...props.modelValue]
  next.splice(i, 1)
  emit('update:modelValue', next)
}

function isDisabled(row, col) {
  return !!(col.disabledWhen && row[col.disabledWhen.key] === col.disabledWhen.value)
}

function toggleFreqNone(row, col) {
  const f = row[col.key]
  if (f) f.none = !f.none
}

// 원 단위 칸은 저장될 금액을 바로 읽어 준다. 10,000배 오입력을 입력 시점에 드러내는 것이 목적이다.
function moneyPreview(row, col) {
  if (col.unit !== '원' || isDisabled(row, col)) return ''
  const raw = row[col.key]
  if (raw === '' || raw == null) return ''
  const won = toCanonicalMoney(raw)
  if (won == null || !Number.isFinite(won)) return ''
  return `${won.toLocaleString('ko-KR')}원`
}

// 표의 기본 열 너비는 유지하되 긴 입력값은 잘리지 않도록 실제 글자 수만큼 열을 넓힌다.
function inputWidth(value) {
  const text = String(value ?? '')
  const units = [...text].reduce((width, character) => (
    width + ((character.codePointAt(0) ?? 0) > 255 ? 1.8 : 1)
  ), 0)
  return `${Math.max(4, Math.ceil(units) + 2)}ch`
}
</script>

<template>
  <div class="table-wrap">
    <div class="table-scroll">
      <table class="addable-table">
        <colgroup>
          <col v-for="col in columns" :key="col.key" :style="col.width ? { width: col.width } : {}" />
          <col style="width: 28px" />
        </colgroup>
        <thead>
          <tr>
            <th v-for="col in columns" :key="col.key" :style="col.minWidth ? { minWidth: col.minWidth } : {}">{{ col.label }}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="modelValue.length === 0" class="empty-row">
            <td :colspan="columns.length + 1">아래 버튼을 눌러 항목을 추가하세요</td>
          </tr>
          <tr v-for="(row, i) in modelValue" :key="i">
            <td v-for="col in columns" :key="col.key">

              <div v-if="col.type === 'toggle'" class="toggle-group">
                <span
                  v-for="opt in col.options"
                  :key="opt"
                  class="tog"
                  :class="{ on: row[col.key] === opt }"
                  @click="row[col.key] = opt"
                >{{ opt }}</span>
              </div>

              <div v-else-if="col.type === 'select'" class="cell-select-wrap">
                <template v-if="row[col.key + '__sel'] === '기타'">
                  <span class="etc-back" title="되돌아가기" @click="clearEtc(row, col)">◂</span>
                  <input
                    class="cell-input cell-input--etc"
                    v-model="row[col.key]"
                    placeholder="직접 입력"
                    :style="{ '--input-width': inputWidth(row[col.key]) }"
                  />
                </template>
                <template v-else>
                  <select
                    class="cell-select"
                    :value="row[col.key + '__sel']"
                    @change="onSelectChange(row, col, $event.target.value)"
                  >
                    <option v-for="opt in col.options" :key="opt" :value="opt">{{ opt }}</option>
                  </select>
                  <span class="cell-select-arrow">▾</span>
                </template>
              </div>

              <div v-else-if="col.type === 'date'" class="cell-date">
                <input class="cell-input--date" type="date" v-model="row[col.key]" />
              </div>

              <div v-else-if="col.type === 'frequency'" class="freq-wrap">
                <button
                  class="freq-none-btn"
                  :class="{ active: row[col.key] && row[col.key].none }"
                  type="button"
                  @click="toggleFreqNone(row, col)"
                >해당없음</button>
                <template v-if="row[col.key] && !row[col.key].none">
                  <select class="freq-unit-sel" v-model="row[col.key].unit">
                    <option>연</option>
                    <option>월</option>
                    <option>주</option>
                  </select>
                  <input class="freq-count-input" type="number" v-model="row[col.key].count" min="1" />
                  <span class="freq-label">회</span>
                </template>
              </div>

              <div v-else class="cell-wrap">
                <div class="cell-field">
                  <input
                    class="cell-input"
                    :class="{
                      'cell-input--right': col.align === 'right',
                      'cell-input--disabled': isDisabled(row, col),
                    }"
                    v-model="row[col.key]"
                    :placeholder="isDisabled(row, col) ? '—' : (col.placeholder ?? '')"
                    :disabled="isDisabled(row, col)"
                    :style="{ '--input-width': inputWidth(row[col.key]) }"
                  />
                  <span v-if="col.unit" class="cell-unit">{{ col.unit }}</span>
                </div>
                <span v-if="moneyPreview(row, col)" class="cell-preview">{{ moneyPreview(row, col) }}</span>
              </div>

            </td>
            <td class="td-action">
              <button class="remove-btn" type="button" @click="removeRow(i)">×</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <button class="add-btn" type="button" @click="addRow">{{ addLabel }}</button>
  </div>
</template>

<style scoped>
.table-wrap { display: flex; flex-direction: column; gap: 12px; }
.table-scroll {
  overflow-x: auto;
  border: 1px solid #e1e4ec;
  border-radius: 16px;
  background: #ffffff;
  box-shadow: 0 8px 24px rgba(42, 49, 78, 0.06);
}

.addable-table {
  width: 100%;
  min-width: max-content;
  border-collapse: separate;
  border-spacing: 0;
  table-layout: auto;
}

thead th {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: #777d91;
  text-align: left;
  padding: 16px 14px 12px;
  background: #ffffff;
  border-bottom: 2px solid #3d4358;
  white-space: nowrap;
}

tbody tr:last-child td { border-bottom: none; }

tbody td {
  padding: 13px 14px;
  vertical-align: top;
  border-bottom: 1px solid #e5e7ed;
  background: #ffffff;
}

.empty-row td {
  text-align: center;
  font-size: 14px;
  color: #a0a5b5;
  padding: 24px 14px;
}

/* ── 기본 text 입력 ── */
.cell-wrap { min-width: 100%; }

.cell-field { display: flex; align-items: baseline; gap: 5px; width: max-content; min-width: 100%; }

.cell-preview {
  display: block; margin-top: 4px; font-size: 11px; color: #8c92a6;
  text-align: right; white-space: nowrap;
}

.cell-unit { font-size: 13px; font-weight: 600; color: #8c92a6; white-space: nowrap; flex-shrink: 0; }

.cell-input {
  flex: 0 0 auto;
  width: var(--input-width, 100%);
  min-width: 100%;
  border: none;
  border-bottom: 1px solid #d7dae3;
  background: transparent;
  padding: 4px 0 5px;
  font: inherit;
  font-size: 15px;
  color: #1a1f3c;
  outline: none;
  border-radius: 0;
  box-sizing: border-box;
  transition: border-color 0.15s;
}
.cell-input:focus { border-bottom-color: #5e5ce6; }
.cell-input::placeholder { color: #b5b9c6; font-size: 13px; }
.cell-input--right { text-align: right; }
.cell-input--etc { font-style: italic; }
.cell-input--disabled { color: #c4c0ba; border-bottom-color: #eae5de; cursor: not-allowed; }

/* ── toggle ── */
.toggle-group { display: flex; gap: 6px; justify-content: flex-start; }
.tog {
  font-size: 12px; font-weight: 700; padding: 5px 10px;
  border: 1px solid #d7dae3; border-radius: 7px;
  color: #9498b5; cursor: pointer; user-select: none;
  white-space: nowrap; transition: all 0.1s;
}
.tog.on { border-color: #5e5ce6; background: #5e5ce6; color: #fff; }

/* ── select ── */
.cell-select-wrap {
  position: relative; display: flex; align-items: center;
  border-bottom: 1px solid #d0ccc6;
}
.cell-select {
  flex: 1; border: none; background: transparent; appearance: none;
  padding: 4px 20px 5px 0; font: inherit; font-size: 15px;
  color: #1a1f3c; outline: none; cursor: pointer; min-width: 0;
}
.cell-select-wrap:focus-within { border-bottom-color: #5e5ce6; }
.cell-select-arrow { position: absolute; right: 2px; font-size: 11px; color: #9498b5; pointer-events: none; }
.etc-back { font-size: 12px; color: #b0b4cc; cursor: pointer; flex-shrink: 0; padding-right: 5px; transition: color 0.12s; }
.etc-back:hover { color: #5e5ce6; }

/* ── date ── */
.cell-date { display: block; }
.cell-input--date {
  display: block;
  width: 100%;
  border: none;
  border-bottom: 1px solid #d0ccc6;
  background: transparent;
  padding: 4px 0 5px;
  font: inherit;
  font-size: 14px;
  color: #1a1f3c;
  outline: none;
  border-radius: 0;
  box-sizing: border-box;
  transition: border-color 0.15s;
  cursor: pointer;
}
.cell-input--date:focus { border-bottom-color: #5e5ce6; }

/* ── frequency ── */
.freq-wrap { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
.freq-none-btn {
  font-size: 12px; font-weight: 700;
  padding: 5px 9px; border-radius: 7px;
  border: 1px solid #d0ccc6; color: #9498b5;
  background: none; cursor: pointer; white-space: nowrap;
  flex-shrink: 0; transition: all 0.1s;
}
.freq-none-btn.active { border-color: #5e5ce6; background: #5e5ce6; color: #fff; }
.freq-unit-sel {
  border: none; border-bottom: 1px solid #d0ccc6;
  background: transparent; appearance: none;
  font: inherit; font-size: 14px; color: #1a1f3c;
  outline: none; cursor: pointer; width: 2.2em; text-align: center;
}
.freq-count-input {
  width: 2.5em; border: none; border-bottom: 1px solid #d0ccc6;
  background: transparent; font: inherit; font-size: 14px;
  color: #1a1f3c; outline: none; text-align: center;
  -moz-appearance: textfield; appearance: textfield;
}
.freq-count-input::-webkit-inner-spin-button,
.freq-count-input::-webkit-outer-spin-button { -webkit-appearance: none; }
.freq-label { font-size: 13px; color: #9498b5; font-weight: 600; }

/* ── action ── */
.td-action { text-align: center; vertical-align: middle; }
.remove-btn {
  font-size: 18px; line-height: 1; color: #b6bac7;
  background: none; border: none; cursor: pointer;
  padding: 0 2px; transition: color 0.15s;
}
.remove-btn:hover { color: #e05c5c; }

.add-btn {
  align-self: flex-start; font-size: 13px; font-weight: 700;
  color: #5055bd; background: #f3f3ff; border: 1px solid #cfd1f7;
  border-radius: 9px; padding: 7px 13px; cursor: pointer;
  letter-spacing: 0.04em; transition: background 0.15s, color 0.15s;
}
.add-btn:hover { background: #5e5ce6; color: #fff; }
</style>
