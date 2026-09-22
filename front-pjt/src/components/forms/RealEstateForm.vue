<script setup>
import { reactive } from 'vue'
import AddableTable from '@/components/ui/AddableTable.vue'

const COLUMNS = [
  { key: 'type',     label: '구분', minWidth: '5em', type: 'select', options: ['주택/아파트', '오피스텔', '빌라', '상가', '기타']},
  { key: 'name',     label: '이름',  minWidth: '15em', placeholder: '강남 푸르지오 26평형' },
  { key: 'value',    label: '평가금액',  minWidth: '6em', placeholder: '300,000,000',  unit: '원'},
  { key: 'purchase',    label: '취득가액',  minWidth: '6em', placeholder: '200,000,000',  unit: '원'},
  { key: 'date',    label: '취득시기',  minWidth: '10em', type: 'date' },
  { key: 'monthly',  label: '월세소득',  minWidth: '4em', placeholder: '300,000',  unit: '원'},
  { key: 'deposit',   label: '임대보증금',  minWidth: '8em', placeholder: '5,000,000',  unit: '원'},
  { key: 'loan',  label: '담보대출금',  minWidth: '8em', placeholder: '50,000,000' ,  unit: '원'}
]

const form = reactive({ assets: [] })

defineExpose({ form })
</script>

<template>
  <AddableTable v-model="form.assets" :columns="COLUMNS" />
</template>
