<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      대시보드
    </div>

    <div class="sidebar-section">
      <RouterLink to="/dashboard/home" class="sidebar-item parent" active-class="active">
        현재
      </RouterLink>
      <div class="sidebar-children">
        <RouterLink to="/dashboard/assets" class="sidebar-item child" active-class="active">
          자산 및 부채
        </RouterLink>
        <RouterLink to="/dashboard/cashflow" class="sidebar-item child" active-class="active">
          현금흐름
        </RouterLink>
      </div>
    </div>

    <div class="sidebar-section">
      <span class="sidebar-item parent label-only">
        미래
      </span>
      <div class="sidebar-children">
        <RouterLink to="/dashboard/goals" class="sidebar-item child" active-class="active">
          재무 목표
        </RouterLink>
        <RouterLink to="/dashboard/lifetime" class="sidebar-item child" active-class="active">
          생애현금흐름 및 생애순자산
        </RouterLink>
        <RouterLink to="/dashboard/simulation" class="sidebar-item child" active-class="active">
          은퇴 시뮬레이션
        </RouterLink>
      </div>
    </div>

  </aside>
</template>

<style scoped>
.sidebar {
  width: 270px;
  flex-shrink: 0;
  background-color: #ffffff;
  border-right: 1px solid #e9eaf2;
  padding: 24px 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

.sidebar-section {
  margin-bottom: 8px;
}

.sidebar-header {
  color: #1a1a2e;
  font-size: 17px;
  font-weight: 800;
  letter-spacing: -0.2px;

  /* 본문 콘텐츠 상단(28px)과 시각적으로 정렬 */
  padding: 30px 20px 14px 20px;

  border-bottom: 1px solid #f0f1f7;
  margin-bottom: 16px;
}

.sidebar-item {
  display: block;
  padding: 9px 20px;
  font-size: 14px;
  text-decoration: none;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}

.sidebar-item.parent {
  color: #1a1a1a;
  font-weight: 700;
  font-size: 13px;
  letter-spacing: 0.3px;
}

.sidebar-item.parent.label-only {
  color: #888;
  font-weight: 700;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  cursor: default;
  padding-top: 20px;
}

.sidebar-item.parent:not(.label-only):hover {
  background-color: #f3f3ff;
  color: #5e5ce6;
}

.sidebar-item.parent.active {
  color: #5e5ce6;
  background-color: #f0f0fd;
}

.sidebar-children {
  margin-left: 0;
}

.sidebar-item.child {
  color: #555;
  font-weight: 400;
  padding-left: 33px;
  font-size: 13.5px;
  border-left: 3px solid transparent;
}

.sidebar-item.child:hover {
  background-color: #f5f5ff;
  color: #5e5ce6;
}

.sidebar-item.child.active {
  color: #5e5ce6;
  font-weight: 600;
  background-color: #f0f0fd;
  border-left-color: #5e5ce6;
}
</style>
