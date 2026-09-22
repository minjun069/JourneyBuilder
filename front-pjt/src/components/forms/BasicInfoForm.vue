<script setup>
import { reactive } from 'vue'

const JOBS = ['직장인', '공무원', '자영업자', '프리랜서', '전업주부', '학생', '퇴직자', '기타']
const NEEDS = ['은퇴', '투자', '주택자금', '교육자금', '보장', '기타']

const TENDENCY_CATS = [
  { label: '위험 성향', items: ['안정 추구', '균형 추구', '위험 감수'] },
  { label: '투자 기간', items: ['단기 지향', '장기 지향'] },
  { label: '우선순위',  items: ['수익 중시', '유동성 중시'] },
]

const form = reactive({
  age: '',
  job: '',
  marital: '',
  dependents: { children: [], parents: [], others: [] },
  tendency: [],
  interest: '',
  need: '',
  earnings: 0,
  retirementAge: '',
  endAge: '',
})

function toggleTendency(val) {
  const idx = form.tendency.indexOf(val)
  if (idx === -1) form.tendency.push(val)
  else form.tendency.splice(idx, 1)
}

function addChild()   { form.dependents.children.push({ age: '' }) }
function removeChild(i) { form.dependents.children.splice(i, 1) }
function addParent()  { form.dependents.parents.push({ age: '' }) }
function removeParent(i) { form.dependents.parents.splice(i, 1) }
function addOther()   { form.dependents.others.push({ name: '', age: '' }) }
function removeOther(i) { form.dependents.others.splice(i, 1) }

defineExpose({ form })
</script>

<template>
  <div class="doc-fields">

    <div class="field-row">
      <span class="field-label">나이</span>
      <input class="field-input" type="number" v-model="form.age" placeholder="35" />
      <span class="field-unit">세</span>
    </div>

    <div class="field-row">
      <span class="field-label">직업</span>
      <div class="select-wrap">
        <select class="field-select" v-model="form.job">
          <option value="" disabled>선택</option>
          <option v-for="j in JOBS" :key="j" :value="j">{{ j }}</option>
        </select>
        <span class="select-arrow">▾</span>
      </div>
    </div>

    <div class="field-row">
      <span class="field-label">소득</span>
      <span class="field-unit">월 세후</span>
      <input class="field-input" type="number" v-model="form.earnings" min="0" placeholder="3,900,000" />
      <span class="field-unit">원</span>
    </div>

    <div class="field-row">
      <span class="field-label">기혼 여부</span>
      <div class="radio-group">
        <label class="radio-btn" :class="{ active: form.marital === '미혼' }">
          <input type="radio" v-model="form.marital" value="미혼" /><span>미혼</span>
        </label>
        <label class="radio-btn" :class="{ active: form.marital === '기혼' }">
          <input type="radio" v-model="form.marital" value="기혼" /><span>기혼</span>
        </label>
      </div>
    </div>

    <div class="field-row dep-row">
      <span class="field-label">부양가족</span>
      <div class="dep-groups">

        <div class="dep-group">
          <div class="dep-group-header">
            <span class="dep-group-label">자녀</span>
            <button class="add-btn" type="button" @click="addChild">+ 추가</button>
          </div>
          <div v-for="(child, i) in form.dependents.children" :key="'c'+i" class="dep-item">
            <span class="dep-idx">{{ i + 1 }}</span>
            <span class="dep-sub-label">나이</span>
            <input class="dep-input dep-input--age" v-model="child.age" placeholder="예) 7세" />
            <button class="remove-btn" type="button" @click="removeChild(i)">×</button>
          </div>
        </div>

        <div class="dep-group">
          <div class="dep-group-header">
            <span class="dep-group-label">부모님</span>
            <button class="add-btn" type="button" @click="addParent">+ 추가</button>
          </div>
          <div v-for="(parent, i) in form.dependents.parents" :key="'p'+i" class="dep-item">
            <span class="dep-idx">{{ i + 1 }}</span>
            <span class="dep-sub-label">나이</span>
            <input class="dep-input dep-input--age" v-model="parent.age" placeholder="예) 65세" />
            <button class="remove-btn" type="button" @click="removeParent(i)">×</button>
          </div>
        </div>

        <div class="dep-group dep-group--last">
          <div class="dep-group-header">
            <span class="dep-group-label">그 외</span>
            <button class="add-btn" type="button" @click="addOther">+ 추가</button>
          </div>
          <div v-for="(other, i) in form.dependents.others" :key="'o'+i" class="dep-item">
            <span class="dep-idx">{{ i + 1 }}</span>
            <span class="dep-sub-label">항목명</span>
            <input class="dep-input dep-input--name" v-model="other.name" placeholder="예) 배우자" />
            <span class="dep-sub-label dep-sub-label--mid">나이</span>
            <input class="dep-input dep-input--age" v-model="other.age" placeholder="예) 40세" />
            <button class="remove-btn" type="button" @click="removeOther(i)">×</button>
          </div>
        </div>

      </div>
    </div>

    <div
      v-for="cat in TENDENCY_CATS"
      :key="cat.label"
      class="field-row"
    >
      <span class="field-label">{{ cat.label }}</span>
      <div class="chip-group">
        <span
          v-for="t in cat.items"
          :key="t"
          class="chip"
          :class="{ active: form.tendency.includes(t) }"
          @click="toggleTendency(t)"
        >{{ t }}</span>
      </div>
    </div>

    <div class="field-row">
      <span class="field-label">관심사</span>
      <input class="field-input" v-model="form.interest" placeholder="시간과 돈을 가장 많이 소비하는 대상" />
    </div>

    <div class="field-row">
      <span class="field-label">니즈</span>
      <div class="select-wrap">
        <select class="field-select" v-model="form.need">
          <option value="" disabled>선택</option>
          <option v-for="n in NEEDS" :key="n" :value="n">{{ n }}</option>
        </select>
        <span class="select-arrow">▾</span>
      </div>
    </div>

    <div class="field-row">
      <span class="field-label">예상 은퇴 나이</span>
      <input class="field-input" type="number" v-model="form.retirementAge" placeholder="60" />
      <span class="field-unit">세</span>
    </div>

    <div class="field-row">
      <span class="field-label">예상 사망 나이</span>
      <input class="field-input" type="number" v-model="form.endAge" placeholder="90" />
      <span class="field-unit">세</span>
    </div>

  </div>
</template>

<style scoped>
.doc-fields { display: flex; flex-direction: column; }

.field-row {
  display: flex;
  align-items: baseline;
  gap: 14px;
  padding: 11px 0 7px;
  border-bottom: 1px solid #eae5de;
}
.field-row:last-child { border-bottom: none; }

.field-label {
  width: 90px;
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: #5565df;
  line-height: 1.5;
}

.field-unit { font-size: 11px; color: #9498b5; font-weight: 600; white-space: nowrap; }

.field-input {
  flex: 1;
  border: none;
  border-bottom: 1.5px solid #d0ccc6;
  background: transparent;
  padding: 1px 0 4px;
  font: inherit;
  font-size: 15px;
  color: #1a1f3c;
  outline: none;
  border-radius: 0;
  transition: border-color 0.15s;
  width: 100%;
  box-sizing: border-box;
}
.field-input:focus { border-bottom-color: #5e5ce6; }
.field-input::placeholder { color: #c4c0ba; font-style: italic; font-size: 13px; }
input[type='number'] { -moz-appearance: textfield; appearance: textfield; }
input[type='number']::-webkit-inner-spin-button,
input[type='number']::-webkit-outer-spin-button { -webkit-appearance: none; }

/* Radio */
.radio-group { display: flex; gap: 6px; flex-wrap: wrap; }
.radio-btn { cursor: pointer; }
.radio-btn input { display: none; }
.radio-btn span {
  display: inline-flex; align-items: center; justify-content: center;
  padding: 3px 12px; border: 1.5px solid #d0ccc6; border-radius: 3px;
  font-size: 13px; font-weight: 600; color: #9498b5; transition: all 0.12s; white-space: nowrap;
}
.radio-btn.active span, .radio-btn input:checked + span {
  border-color: #5e5ce6; background: #5e5ce6; color: #fff;
}

/* Select */
.select-wrap {
  flex: 1; position: relative; display: flex; align-items: center;
  border-bottom: 1.5px solid #d0ccc6;
}
.field-select {
  flex: 1; border: none; background: transparent; appearance: none;
  padding: 1px 20px 4px 0; font: inherit; font-size: 15px;
  color: #1a1f3c; outline: none; cursor: pointer;
}
.select-wrap:focus-within { border-bottom-color: #5e5ce6; }
.select-arrow { position: absolute; right: 2px; font-size: 10px; color: #9498b5; pointer-events: none; }

/* 부양가족 */
.dep-row { align-items: flex-start; }
.dep-groups { flex: 1; display: flex; flex-direction: column; gap: 0; }
.dep-group { padding: 5px 0 4px; border-bottom: 1px dashed #eae5de; }
.dep-group--last { border-bottom: none; }
.dep-group-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 3px; }
.dep-group-label { font-size: 11px; font-weight: 700; color: #9498b5; letter-spacing: 0.06em; }
.add-btn {
  font-size: 10px; font-weight: 700; color: #5e5ce6;
  background: none; border: 1px solid #5e5ce6; border-radius: 10px;
  padding: 1px 7px; cursor: pointer; letter-spacing: 0.04em; transition: background 0.15s, color 0.15s;
}
.add-btn:hover { background: #5e5ce6; color: #fff; }
.dep-item { display: flex; align-items: baseline; gap: 5px; padding: 4px 0 2px 2px; }
.dep-idx { font-size: 10px; color: #c4c0ba; width: 12px; flex-shrink: 0; text-align: right; }
.dep-sub-label { font-size: 10px; font-weight: 700; color: #b0b4cc; flex-shrink: 0; }
.dep-sub-label--mid { margin-left: 6px; }
.dep-input {
  border: none; border-bottom: 1px solid #d0ccc6; background: transparent;
  padding: 1px 0 2px; font: inherit; font-size: 13px; color: #1a1f3c;
  outline: none; border-radius: 0; transition: border-color 0.15s;
}
.dep-input:focus { border-bottom-color: #5e5ce6; }
.dep-input::placeholder { color: #c4c0ba; font-style: italic; font-size: 11px; }
.dep-input--age { width: 56px; }
.dep-input--name { flex: 1; }
.remove-btn {
  font-size: 13px; line-height: 1; color: #c4c0ba;
  background: none; border: none; cursor: pointer; padding: 0 2px;
  margin-left: auto; transition: color 0.15s;
}
.remove-btn:hover { color: #e05c5c; }

/* 성향 칩 */
.chip-group { flex: 1; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.chip {
  display: inline-flex; align-items: center; justify-content: center;
  padding: 3px 11px; border: 1.5px solid #d0ccc6; border-radius: 3px;
  font-size: 12px; font-weight: 600; color: #9498b5; cursor: pointer;
  transition: all 0.12s; white-space: nowrap; user-select: none;
}
.chip:hover { border-color: #5e5ce6; color: #5e5ce6; }
.chip.active { border-color: #5e5ce6; background: #5e5ce6; color: #fff; }
</style>
