import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/HomeView.vue'

const routes = [
  { path: '/', component: HomeView },

  { path: '/login',  component: () => import('@/views/auth/LoginView.vue'),  meta: { guestOnly: true } },
  { path: '/signup', component: () => import('@/views/auth/SignupView.vue'), meta: { guestOnly: true } },

  {
    path: '/dashboard',
    component: () => import('@/views/dashboard/DashboardView.vue'),
    meta: { requiresAuth: true },
    children: [
      { path: '', redirect: '/dashboard/home' },
      { path: 'home',     component: () => import('@/views/dashboard/DashboardHomeView.vue') },
      { path: 'assets',   component: () => import('@/views/dashboard/AssetLiabilityView.vue') },
      { path: 'cashflow', component: () => import('@/views/dashboard/CashFlowView.vue') },
      { path: 'goals',    component: () => import('@/views/dashboard/FinancialGoalView.vue') },
      { path: 'lifetime', component: () => import('@/views/dashboard/LifetimeCashFlowView.vue') },
      { path: 'report',   component: () => import('@/views/dashboard/ReportView.vue') },
    ],
  },

  {
    path: '/pb',
    component: () => import('@/views/pb/PbDashboardView.vue'),
    meta: { requiresAuth: true, requiresStaff: true },
    children: [
      { path: '', redirect: '/pb/overview' },
      { path: 'overview',      component: () => import('@/views/pb/PbOverviewView.vue') },
      { path: 'customers',     component: () => import('@/views/pb/PbCustomerListView.vue') },
      { path: 'customers/:id', component: () => import('@/views/pb/PbCustomerDetailView.vue') },
      { path: 'segments',      component: () => import('@/views/pb/PbSegmentsView.vue') },
    ],
  },

  { path: '/ai',              component: () => import('@/views/ai/AiChatView.vue'),             meta: { requiresAuth: true } },
  { path: '/community',       component: () => import('@/views/community/CommunityView.vue') },
  { path: '/community/write', component: () => import('@/views/community/CommunityWriteView.vue'), meta: { requiresAuth: true } },
  { path: '/community/:id',   component: () => import('@/views/community/CommunityPostView.vue') },
]

if (import.meta.env.DEV) {
  routes.push({ path: '/dev/forms', component: () => import('@/views/dev/FormExperimentsView.vue') })
  routes.push({ path: '/dev/presentation', component: () => import('@/views/dev/PresentationExperimentsView.vue') })
}

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

router.beforeEach(async to => {
  const token = localStorage.getItem('access_token')

  // DEV 편의로 인증 가드만 건너뛴다 — 역할(requiresStaff) 가드는 항상 검사
  if (!import.meta.env.DEV) {
    if (to.meta.requiresAuth && !token) {
      return { path: '/login', query: { redirect: to.fullPath } }
    }

    if (to.meta.guestOnly && token) {
      return { path: '/' }
    }
  }

  if (to.meta.requiresStaff) {
    if (!token) {
      return { path: '/login', query: { redirect: to.fullPath } }
    }
    const { useAuthStore } = await import('@/stores/auth')
    const user = await useAuthStore().ensureUser()
    if (!user?.is_staff) {
      return { path: '/' }
    }
  }
})

export default router
