<template>
  <div class="customer-list">
    <div class="page-head">
      <h2 class="page-title">고객 리스트</h2>
      <div class="refresh-area">
        <span v-if="generatedAt" class="refreshed-at">마지막 갱신 {{ refreshedLabel }}</span>
        <button type="button" class="btn-refresh" :disabled="loading" @click="load">
          새로고침
        </button>
      </div>
    </div>

    <div class="filter-bar">
      <input
        v-model="search"
        type="search"
        class="search-input"
        placeholder="이름·이메일 검색"
        @keyup.enter="applySearch"
      />
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
    </div>

    <div class="table-card">
      <table class="customer-table">
        <thead>
          <tr>
            <th v-for="col in COLUMNS" :key="col.key"
                :class="{ sortable: col.sortable, num: col.numeric }"
                @click="col.sortable && toggleSort(col.key)">
              {{ col.label }}
              <span v-if="col.sortable" class="sort-mark">{{ sortMark(col.key) }}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading && !rows.length">
            <td :colspan="COLUMNS.length" class="empty">불러오는 중…</td>
          </tr>
          <tr v-else-if="error">
            <td :colspan="COLUMNS.length" class="empty error">{{ error }}</td>
          </tr>
          <tr v-else-if="!rows.length">
            <td :colspan="COLUMNS.length" class="empty">조건에 맞는 고객이 없습니다.</td>
          </tr>
          <tr v-for="row in rows" :key="row.user_id" class="data-row" @click="goDetail(row)">
            <td class="name-cell">
              <span class="name">{{ row.name }}</span>
              <span class="email">{{ row.email }}</span>
            </td>
            <td class="num">{{ row.age ?? '—' }}</td>
            <td>{{ row.job ?? '—' }}</td>
            <td>{{ row.need ?? '—' }}</td>
            <td>
              <span class="badge" :class="statusMeta(row.session_status).cls">
                {{ statusMeta(row.session_status).label }}
              </span>
            </td>
            <td class="num">
              <div class="progress-wrap">
                <div class="progress-bar"><div class="progress-fill" :style="{ width: row.progress_pct + '%' }" /></div>
                <span class="progress-num">{{ row.progress_pct }}%</span>
              </div>
            </td>
            <td class="num">{{ formatEok(row.net_worth) }}</td>
            <td class="num" :class="{ minus: (row.monthly_surplus ?? 0) < 0 }">
              {{ formatEok(row.monthly_surplus) }}
            </td>
            <td class="num">
              <span v-if="row.risk_count === null">—</span>
              <span v-else-if="row.risk_count === 0" class="risk-chip safe">0</span>
              <span v-else class="risk-chip" :class="row.risk_count >= 3 ? 'danger' : 'warn'">
                {{ row.risk_count }}
              </span>
            </td>
            <td class="num muted">{{ formatDate(row.last_activity) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="pagination" v-if="meta.total_pages > 1">
      <button type="button" :disabled="page <= 1" @click="page -= 1">이전</button>
      <span class="page-info">{{ meta.page }} / {{ meta.total_pages }} 페이지 · 총 {{ meta.total }}명</span>
      <button type="button" :disabled="page >= meta.total_pages" @click="page += 1">다음</button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getCustomers } from '@/api/admin'
import { formatEok } from '@/utils/money'

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
const COLUMNS = [
  { key: 'name', label: '이름', sortable: true },
  { key: 'age', label: '나이', sortable: true, numeric: true },
  { key: 'job', label: '직업' },
  { key: 'need', label: '니즈' },
  { key: 'session_status', label: '상담 상태' },
  { key: 'progress_pct', label: '진행률', sortable: true, numeric: true },
  { key: 'net_worth', label: '순자산', sortable: true, numeric: true },
  { key: 'monthly_surplus', label: '월 잉여', sortable: true, numeric: true },
  { key: 'risk_count', label: '위험지표', sortable: true, numeric: true },
  { key: 'last_activity', label: '최근 활동', sortable: true, numeric: true },
]

const route = useRoute()
const router = useRouter()

// FR-07 크로스 필터: /pb/customers?need=은퇴 같은 쿼리를 초기 필터로 반영
const search = ref(route.query.search ?? '')
const statusFilter = ref(route.query.status ?? '')
const needFilter = ref(route.query.need ?? '')
const ageBandFilter = ref(route.query.age_band ?? '')
const netWorthBandFilter = ref(route.query.net_worth_band ?? '')
const ordering = ref(route.query.ordering ?? '-last_activity')
const page = ref(Number(route.query.page) || 1)

const rows = ref([])
const meta = ref({ total: 0, page: 1, page_size: 20, total_pages: 1 })
const generatedAt = ref(null)
const loading = ref(false)
const error = ref('')
const appliedSearch = ref(search.value)

const refreshedLabel = computed(() =>
  generatedAt.value ? new Date(generatedAt.value).toLocaleTimeString('ko-KR') : '')

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
  loading.value = true
  error.value = ''
  try {
    const body = await getCustomers({
      search: appliedSearch.value || undefined,
      status: statusFilter.value || undefined,
      need: needFilter.value || undefined,
      age_band: ageBandFilter.value || undefined,
      net_worth_band: netWorthBandFilter.value || undefined,
      ordering: ordering.value || undefined,
      page: page.value,
    })
    rows.value = body.data
    meta.value = body.meta
    generatedAt.value = body.meta.generated_at
  } catch (err) {
    error.value = err.response?.status === 403
      ? 'PB 권한이 필요한 화면입니다.'
      : '고객 리스트를 불러오지 못했습니다.'
  } finally {
    loading.value = false
  }
}

watch(
  [appliedSearch, statusFilter, needFilter, ageBandFilter, netWorthBandFilter, ordering, page],
  load,
  { immediate: true },
)
</script>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 18px;
}

.page-title {
  font-size: 22px;
  font-weight: 700;
  color: #1a1a2e;
  margin: 0;
}

.refresh-area {
  display: flex;
  align-items: center;
  gap: 10px;
}

.refreshed-at {
  font-size: 12.5px;
  color: #888;
}

.btn-refresh {
  border: 1px solid #d9d8f5;
  background: #fff;
  color: #5e5ce6;
  font-size: 13px;
  font-weight: 600;
  padding: 7px 14px;
  border-radius: 8px;
  cursor: pointer;
}

.btn-refresh:hover:not(:disabled) { background: #f3f3ff; }
.btn-refresh:disabled { opacity: 0.5; cursor: default; }

.filter-bar {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

.search-input {
  flex: 1;
  min-width: 220px;
  padding: 9px 12px;
  border: 1.5px solid #e0dff7;
  border-radius: 8px;
  font-size: 13.5px;
  outline: none;
}

.search-input:focus { border-color: #5e5ce6; }

.filter-select {
  padding: 9px 10px;
  border: 1.5px solid #e0dff7;
  border-radius: 8px;
  font-size: 13px;
  color: #444;
  background: #fff;
  outline: none;
}

.table-card {
  background: #fff;
  border: 1px solid #ebebf5;
  border-radius: 14px;
  overflow-x: auto;
  box-shadow: 0 2px 10px rgba(94, 92, 230, 0.05);
}

.customer-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13.5px;
  min-width: 920px;
}

.customer-table th {
  text-align: left;
  font-size: 12px;
  font-weight: 600;
  color: #777;
  padding: 12px 14px;
  border-bottom: 1px solid #ebebf5;
  background: #fafaff;
  white-space: nowrap;
  user-select: none;
}

.customer-table th.sortable { cursor: pointer; }
.customer-table th.sortable:hover { color: #5e5ce6; }
.customer-table th.num { text-align: right; }

.sort-mark { font-size: 10px; color: #5e5ce6; }

.customer-table td {
  padding: 12px 14px;
  border-bottom: 1px solid #f4f4fb;
  color: #333;
  white-space: nowrap;
}

.customer-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
.customer-table td.minus { color: #e53e3e; }
.customer-table td.muted { color: #999; }

.data-row { cursor: pointer; transition: background 0.12s ease; }
.data-row:hover { background: #f7f7ff; }

.name-cell { display: flex; flex-direction: column; }
.name-cell .name { font-weight: 600; color: #1a1a2e; }
.name-cell .email { font-size: 11.5px; color: #999; }

.badge {
  display: inline-block;
  font-size: 12px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 999px;
}

.badge.done       { background: #e6f6ee; color: #23855c; }
.badge.progress   { background: #eeeefc; color: #5e5ce6; }
.badge.onboarding { background: #fff3e0; color: #b06f14; }
.badge.abandoned  { background: #f2f2f2; color: #888; }
.badge.none       { background: #fafafa; color: #aaa; border: 1px solid #eee; }

.progress-wrap { display: flex; align-items: center; gap: 8px; justify-content: flex-end; }
.progress-bar {
  width: 64px;
  height: 6px;
  background: #eeeefc;
  border-radius: 3px;
  overflow: hidden;
}
.progress-fill { height: 100%; background: #5e5ce6; border-radius: 3px; }
.progress-num { font-size: 12px; color: #666; width: 34px; text-align: right; }

.risk-chip {
  display: inline-block;
  min-width: 24px;
  text-align: center;
  font-size: 12px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
}
.risk-chip.safe   { background: #e6f6ee; color: #23855c; }
.risk-chip.warn   { background: #fff3e0; color: #b06f14; }
.risk-chip.danger { background: #fdecec; color: #d43a3a; }

.empty {
  text-align: center;
  color: #999;
  padding: 40px 0 !important;
}
.empty.error { color: #d43a3a; }

.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  margin-top: 16px;
}

.pagination button {
  border: 1px solid #e0dff7;
  background: #fff;
  color: #444;
  font-size: 13px;
  padding: 7px 14px;
  border-radius: 8px;
  cursor: pointer;
}

.pagination button:hover:not(:disabled) { border-color: #5e5ce6; color: #5e5ce6; }
.pagination button:disabled { opacity: 0.4; cursor: default; }

.page-info { font-size: 13px; color: #666; }
</style>
