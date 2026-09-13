<template>
  <div class="customer-list">
    <div class="page-head">
      <div>
        <h2 class="page-title">고객 리스트</h2>
        <p class="page-sub" v-if="meta.total">전체 {{ meta.total }}명의 고객을 관리하고 있습니다</p>
      </div>
      <div class="refresh-area">
        <span v-if="generatedAt" class="refreshed-at">{{ refreshedLabel }} 갱신</span>
        <button type="button" class="btn-refresh" :disabled="loading" @click="load">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
            <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 2.5v3h-3" />
          </svg>
          새로고침
        </button>
      </div>
    </div>

    <div class="filter-bar">
      <div class="search-wrap">
        <svg class="search-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6">
          <circle cx="7" cy="7" r="4.5" /><path d="m13.5 13.5-3-3" stroke-linecap="round" />
        </svg>
        <input
          v-model="search"
          type="search"
          class="search-input"
          placeholder="이름·이메일 검색"
          @keyup.enter="applySearch"
        />
      </div>
      <select v-model="statusFilter" class="filter-select" @change="resetToFirstPage">
        <option value="">상담 상태 전체</option>
        <option v-for="opt in STATUS_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
      </select>
      <select v-model="needFilter" class="filter-select" @change="resetToFirstPage">
        <option value="">니즈 전체</option>
        <option v-for="need in NEEDS" :key="need" :value="need">{{ need }}</option>
      </select>
      <select v-model="ageBandFilter" class="filter-select" @change="resetToFirstPage">
        <option value="">연령 전체</option>
        <option v-for="opt in AGE_BANDS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
      </select>
      <select v-model="netWorthBandFilter" class="filter-select" @change="resetToFirstPage">
        <option value="">자산규모 전체</option>
        <option v-for="opt in NET_WORTH_BANDS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
      </select>
      <select v-model="tendencyFilter" class="filter-select" @change="resetToFirstPage">
        <option value="">성향 전체</option>
        <option v-for="opt in TENDENCIES" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
      </select>
      <select v-model="healthFilter" class="filter-select" @change="resetToFirstPage">
        <option value="">건전성 전체</option>
        <option v-for="opt in HEALTHS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
      </select>
    </div>

    <div class="table-card">
      <table class="customer-table">
        <thead>
          <tr>
            <th v-for="col in COLUMNS" :key="col.key"
                :class="{ sortable: col.sortable, num: col.numeric }"
                @click="col.sortable && toggleSort(col.key)">
              {{ col.label }}
              <span v-if="col.sortable" class="sort-mark" :class="{ on: sortMark(col.key) }">
                {{ sortMark(col.key) || '↕' }}
              </span>
            </th>
          </tr>
        </thead>

        <tbody v-if="loading && !rows.length" aria-hidden="true">
          <tr v-for="n in 8" :key="n" class="skeleton-row">
            <td><span class="sk sk-avatar" /><span class="sk sk-line w120" /></td>
            <td class="num"><span class="sk sk-line w24" /></td>
            <td><span class="sk sk-line w48" /></td>
            <td><span class="sk sk-line w40" /></td>
            <td><span class="sk sk-line w56" /></td>
            <td class="num"><span class="sk sk-line w64" /></td>
            <td class="num"><span class="sk sk-line w64" /></td>
            <td class="num"><span class="sk sk-line w56" /></td>
            <td class="num"><span class="sk sk-line w24" /></td>
            <td class="num"><span class="sk sk-line w48" /></td>
          </tr>
        </tbody>

        <tbody v-else>
          <tr v-if="error">
            <td :colspan="COLUMNS.length" class="empty error">{{ error }}</td>
          </tr>
          <tr v-else-if="!rows.length">
            <td :colspan="COLUMNS.length" class="empty">조건에 맞는 고객이 없습니다</td>
          </tr>
          <tr v-for="row in rows" :key="row.user_id" class="data-row" @click="goDetail(row)">
            <td class="name-cell">
              <span class="avatar" :style="avatarStyle(row.name)">{{ initialOf(row.name) }}</span>
              <span class="name-text">
                <span class="name">{{ row.name }}</span>
                <span class="email">{{ row.email }}</span>
              </span>
            </td>
            <td class="num">{{ row.age ?? '—' }}</td>
            <td class="muted">{{ row.job ?? '—' }}</td>
            <td>
              <span v-if="row.need" class="need-chip">{{ row.need }}</span>
              <span v-else class="muted">—</span>
            </td>
            <td>
              <span class="status">
                <i class="dot" :class="statusMeta(row.session_status).cls" />
                {{ statusMeta(row.session_status).label }}
              </span>
            </td>
            <td class="num">
              <div class="progress-wrap">
                <div class="progress-bar">
                  <div class="progress-fill" :class="{ full: row.progress_pct === 100 }"
                       :style="{ width: row.progress_pct + '%' }" />
                </div>
                <span class="progress-num">{{ row.progress_pct }}%</span>
              </div>
            </td>
            <td class="num strong">{{ formatEok(row.net_worth) }}</td>
            <td class="num" :class="{ minus: (row.monthly_surplus ?? 0) < 0 }">
              {{ formatEok(row.monthly_surplus) }}
            </td>
            <td class="num">
              <span v-if="row.risk_count === null" class="muted">—</span>
              <span v-else class="risk-chip"
                    :class="row.risk_count === 0 ? 'safe' : row.risk_count >= 3 ? 'danger' : 'warn'">
                {{ row.risk_count }}
              </span>
            </td>
            <td class="num muted">{{ formatDate(row.last_activity) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="pagination" v-if="meta.total_pages > 1">
      <button type="button" :disabled="page <= 1" @click="page -= 1">← 이전</button>
      <span class="page-info">{{ meta.page }} / {{ meta.total_pages }}</span>
      <button type="button" :disabled="page >= meta.total_pages" @click="page += 1">다음 →</button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter } from 'vue-router'
import { usePolling } from '@/composables/usePolling'
import { useAdminStore } from '@/stores/admin'
import { formatEok } from '@/utils/money'
import { avatarStyle, initialOf } from './pbTheme'

const STATUS_OPTIONS = [
  { value: 'completed', label: '상담 완료' },
  { value: 'in_progress', label: '진행 중' },
  { value: 'onboarding', label: '온보딩' },
  { value: 'abandoned', label: '이탈' },
  { value: 'none', label: '세션 없음' },
]
const STATUS_META = {
  completed:   { label: '완료',   cls: 'done' },
  in_progress: { label: '진행 중', cls: 'progress' },
  onboarding:  { label: '온보딩', cls: 'onboarding' },
  abandoned:   { label: '이탈',   cls: 'abandoned' },
  none:        { label: '세션 없음', cls: 'none' },
}
const NEEDS = ['은퇴', '투자', '주택자금', '교육자금', '보장', '기타']
const AGE_BANDS = [
  { value: '20s', label: '20대' }, { value: '30s', label: '30대' },
  { value: '40s', label: '40대' }, { value: '50s', label: '50대' },
  { value: '65+', label: '65세+' },
]
const NET_WORTH_BANDS = [
  { value: 'under_50m', label: '5천만 미만' },
  { value: '50m_200m', label: '5천만~2억' },
  { value: '200m_500m', label: '2억~5억' },
  { value: '500m_1b', label: '5억~10억' },
  { value: 'over_1b', label: '10억+' },
]
const TENDENCIES = [
  { value: 'stable', label: '안정형' },
  { value: 'balanced', label: '균형형' },
  { value: 'aggressive', label: '공격형' },
  { value: 'unknown', label: '미입력' },
]
const HEALTHS = [
  { value: 'healthy', label: '건전' },
  { value: 'caution', label: '주의' },
  { value: 'risky', label: '위험' },
  { value: 'unknown', label: '미입력' },
]
const COLUMNS = [
  { key: 'name', label: '고객', sortable: true },
  { key: 'age', label: '나이', sortable: true, numeric: true },
  { key: 'job', label: '직업' },
  { key: 'need', label: '니즈' },
  { key: 'session_status', label: '상담 상태' },
  { key: 'progress_pct', label: '진행률', sortable: true, numeric: true },
  { key: 'net_worth', label: '순자산', sortable: true, numeric: true },
  { key: 'monthly_surplus', label: '월 잉여', sortable: true, numeric: true },
  { key: 'risk_count', label: '위험', sortable: true, numeric: true },
  { key: 'last_activity', label: '최근 활동', sortable: true, numeric: true },
]

const route = useRoute()
const router = useRouter()
const adminStore = useAdminStore()
const {
  customers: rows,
  customerListMeta: meta,
  customerListLoading: loading,
  customerListError,
} = storeToRefs(adminStore)

// FR-07 크로스 필터: /pb/customers?need=은퇴 같은 쿼리를 초기 필터로 반영
const search = ref(route.query.search ?? '')
const statusFilter = ref(route.query.status ?? '')
const needFilter = ref(route.query.need ?? '')
const ageBandFilter = ref(route.query.age_band ?? '')
const netWorthBandFilter = ref(route.query.net_worth_band ?? '')
const tendencyFilter = ref(route.query.tendency ?? '')
const healthFilter = ref(route.query.health ?? '')
const ordering = ref(route.query.ordering ?? '-last_activity')
const page = ref(Number(route.query.page) || 1)

const appliedSearch = ref(search.value)

const generatedAt = computed(() => meta.value.generated_at)
const refreshedLabel = computed(() =>
  generatedAt.value ? new Date(generatedAt.value).toLocaleTimeString('ko-KR') : '')
const error = computed(() => {
  if (!customerListError.value) return ''
  return customerListError.value.response?.status === 403
    ? 'PB 권한이 필요한 화면입니다.'
    : '고객 리스트를 불러오지 못했습니다.'
})

function statusMeta(status) {
  return STATUS_META[status] ?? { label: status ?? '—', cls: 'none' }
}

function formatDate(value) {
  if (!value) return '—'
  return new Date(value).toLocaleDateString('ko-KR', { month: 'short', day: 'numeric' })
}

function sortMark(key) {
  if (ordering.value === key) return '▲'
  if (ordering.value === `-${key}`) return '▼'
  return ''
}

function toggleSort(key) {
  ordering.value = ordering.value === `-${key}` ? key : `-${key}`
  page.value = 1
}

function resetToFirstPage() {
  page.value = 1
}

function applySearch() {
  appliedSearch.value = search.value.trim()
  page.value = 1
}

function goDetail(row) {
  router.push(`/pb/customers/${row.user_id}`)
}

async function load() {
  await adminStore.loadCustomers({
    search: appliedSearch.value || undefined,
    status: statusFilter.value || undefined,
    need: needFilter.value || undefined,
    age_band: ageBandFilter.value || undefined,
    net_worth_band: netWorthBandFilter.value || undefined,
    tendency: tendencyFilter.value || undefined,
    health: healthFilter.value || undefined,
    ordering: ordering.value || undefined,
    page: page.value,
  })
}

watch(
  [appliedSearch, statusFilter, needFilter, ageBandFilter, netWorthBandFilter,
   tendencyFilter, healthFilter, ordering, page],
  load,
  { immediate: true },
)

// 크로스 필터(FR-07)로 같은 라우트에 쿼리만 바뀌어 들어와도 필터를 동기화
watch(() => route.query, query => {
  search.value = query.search ?? ''
  appliedSearch.value = query.search ?? ''
  statusFilter.value = query.status ?? ''
  needFilter.value = query.need ?? ''
  ageBandFilter.value = query.age_band ?? ''
  netWorthBandFilter.value = query.net_worth_band ?? ''
  tendencyFilter.value = query.tendency ?? ''
  healthFilter.value = query.health ?? ''
})

usePolling(load) // FR-09: 60초 주기 자동 갱신
</script>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 20px;
}

.page-title {
  font-size: 22px;
  font-weight: 800;
  letter-spacing: -0.3px;
  color: #191a2b;
  margin: 0;
}

.page-sub { font-size: 13px; color: #9a9cb0; margin: 5px 0 0; }

.refresh-area { display: flex; align-items: center; gap: 12px; }
.refreshed-at { font-size: 12px; color: #a2a4b8; font-variant-numeric: tabular-nums; }

.btn-refresh {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid #e3e2f4;
  background: #fff;
  color: #55538a;
  font-size: 13px;
  font-weight: 600;
  padding: 8px 14px;
  border-radius: 9px;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.btn-refresh svg { width: 13px; height: 13px; }
.btn-refresh:hover:not(:disabled) { border-color: #c5c3ee; box-shadow: 0 1px 6px rgba(94, 92, 230, 0.12); }
.btn-refresh:disabled { opacity: 0.5; cursor: default; }

.filter-bar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

.search-wrap { position: relative; flex: 1; min-width: 220px; }
.search-icon {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  width: 14px;
  height: 14px;
  color: #a2a4b8;
  pointer-events: none;
}

.search-input {
  width: 100%;
  box-sizing: border-box;
  padding: 9px 12px 9px 34px;
  border: 1px solid #e6e6f0;
  background: #fff;
  border-radius: 9px;
  font-size: 13.5px;
  color: #191a2b;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.search-input:focus { border-color: #5e5ce6; box-shadow: 0 0 0 3px rgba(94, 92, 230, 0.1); }
.search-input::placeholder { color: #b6b8c8; }

.filter-select {
  padding: 9px 10px;
  border: 1px solid #e6e6f0;
  border-radius: 9px;
  font-size: 13px;
  color: #444;
  background: #fff;
  outline: none;
  cursor: pointer;
  transition: border-color 0.15s ease;
}
.filter-select:hover { border-color: #c5c3ee; }
.filter-select:focus { border-color: #5e5ce6; }

.table-card {
  background: #fff;
  border: 1px solid #ebebf2;
  border-radius: 14px;
  overflow-x: auto;
  box-shadow: 0 1px 3px rgba(25, 26, 43, 0.04);
}

.customer-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13.5px;
  min-width: 960px;
}

.customer-table th {
  position: sticky;
  top: 0;
  text-align: left;
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: #8a8ca3;
  padding: 12px 14px;
  border-bottom: 1px solid #ededf4;
  background: #fbfbfe;
  white-space: nowrap;
  user-select: none;
}

.customer-table th.sortable { cursor: pointer; }
.customer-table th.sortable:hover { color: #5e5ce6; }
.customer-table th.num { text-align: right; }

.sort-mark { font-size: 9px; color: #c9c9dd; margin-left: 1px; }
.sort-mark.on { color: #5e5ce6; }

.customer-table td {
  padding: 11px 14px;
  border-bottom: 1px solid #f5f5fa;
  color: #33344a;
  white-space: nowrap;
}
.customer-table tr:last-child td { border-bottom: none; }

.customer-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
.customer-table td.strong { font-weight: 600; color: #191a2b; }
.customer-table td.minus { color: #d64550; }
.customer-table .muted { color: #a2a4b8; }

.data-row { cursor: pointer; transition: background 0.12s ease; }
.data-row:hover { background: #f8f8fe; }

.name-cell { display: flex; align-items: center; gap: 10px; }
.avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
  flex-shrink: 0;
}
.name-text { display: flex; flex-direction: column; min-width: 0; }
.name-text .name { font-weight: 600; color: #191a2b; }
.name-text .email { font-size: 11px; color: #a2a4b8; }

.need-chip {
  font-size: 12px;
  font-weight: 600;
  color: #55538a;
  background: #f3f3fc;
  border-radius: 6px;
  padding: 3px 8px;
}

.status { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; font-weight: 600; color: #565a6e; }
.dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.dot.done       { background: #3eb37f; }
.dot.progress   { background: #5e5ce6; }
.dot.onboarding { background: #f2a54a; }
.dot.abandoned  { background: #c2c3d1; }
.dot.none       { background: #e3e3ec; }

.progress-wrap { display: flex; align-items: center; gap: 8px; justify-content: flex-end; }
.progress-bar {
  width: 60px;
  height: 5px;
  background: #efeff6;
  border-radius: 3px;
  overflow: hidden;
}
.progress-fill { height: 100%; background: #8280ee; border-radius: 3px; transition: width 0.3s ease; }
.progress-fill.full { background: #3eb37f; }
.progress-num { font-size: 11.5px; color: #8a8ca3; width: 32px; text-align: right; font-variant-numeric: tabular-nums; }

.risk-chip {
  display: inline-block;
  min-width: 22px;
  text-align: center;
  font-size: 12px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 7px;
}
.risk-chip.safe   { background: #e8f6ef; color: #23855c; }
.risk-chip.warn   { background: #fdf3e2; color: #b06f14; }
.risk-chip.danger { background: #fdebec; color: #d64550; }

.empty { text-align: center; color: #9a9cb0; padding: 48px 0 !important; }
.empty.error { color: #d64550; }

/* 스켈레톤 로딩 */
.skeleton-row td { padding: 13px 14px; }
.sk {
  display: inline-block;
  border-radius: 5px;
  background: linear-gradient(90deg, #f0f0f6 25%, #f8f8fc 50%, #f0f0f6 75%);
  background-size: 200% 100%;
  animation: shimmer 1.4s ease infinite;
  vertical-align: middle;
}
.sk-avatar { width: 32px; height: 32px; border-radius: 50%; margin-right: 10px; }
.sk-line { height: 11px; }
.w24 { width: 24px; } .w40 { width: 40px; } .w48 { width: 48px; }
.w56 { width: 56px; } .w64 { width: 64px; } .w120 { width: 120px; }

@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  margin-top: 18px;
}

.pagination button {
  border: 1px solid #e6e6f0;
  background: #fff;
  color: #565a6e;
  font-size: 13px;
  font-weight: 600;
  padding: 7px 16px;
  border-radius: 9px;
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease;
}

.pagination button:hover:not(:disabled) { border-color: #5e5ce6; color: #5e5ce6; }
.pagination button:disabled { opacity: 0.35; cursor: default; }

.page-info { font-size: 13px; color: #8a8ca3; font-variant-numeric: tabular-nums; }
</style>
