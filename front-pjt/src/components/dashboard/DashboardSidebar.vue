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

    <br>

    <div class="sidebar-section2">
      <RouterLink to="/dashboard/report" class="sidebar-item parent" active-class="active">
        정기 리포트
      </RouterLink>
    </div>

  </aside>
</template>

<style scoped>
.sidebar {
  width: 270px;
  flex-shrink: 0;
  background-color: #ffffff;
  border-right: 1px solid #ebebeb;
  padding: 24px 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

.sidebar-section {
  margin-bottom: 8px;
}

.sidebar-section2 {
  margin-bottom: 8px;

  /* 하단 경계선 */
  border-top: 1px solid #f5f5f5;
}

.sidebar-header {
  color: #1a1a1a;
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.3px;
  
  padding: 50px 20px 16px 20px;
  
  /* 하단 경계선 */
  border-bottom: 1px solid #f5f5f5;
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
  padding-left: 36px;
  font-size: 13.5px;
}

.sidebar-item.child:hover {
  background-color: #f5f5ff;
  color: #5e5ce6;
}

.sidebar-item.child.active {
  color: #5e5ce6;
  font-weight: 600;
  background-color: #eeeeff;
  border-right: 3px solid #5e5ce6;
}
</style>
