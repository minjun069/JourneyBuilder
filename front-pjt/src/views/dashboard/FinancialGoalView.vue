<script setup>
import DashboardReportShell from '@/components/dashboard/DashboardReportShell.vue'
import FinancialGoalSlide from '@/components/presentation/FinancialGoalSlide.vue'
</script>

<template>
  <DashboardReportShell title="재무 목표" report-key="financial-goal-result">
    <FinancialGoalSlide />
  </DashboardReportShell>
</template>
