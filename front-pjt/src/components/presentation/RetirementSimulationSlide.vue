<script setup>
import RetirementSimulationPanel from '@/components/simulation/RetirementSimulationPanel.vue'
defineProps({ preview: { type: Boolean, default: false } })
</script>

<template>
  <div class="slide">
    <RetirementSimulationPanel :compact="!preview" :read-only="preview" />
  </div>
</template>

<style scoped>
.slide { width: 100%; height: 100%; padding: 10px 14px; box-sizing: border-box; overflow: auto; background: #fff; }
</style>
