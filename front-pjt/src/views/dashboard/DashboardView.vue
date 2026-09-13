<template>
  <div class="dashboard-layout">
    <DashboardSidebar />
    <main class="dashboard-content">
      <RouterView />
    </main>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import DashboardSidebar from '@/components/dashboard/DashboardSidebar.vue'
import { useReportStore } from '@/stores/report'

const reportStore = useReportStore()

onMounted(() => {
  void reportStore.loadLatestPresentation()
})
</script>

<style scoped>
.dashboard-layout {
  display: flex;
  min-height: calc(100vh - 80px);
  max-width: 1440px;
  margin: 0 auto;
  background-color: #f7f8fc;
}

.dashboard-content {
  flex: 1;
  overflow-y: auto;
  padding: 28px 32px;
}
</style>
