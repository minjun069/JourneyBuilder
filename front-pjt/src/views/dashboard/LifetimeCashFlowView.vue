<script setup>
import DashboardReportShell from '@/components/dashboard/DashboardReportShell.vue'
import LifetimeCashflowSlide from '@/components/presentation/LifetimeCashflowSlide.vue'
</script>

<template>
  <DashboardReportShell title="생애현금흐름 및 생애순자산" report-key="lifetime-cashflow">
    <LifetimeCashflowSlide />
  </DashboardReportShell>
</template>

<style scoped>
/* LifetimeCashflowSlide는 루트가 height:100%(차트 막대가 % 높이)라서
   확정 높이가 없는 report-panel에서는 차트가 0 높이로 붕괴한다. */
:deep(.report-panel) {
  height: 560px;
}
</style>
