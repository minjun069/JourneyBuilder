<script setup>
import DashboardReportShell from '@/components/dashboard/DashboardReportShell.vue'
import LifetimeCashflowSlide from '@/components/presentation/LifetimeCashflowSlide.vue'
</script>

<template>
  <DashboardReportShell title="생애현금흐름 및 생애순자산" report-key="lifetime-cashflow">
    <LifetimeCashflowSlide />
  </DashboardReportShell>
</template>
