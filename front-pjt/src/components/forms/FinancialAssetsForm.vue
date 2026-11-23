<script setup>
import { reactive } from 'vue'
import AddableTable from '@/components/ui/AddableTable.vue'

const COLUMNS = [
  { key: 'assetType', label: '자산 유형',      minWidth: '8em',  type: 'select', options: ['예금/적금', '주식', '펀드/ETF', '채권', '가상자산', '기타'] },
  { key: 'name',      label: '상품명',          minWidth: '8em',  placeholder: 'KB 적금' },
  { key: 'principal', label: '원금',            minWidth: '6em',  placeholder: '5,000,000', unit: '원', align: 'right' },
  { key: 'value',     label: '현재 평가금액',    minWidth: '8em',  placeholder: '5,200,000', unit: '원', align: 'right' },
  { key: 'monthly',   label: '월 적립액 (선택)', minWidth: '8em',  placeholder: '300,000',  unit: '원', align: 'right' },
  { key: 'purpose',   label: '목적',            minWidth: '6em',  type: 'select', options: ['자산증식', '목돈마련', '비상자금', '자녀교육', '노후준비', '기타'] },
]

const form = reactive({ assets: [] })

defineExpose({ form })
</script>

<template>
  <AddableTable v-model="form.assets" :columns="COLUMNS" />
</template>
