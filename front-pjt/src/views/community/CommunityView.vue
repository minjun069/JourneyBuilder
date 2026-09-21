<script setup>
import { ref, computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import { useCommunityStore, CATEGORY_STYLE, formatViews } from './communityStore.js'

const communityStore = useCommunityStore()
const { posts, loading, error } = storeToRefs(communityStore)

const router = useRouter()

onMounted(() => {
  communityStore.fetchPosts()
})

// ─── Tabs ─────────────────────────────────────────────────
const activeTab = ref('추천 글')
const TABS = ['추천 글', '인기 글']

// ─── Filters ──────────────────────────────────────────────
const searchQuery = ref('')
const filterCategory = ref('전체')
const filterAge    = ref('전체')
const filterAsset  = ref('전체')
const filterIncome = ref('전체')

const CATEGORY_OPTIONS = ['전체', '재무계획', '투자', '은퇴준비', '절약/지출', '대출/부채', '기타']
const AGE_OPTIONS    = ['전체', '20대', '30대', '40대', '50대', '60대+']
const ASSET_OPTIONS  = ['전체', '1억 미만', '1~3억', '3~5억', '5~10억', '10억+']
const INCOME_OPTIONS = ['전체', '3천 미만', '3~5천', '5~7천', '7천~1억', '1억+']

const isFiltered = computed(() =>
  !!searchQuery.value.trim() ||
  filterCategory.value !== '전체' ||
  filterAge.value    !== '전체' ||
  filterAsset.value  !== '전체' ||
  filterIncome.value !== '전체'
)

function resetFilters() {
  searchQuery.value  = ''
  filterCategory.value    = '전체'
  filterAge.value    = '전체'
  filterAsset.value  = '전체'
  filterIncome.value = '전체'
}

// ─── Filtered Posts ───────────────────────────────────────
const filteredPosts = computed(() => {
  let list = posts.value.slice()

  if (activeTab.value === '추천 글') {
    list = list.filter((p) => p.recommended)
  } else {
    list.sort((a, b) => b.likes - a.likes)
  }

  const q = searchQuery.value.trim().toLowerCase()
  if (q) {
    list = list.filter((p) =>
      p.title.toLowerCase().includes(q) || p.body.toLowerCase().includes(q),
    )
  }

  if (filterCategory.value  !== '전체') list = list.filter((p) => p.category  === filterCategory.value)
  if (filterAge.value       !== '전체') list = list.filter((p) => p.age       === filterAge.value)
  if (filterAsset.value     !== '전체') list = list.filter((p) => p.asset     === filterAsset.value)
  if (filterIncome.value    !== '전체') list = list.filter((p) => p.income    === filterIncome.value)

  return list
})

// ─── Like ─────────────────────────────────────────────────
const likeError = ref('')

async function onToggleLike(post) {
  // axios 인터셉터가 401을 하드 리다이렉트하므로 API 호출 전에 토큰을 확인한다
  if (!localStorage.getItem('access_token')) {
    router.push('/login')
    return
  }
  likeError.value = ''
  try {
    await communityStore.toggleLike(post.id)
  } catch {
    likeError.value = '좋아요 처리에 실패했습니다. 잠시 후 다시 시도해 주세요.'
  }
}

// 빈 밴드가 있어도 ' · · ' 없이 이어붙인다
function metaLine(post) {
  return [post.category, post.age, post.asset, post.income].filter(Boolean).join(' · ')
}
</script>

<template>
  <div class="community-page">
    <div class="content-wrap">

      <!-- Page Header -->
      <div class="page-header">
        <div class="header-left">
          <h1 class="page-title">커뮤니티</h1>
          <p class="page-desc">재무 경험과 노하우를 함께 나눠요</p>
        </div>
        <button class="write-btn" @click="router.push('/community/write')">
          <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
            <path d="M7.5 1v13M1 7.5h13" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
          </svg>
          글 작성
        </button>
      </div>

      <!-- Tab Bar -->
      <div class="tab-bar">
        <button
          v-for="tab in TABS"
          :key="tab"
          class="tab"
          :class="{ active: activeTab === tab }"
          @click="activeTab = tab"
        >
          <svg v-if="tab === '추천 글'" width="14" height="14" viewBox="0 0 24 24" fill="none">
            <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
          </svg>
          <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none">
            <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
          </svg>
          {{ tab }}
        </button>
      </div>

      <!-- Search + Filter -->
      <div class="filter-panel">
        <div class="search-row">
          <div class="search-box">
            <svg class="search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none">
              <circle cx="11" cy="11" r="7" stroke="#9ca3af" stroke-width="1.8"/>
              <path d="M16.5 16.5L21 21" stroke="#9ca3af" stroke-width="1.8" stroke-linecap="round"/>
            </svg>
            <input v-model="searchQuery" type="text" class="search-input" placeholder="제목 또는 내용 검색..."/>
            <button v-if="searchQuery" class="input-clear" @click="searchQuery = ''">✕</button>
          </div>
          <button v-if="isFiltered" class="reset-btn" @click="resetFilters">필터 초기화</button>
        </div>

        <div class="filter-row">
          <div class="filter-item">
            <label class="filter-label">카테고리</label>
            <select v-model="filterCategory" class="filter-select">
              <option v-for="opt in CATEGORY_OPTIONS" :key="opt">{{ opt }}</option>
            </select>
          </div>
          <div class="filter-item">
            <label class="filter-label">연령대</label>
            <select v-model="filterAge" class="filter-select">
              <option v-for="opt in AGE_OPTIONS" :key="opt">{{ opt }}</option>
            </select>
          </div>
          <div class="filter-item">
            <label class="filter-label">자산규모</label>
            <select v-model="filterAsset" class="filter-select">
              <option v-for="opt in ASSET_OPTIONS" :key="opt">{{ opt }}</option>
            </select>
          </div>
          <div class="filter-item">
            <label class="filter-label">연봉</label>
            <select v-model="filterIncome" class="filter-select">
              <option v-for="opt in INCOME_OPTIONS" :key="opt">{{ opt }}</option>
            </select>
          </div>
        </div>
      </div>

      <!-- Post List -->
      <span class="result-count">전체 게시물 {{ filteredPosts.length }}개</span>

      <p v-if="likeError" class="like-error">{{ likeError }}</p>

      <div class="post-list">
        <!-- 로딩 스켈레톤 (기존 데이터가 없을 때만) -->
        <template v-if="loading && posts.length === 0">
          <div v-for="i in 3" :key="i" class="post-card skeleton-card">
            <div class="skeleton-line w-30"></div>
            <div class="skeleton-line w-70"></div>
            <div class="skeleton-line w-90"></div>
          </div>
        </template>

        <!-- 오류 -->
        <div v-else-if="error && posts.length === 0" class="empty-state">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="9" stroke="#d1d5db" stroke-width="1.5"/>
            <path d="M12 8v4M12 15.5v.5" stroke="#d1d5db" stroke-width="1.5" stroke-linecap="round"/>
          </svg>
          <p>게시글을 불러오지 못했습니다.</p>
          <button class="reset-btn" @click="communityStore.fetchPosts()">다시 시도</button>
        </div>

        <!-- 빈 상태: 필터/검색 중 > 추천 탭 빔 > 전체 빔 -->
        <div v-else-if="filteredPosts.length === 0" class="empty-state">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none">
            <circle cx="11" cy="11" r="7" stroke="#d1d5db" stroke-width="1.5"/>
            <path d="M16.5 16.5L21 21" stroke="#d1d5db" stroke-width="1.5" stroke-linecap="round"/>
          </svg>
          <template v-if="isFiltered">
            <p>검색 결과가 없습니다.</p>
            <button class="reset-btn" @click="resetFilters">필터 초기화</button>
          </template>
          <p v-else-if="activeTab === '추천 글'">아직 추천 글이 없습니다.</p>
          <p v-else>아직 게시글이 없습니다.</p>
        </div>

        <article
          v-for="(post, idx) in filteredPosts"
          :key="post.id"
          class="post-card"
          @click="router.push(`/community/${post.id}`)"
        >
          <div v-if="activeTab === '인기 글'" class="rank-badge" :class="{ top3: idx < 3 }">
            {{ idx + 1 }}
          </div>

          <div class="post-top">
            <span class="category-badge" :style="CATEGORY_STYLE[post.category]">{{ post.category }}</span>
            <span class="post-time">{{ post.createdAt }}</span>
          </div>

          <h3 class="post-title">{{ post.title }}</h3>
          <p class="post-body">{{ post.body }}</p>

          <div class="post-footer">
            <div class="author-row">
              <span class="author-avatar">{{ post.author[0] }}</span>
              <span class="author-name">{{ post.author }}</span>
              <span class="author-meta">{{ metaLine(post) }}</span>
            </div>
            <div class="stat-row">
              <button
                class="stat-btn like-btn"
                :class="{ liked: post.liked }"
                :disabled="communityStore.isLikePending(post.id)"
                @click.stop="onToggleLike(post)"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                  <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"
                    :fill="post.liked ? 'currentColor' : 'none'"
                    stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
                {{ post.likes }}
              </button>
              <span class="stat">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"
                    stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
                {{ post.comments }}
              </span>
              <span class="stat">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" stroke="currentColor" stroke-width="1.8"/>
                  <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8"/>
                </svg>
                {{ formatViews(post.views) }}
              </span>
            </div>
          </div>
        </article>
      </div>

    </div>
  </div>
</template>

<style scoped>
.community-page {
  min-height: calc(100vh - 80px);
  background: #f7f8fc;
  padding: 36px 24px 60px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

.content-wrap {
  max-width: 820px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* ── Header ───────────────────────────────────────── */
.page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
}

.page-title {
  font-size: 26px;
  font-weight: 800;
  color: #111827;
  letter-spacing: -0.4px;
  margin: 0 0 4px;
}

.page-desc {
  font-size: 13px;
  color: #9ca3af;
  margin: 0;
}

.write-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 9px 18px;
  background: #5e5ce6;
  color: #fff;
  border: none;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s;
}

.write-btn:hover { background: #4c4ad4; }

/* ── Tabs ─────────────────────────────────────────── */
.tab-bar { display: flex; gap: 6px; }

.tab {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 18px;
  background: #fff;
  border: 1.5px solid #e5e7eb;
  border-radius: 999px;
  font-size: 14px;
  font-weight: 500;
  color: #6b7280;
  cursor: pointer;
  transition: all 0.15s;
}

.tab:hover { border-color: #5e5ce6; color: #5e5ce6; }

.tab.active {
  background: #5e5ce6;
  border-color: #5e5ce6;
  color: #fff;
  font-weight: 700;
}

/* ── Filter Panel ────────────────────────────────── */
.filter-panel {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 14px;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.search-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.search-box {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  background: #f9fafb;
  border: 1.5px solid #e5e7eb;
  border-radius: 10px;
  padding: 0 12px;
  transition: border-color 0.15s;
}

.search-box:focus-within { border-color: #5e5ce6; }

.search-input {
  flex: 1;
  border: none;
  background: transparent;
  font-size: 14px;
  color: #111827;
  padding: 10px 0;
  outline: none;
}

.search-input::placeholder { color: #9ca3af; }

.input-clear {
  background: none;
  border: none;
  color: #9ca3af;
  cursor: pointer;
  font-size: 12px;
  padding: 2px 4px;
}

.reset-btn {
  padding: 8px 14px;
  background: none;
  border: 1.5px solid #e5e7eb;
  border-radius: 8px;
  font-size: 13px;
  color: #6b7280;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s;
}

.reset-btn:hover { border-color: #5e5ce6; color: #5e5ce6; }

.filter-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.filter-item { display: flex; align-items: center; gap: 6px; }

.filter-label {
  font-size: 12px;
  font-weight: 600;
  color: #6b7280;
  white-space: nowrap;
}

.filter-select {
  padding: 5px 28px 5px 10px;
  background: #f9fafb url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%239ca3af' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round' fill='none'/%3E%3C/svg%3E") no-repeat right 8px center;
  border: 1.5px solid #e5e7eb;
  border-radius: 8px;
  font-size: 13px;
  color: #374151;
  cursor: pointer;
  outline: none;
  -webkit-appearance: none;
  appearance: none;
  transition: border-color 0.15s;
}

.filter-select:focus { border-color: #5e5ce6; }

.result-count { margin-left: auto; font-size: 12px; color: #9ca3af; }

.like-error { font-size: 12px; color: #dc2626; margin: 0; }

/* ── Post List ───────────────────────────────────── */
.post-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 60px 0;
  color: #9ca3af;
  font-size: 14px;
}

/* ── Loading Skeleton ────────────────────────────── */
.skeleton-card { cursor: default; }
.skeleton-card:hover { box-shadow: none; transform: none; border-color: #e5e7eb; }

.skeleton-line {
  height: 14px;
  border-radius: 6px;
  background: linear-gradient(90deg, #f3f4f6 25%, #e5e7eb 50%, #f3f4f6 75%);
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.4s ease infinite;
}

.skeleton-line.w-30 { width: 30%; }
.skeleton-line.w-70 { width: 70%; }
.skeleton-line.w-90 { width: 90%; }

@keyframes skeleton-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

/* ── Post Card ───────────────────────────────────── */
.post-card {
  position: relative;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 14px;
  padding: 20px 24px;
  cursor: pointer;
  transition: box-shadow 0.15s, transform 0.15s, border-color 0.15s;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.post-card:hover {
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.07);
  transform: translateY(-1px);
  border-color: #d1d5db;
}

.rank-badge {
  position: absolute;
  top: 10px;
  right: 830px;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: #f3f4f6;
  color: #6b7280;
  font-size: 12px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
}

.rank-badge.top3 { background: #5e5ce6; color: #fff; }

.post-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.category-badge {
  display: inline-block;
  padding: 3px 9px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
}

.post-time { font-size: 12px; color: #9ca3af; }

.post-title {
  font-size: 16px;
  font-weight: 700;
  color: #111827;
  line-height: 1.45;
  margin: 0;
  letter-spacing: -0.2px;
}

.post-body {
  font-size: 13.5px;
  color: #6b7280;
  line-height: 1.6;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.post-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 8px;
  border-top: 1px solid #f3f4f6;
}

.author-row {
  display: flex;
  align-items: center;
  gap: 7px;
}

.author-avatar {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #5e5ce6;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}

.author-name { font-size: 13px; font-weight: 600; color: #374151; }
.author-meta { font-size: 12px; color: #9ca3af; }

.stat-row { display: flex; align-items: center; gap: 12px; }

.stat {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #9ca3af;
}

.stat-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #9ca3af;
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
  transition: color 0.15s;
}

.stat-btn:hover { color: #f43f5e; }
.stat-btn.liked { color: #f43f5e; }

.stat-btn:disabled { opacity: 0.5; cursor: default; }
.stat-btn:disabled:hover { color: #9ca3af; }
.stat-btn.liked:disabled:hover { color: #f43f5e; }
</style>
