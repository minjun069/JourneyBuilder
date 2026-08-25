<script setup>
import DashboardReportShell from '@/components/dashboard/DashboardReportShell.vue'
import CashFlowSlide from '@/components/presentation/CashFlowSlide.vue'
</script>

<template>
  <DashboardReportShell title="현금흐름" report-key="cash_flow">
    <CashFlowSlide />
  </DashboardReportShell>
</template>
