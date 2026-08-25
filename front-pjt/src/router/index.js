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

router.beforeEach(to => {
  if (import.meta.env.DEV) return

  const token = localStorage.getItem('access_token')

  if (to.meta.requiresAuth && !token) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  if (to.meta.guestOnly && token) {
    return { path: '/' }
  }
})

export default router
