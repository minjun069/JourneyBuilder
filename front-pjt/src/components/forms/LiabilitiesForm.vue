<script setup>
import { reactive } from 'vue'
import AddableTable from '@/components/ui/AddableTable.vue'

const COLUMNS = [
  { key: 'type',       label: '구분',      minWidth: '8em',  type: 'select', options: ['신용대출', '임대보증금', '담보대출', '학자금대출', '기타'] },
  { key: 'name',       label: '대출 상품명', minWidth: '13em', placeholder: '모두은행 청년신용대출' },
  { key: 'value',      label: '대출잔액',   minWidth: '5em',  placeholder: '100,000,000',    unit: '원' },
  { key: 'rate',       label: '대출금리',   minWidth: '4em',  placeholder: '5',      unit: '%' },
  { key: 'rateType',   label: '금리유형',   minWidth: '7em',  type: 'toggle', options: ['고정', '변동'] },
  { key: 'baseRate',   label: '기준금리',   minWidth: '6em',  placeholder: 'COFIX', disabledWhen: { key: 'rateType', value: '고정' } },
  { key: 'repayment',  label: '상환방식',   minWidth: '9em',  type: 'select', options: ['만기일시', '원리금분할상환', '원금분할상환', '기타'] },
  { key: 'loan',       label: '대출일시',   minWidth: '10em', type: 'date' },
  { key: 'maturity',   label: '만기일시',   minWidth: '10em', type: 'date' },
  { key: 'grace',      label: '거치기간',   minWidth: '5em',  placeholder: '1',      unit: '년' },
]

const form = reactive({ assets: [] })

defineExpose({ form })
</script>

<template>
  <AddableTable v-model="form.assets" :columns="COLUMNS" />
</template>
