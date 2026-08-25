<script setup>
import { reactive } from 'vue'
import AddableTable from '@/components/ui/AddableTable.vue'

const COLUMNS = [
  { key: 'type',     label: '구분', minWidth: '5em', type: 'select', options: ['근로소득', '사업소득', '이자소득', '배당소득', '연금소득', '기타']},
  { key: 'name',       label: '내용', minWidth: '13em', placeholder: '급여 / 이자수익 / 월세 수입 ...' },
  { key: 'amount',    label: '소득 금액',  minWidth: '10em', placeholder: '500만', unit: '원'},
  { key: 'fixed',     label: '고정/변동',  type: 'toggle', options: ['고정', '변동'] },
]

const form = reactive({ assets: [] })

defineExpose({ form })
</script>

<template>
  <AddableTable v-model="form.assets" :columns="COLUMNS" />
</template>
