<template>
  <div class="pb-layout">
    <aside class="sidebar">
      <div class="sidebar-header">PB 대시보드</div>

      <div class="sidebar-section">
        <RouterLink to="/pb/overview" class="sidebar-item parent" active-class="active">
          개요
        </RouterLink>
        <RouterLink to="/pb/customers" class="sidebar-item parent" active-class="active">
          고객 리스트
        </RouterLink>
        <RouterLink to="/pb/segments" class="sidebar-item parent" active-class="active">
          세그먼트 분석
        </RouterLink>
      </div>
    </aside>

    <main class="pb-content">
      <RouterView />
    </main>
  </div>
</template>

<script setup>
</script>

<style scoped>
.pb-layout {
  display: flex;
  min-height: calc(100vh - 80px);
  max-width: 1440px;
  margin: 0 auto;
  background-color: #f7f8fc;
}

.pb-content {
  flex: 1;
  overflow-y: auto;
  padding: 28px 32px;
  min-width: 0;
}

.sidebar {
  width: 270px;
  flex-shrink: 0;
  background-color: #ffffff;
  border-right: 1px solid #ebebeb;
  padding: 24px 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

.sidebar-header {
  color: #1a1a1a;
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.3px;
  padding: 50px 20px 16px 20px;
  border-bottom: 1px solid #f5f5f5;
  margin-bottom: 16px;
}

.sidebar-item {
  display: block;
  padding: 11px 20px;
  font-size: 14px;
  text-decoration: none;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}

.sidebar-item.parent {
  color: #1a1a1a;
  font-weight: 600;
}

.sidebar-item.parent:hover {
  background-color: #f3f3ff;
  color: #5e5ce6;
}

.sidebar-item.parent.active {
  color: #5e5ce6;
  background-color: #f0f0fd;
  border-right: 3px solid #5e5ce6;
}
</style>
