<script setup>
import DashboardReportShell from '@/components/dashboard/DashboardReportShell.vue'
import BalanceSheetSlide from '@/components/presentation/BalanceSheetSlide.vue'
</script>

<template>
  <DashboardReportShell title="자산 및 부채" report-key="asset_statement">
    <BalanceSheetSlide />
  </DashboardReportShell>
</template>
