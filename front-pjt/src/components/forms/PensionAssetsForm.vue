<script setup>
import { reactive } from 'vue'
import AddableTable from '@/components/ui/AddableTable.vue'

const COLUMNS = [
  { key: 'type',          label: '구분',         minWidth: '8em',  type: 'select', options: ['연금저축', '연금보험', '저축보험', '퇴직연금(IRP)', '국민연금', '기타'] },
  { key: 'name',          label: '상품명',        minWidth: '8em',  placeholder: 'KB 연금저축' },
  { key: 'value',         label: '평가금액',      minWidth: '5em',  placeholder: '5,200,000', unit: '원', align: 'right' },
  { key: 'monthly',       label: '월 적립액',     minWidth: '5em',  placeholder: '300,000',  unit: '원', align: 'right' },
  { key: 'rate',          label: '기대수익률',    minWidth: '5em',  placeholder: '5',     unit: '%',  align: 'right' },
  { key: 'date',          label: '가입일',        minWidth: '10em', type: 'date' },
  { key: 'paymentPeriod', label: '납입기간',      minWidth: '4em',  placeholder: '20',    unit: '년' },
  { key: 'receiveMethod', label: '수령방식',      minWidth: '5em',  type: 'select', options: ['연금형', '일시금'] },
  { key: 'age',           label: '개시 연령',     minWidth: '4em',  placeholder: '65',    unit: '세' },
  { key: 'pensionPeriod', label: '연금 기간',     minWidth: '7em',  type: 'select', options: ['10년', '15년', '20년', '25년', '30년', '종신', '기타'] },
  { key: 'expectedAmount',label: '예상 수령액',   minWidth: '6em',  placeholder: '1,000,000', unit: '원/월', align: 'right' },
]

const form = reactive({ assets: [] })

defineExpose({ form })
</script>

<template>
  <AddableTable v-model="form.assets" :columns="COLUMNS" />
</template>
