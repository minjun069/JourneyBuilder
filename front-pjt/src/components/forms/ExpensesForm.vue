<script setup>
import { reactive } from 'vue'
import AddableTable from '@/components/ui/AddableTable.vue'

const EXPENSE_CATS = ['주거비', '식비/생활비', '교통비', '통신비', '보험료', '교육비', '의료비', '여가/문화', '외식', '쇼핑', '대출상환', '기타']

const COLUMNS = [
  { key: 'name',      label: '항목',     minWidth: '9em',  type: 'select', options: EXPENSE_CATS },
  { key: 'value',     label: '지출 금액', minWidth: '8em',  placeholder: '500,000', unit: '원' },
  { key: 'fixed',     label: '고정/변동', minWidth: '7em',  type: 'toggle', options: ['고정', '변동'] },
  { key: 'frequency', label: '주기',      minWidth: '13em', type: 'frequency' },
]

const form = reactive({ assets: [] })

defineExpose({ form })
</script>

<template>
  <AddableTable v-model="form.assets" :columns="COLUMNS" />
</template>
