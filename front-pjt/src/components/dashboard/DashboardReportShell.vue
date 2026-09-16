<script setup>
import { onMounted } from 'vue'
import { useReportStore } from '@/stores/report'

defineProps({
  title: { type: String, required: true },
  reportKey: { type: String, required: true },
})

const reportStore = useReportStore()

onMounted(() => {
  reportStore.loadLatestReports()
})
</script>

<template>
  <div class="report-view">
    <div class="page-header">
      <h1 class="page-title">{{ title }}</h1>
    </div>

    <div v-if="reportStore.loading" class="state-card">보고서를 불러오는 중입니다.</div>
    <div v-else-if="reportStore.error" class="state-card error">보고서를 불러오지 못했습니다.</div>
    <div v-else-if="!reportStore.hasReport(reportKey)" class="state-card">
      AI 상담을 완료하면 생성된 보고서를 여기에서 확인할 수 있습니다.
    </div>
    <div v-else class="report-panel">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.report-view {
  display: flex;
  flex-direction: column;
  gap: 18px;
  min-height: 620px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.page-title {
  font-size: 26px;
  font-weight: 800;
  letter-spacing: -0.4px;
  color: #1a1a2e;
  margin: 0;
}

.report-panel {
  min-height: 560px;
  border: 1px solid #e9eaf2;
  border-radius: 12px;
  overflow: hidden;
  background: #fff;
  box-shadow: 0 1px 3px rgba(20, 20, 43, 0.06);
  /* 슬라이드가 자체 배경색을 가지므로 패널 높이를 꽉 채우게 한다.
     안 그러면 콘텐츠가 min-height보다 짧을 때 아래가 흰색으로 갈라져 보인다. */
  display: flex;
  flex-direction: column;
}

.report-panel :slotted(*) {
  flex: 1 0 auto;
}

.state-card {
  min-height: 260px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #e9eaf2;
  border-radius: 12px;
  background: #fff;
  color: #777;
  font-size: 15px;
}

.state-card.error {
  color: #dc2626;
}
</style>
