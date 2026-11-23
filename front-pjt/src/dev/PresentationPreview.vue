<script setup>
import { onMounted, onUpdated, ref, watch } from 'vue'
import { useReportStore } from '@/stores/report'

const props = defineProps({
  scenario: { type: Object, default: null },
  slide: { type: Object, required: true },
  resetTick: { type: Number, default: 0 },
  restoreStoreContext: { type: Function, required: true },
})
const reportStore = useReportStore()
const error = ref('')

watch(() => props.scenario, (scenario) => {
  error.value = ''
  if (!scenario) {
    reportStore.clearReports()
    props.restoreStoreContext()
    return
  }
  try {
    reportStore.loadDevelopmentPresentation(scenario)
  } catch (cause) {
    reportStore.clearReports()
    error.value = `계산 데이터를 표시할 수 없습니다: ${cause.message}`
  } finally {
    props.restoreStoreContext()
  }
}, { immediate: true })
// Pinia's non-component fallback is global across Vue applications. Restore it
// after child setup/actions so router guards keep using the real application.
onMounted(props.restoreStoreContext)
onUpdated(props.restoreStoreContext)
</script>

<template>
  <p v-if="error" role="alert">{{ error }}</p>
  <component
    :is="slide"
    v-else-if="scenario"
    :key="`${scenario.id}-${resetTick}`"
    preview
  />
  <p v-else>계산 데이터를 불러오고 있습니다.</p>
</template>
