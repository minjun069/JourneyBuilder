<script setup>
import { ref, computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter } from 'vue-router'
import { useCommunityStore, CATEGORY_STYLE, formatViews } from './communityStore.js'
import { useReportStore } from '@/stores/report'

const communityStore = useCommunityStore()
const { posts, detailLoading } = storeToRefs(communityStore)
import BalanceSheetSlide      from '@/components/presentation/BalanceSheetSlide.vue'
import CashFlowSlide          from '@/components/presentation/CashFlowSlide.vue'
import FinancialIndicatorsSlide from '@/components/presentation/FinancialIndicatorsSlide.vue'
import FinancialGoalSlide     from '@/components/presentation/FinancialGoalSlide.vue'
import RetirementFundSlide    from '@/components/presentation/RetirementFundSlide.vue'
import RetirementReadinessSlide from '@/components/presentation/RetirementReadinessSlide.vue'
import PensionScheduleSlide   from '@/components/presentation/PensionScheduleSlide.vue'
import LifetimeCashflowSlide  from '@/components/presentation/LifetimeCashflowSlide.vue'
import RequiredSavingsSlide   from '@/components/presentation/RequiredSavingsSlide.vue'

const COMPONENT_MAP = {
  BalanceSheetSlide,
  CashFlowSlide,
  FinancialIndicatorsSlide,
  FinancialGoalSlide,
  RetirementFundSlide,
  RetirementReadinessSlide,
  PensionScheduleSlide,
  LifetimeCashflowSlide,
  RequiredSavingsSlide,
}

// store를 읽어 그리는 5종 — 첨부 스냅샷도 뷰어 리포트도 없으면 0원 표가 떠서 안내로 대신한다.
// 나머지 4종은 하드코딩 정적 마크업이라 데이터와 무관하게 그대로 그린다
const DATA_DRIVEN_SLIDES = {
  BalanceSheetSlide:        'asset_statement',
  CashFlowSlide:            'cash_flow',
  FinancialIndicatorsSlide: 'financial-indicators',
  FinancialGoalSlide:       'financial-goal-result',
  LifetimeCashflowSlide:    'lifetime-cashflow',
}

// 내용만큼 세로로 늘어나는 슬라이드 — .slide 가 min-height:100% + overflow-y:auto 라
// 본문에서 고정 높이 상자에 넣으면 차트 절반이 잘린 채 스크롤도 안 된다.
// 나머지 슬라이드는 height:100% + overflow:hidden 이라 고정 높이 상자가 있어야 제대로 그려진다
const AUTO_HEIGHT_SLIDES = new Set([
  'BalanceSheetSlide',
  'CashFlowSlide',
  'FinancialIndicatorsSlide',
])

function isAutoHeightSlide(section) {
  return AUTO_HEIGHT_SLIDES.has(section.name)
}

const reportStore = useReportStore()

// 판정은 화면에서 한다 — 컴포넌트는 data가 없으면 store를 보는 기존 동작을 유지한다
function hasSlideData(section) {
  const reportKey = DATA_DRIVEN_SLIDES[section.name]
  if (!reportKey) return true
  return !!section.data || !!reportStore.reports?.[reportKey]
}

const route  = useRoute()
const router = useRouter()

const postId = computed(() => Number(route.params.id))

const post = computed(() =>
  posts.value.find((p) => p.id === postId.value)
)

// 상세 조회 실패 응답 — 404(삭제/오타)와 그 외 오류(500·타임아웃)를 구분한다
const loadError = ref(null)

// 404이거나 끝내 글을 얻지 못한 경우 → '존재하지 않는 글'
const isMissing = computed(() => loadError.value?.response?.status === 404 || !post.value)

// 404 이외의 실패 → 목록에서 넘어온 캐시본이 최신인 척하지 않도록 본문 대신 오류를 보여준다
const isLoadFailed = computed(
  () => !!loadError.value && loadError.value.response?.status !== 404,
)

// 목록 경유 시 즉시 렌더 + 백그라운드 갱신, 직접 진입 시 서버에서 로드
async function loadPost() {
  try {
    await communityStore.fetchPost(postId.value)
    // 성공했을 때만 오류를 지운다 — 재시도 중에는 오류 화면을 유지한다
    loadError.value = null
  } catch (err) {
    // 상세를 못 받았으면 댓글도 요청하지 않는다
    loadError.value = err
    return
  }
  await loadComments()
}

onMounted(loadPost)

// 작성자 메타 줄 — 빈 밴드가 있어도 ' · · ' 없이 이어붙인다
const metaLine = computed(() =>
  post.value
    ? [post.value.age, post.value.asset, post.value.income].filter(Boolean).join(' · ')
    : ''
)

// ─── Like ─────────────────────────────────────────────────
const likeError = ref('')

async function onToggleLike() {
  if (!post.value) return
  if (!localStorage.getItem('access_token')) {
    router.push('/login')
    return
  }
  likeError.value = ''
  try {
    await communityStore.toggleLike(post.value.id)
  } catch {
    likeError.value = '좋아요 처리에 실패했습니다. 잠시 후 다시 시도해 주세요.'
  }
}

// ─── Delete post ──────────────────────────────────────────
// 글 삭제는 본문·댓글을 통째로 잃는 동작이라 인라인 2단계 확인을 거친다
const deleteConfirming = ref(false)
const postDeleting     = ref(false)
const deleteError      = ref('')

// 삭제 성공 직후 스토어가 목록에서 글을 지우므로,
// 이동 전까지 '존재하지 않는 게시글' 화면이 번쩍이지 않도록 별도 상태로 덮는다
const isDeleted = computed(() => postDeleting.value && !post.value)

function askDeletePost() {
  deleteError.value      = ''
  deleteConfirming.value = true
}

function cancelDeletePost() {
  if (postDeleting.value) return
  deleteConfirming.value = false
}

async function confirmDeletePost() {
  if (postDeleting.value || !post.value) return
  const id = post.value.id
  postDeleting.value = true
  deleteError.value  = ''
  try {
    await communityStore.removePost(id)
  } catch (err) {
    // 403(작성자 아님)도 성공처럼 보이지 않게 이동하지 않고 오류를 남긴다
    deleteError.value =
      err?.response?.status === 403
        ? '작성자만 삭제할 수 있습니다.'
        : '글 삭제에 실패했습니다. 잠시 후 다시 시도해 주세요.'
    deleteConfirming.value = false
    postDeleting.value     = false
    return
  }
  // 성공 시 postDeleting을 되돌리지 않는다 — 이동 전 isDeleted 화면을 유지한다
  router.push('/community')
}

// ─── Comments ─────────────────────────────────────────────
const commentText       = ref('')
const commentError      = ref('')
const commentSubmitting = ref(false)
const commentLoadError  = ref('')

// 댓글 조회 실패는 본문과 별개로 댓글 영역에서 알리고 재시도할 수 있게 한다
async function loadComments() {
  commentLoadError.value = ''
  try {
    await communityStore.fetchComments(postId.value)
  } catch {
    commentLoadError.value = '댓글을 불러오지 못했습니다.'
  }
}

async function submitComment() {
  if (commentSubmitting.value) return
  const text = commentText.value.trim()
  if (!text) { commentError.value = '댓글을 입력해 주세요.'; return }
  // axios 인터셉터가 401을 하드 리다이렉트하므로 API 호출 전에 토큰을 확인한다
  if (!localStorage.getItem('access_token')) {
    commentError.value = '로그인이 필요합니다.'
    return
  }
  commentSubmitting.value = true
  try {
    await communityStore.addComment(postId.value, text)
    commentText.value  = ''
    commentError.value = ''
  } catch {
    commentError.value = '댓글 등록에 실패했습니다. 잠시 후 다시 시도해 주세요.'
  } finally {
    commentSubmitting.value = false
  }
}

async function removeComment(commentId) {
  try {
    await communityStore.removeComment(postId.value, commentId)
  } catch {
    commentError.value = '댓글 삭제에 실패했습니다.'
  }
}
</script>

<template>
  <!-- 삭제 직후 목록으로 이동하기 전까지 — not-found 플래시를 막는다 -->
  <div v-if="isDeleted" class="not-found">
    <p>게시글을 삭제했습니다.</p>
  </div>

  <!-- 로딩 중에는 not-found 플래시를 막는다 -->
  <div v-else-if="!post && detailLoading" class="not-found">
    <p>게시글을 불러오는 중...</p>
  </div>

  <!-- 404 이외의 실패 — 캐시된 글을 최신인 것처럼 보여주지 않는다 -->
  <div v-else-if="isLoadFailed" class="not-found">
    <p>게시글을 불러오지 못했습니다.</p>
    <div class="not-found-actions">
      <button class="retry-btn" :disabled="detailLoading" @click="loadPost">
        {{ detailLoading ? '불러오는 중…' : '다시 시도' }}
      </button>
      <button class="back-btn" @click="router.push('/community')">목록으로</button>
    </div>
  </div>

  <div v-else-if="isMissing" class="not-found">
    <p>존재하지 않는 게시글입니다.</p>
    <button class="back-btn" @click="router.push('/community')">목록으로</button>
  </div>

  <div v-else class="post-page">
    <div class="content-wrap">

      <!-- Back -->
      <button class="back-btn" @click="router.push('/community')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
          <path d="M19 12H5M12 5l-7 7 7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        목록으로
      </button>

      <!-- Article -->
      <article class="post-card">

        <!-- Category + time + 내 글 액션 -->
        <div class="post-top">
          <span class="category-badge" :style="CATEGORY_STYLE[post.category]">{{ post.category }}</span>
          <div class="post-actions">
            <span class="post-time">{{ post.createdAt }}</span>

            <!-- 내 글에만 노출. 댓글의 텍스트형 삭제 버튼과 섞이지 않도록 테두리 버튼으로 구분한다 -->
            <template v-if="post.isMine">
              <button v-if="!deleteConfirming" class="post-delete-btn" @click="askDeletePost">글 삭제</button>
              <span v-else class="post-delete-confirm">
                <span class="post-delete-ask">정말 삭제할까요?</span>
                <button class="post-delete-yes" :disabled="postDeleting" @click="confirmDeletePost">
                  {{ postDeleting ? '삭제 중…' : '삭제' }}
                </button>
                <button class="post-delete-no" :disabled="postDeleting" @click="cancelDeletePost">취소</button>
              </span>
            </template>
          </div>
        </div>

        <p v-if="deleteError" class="post-delete-error">{{ deleteError }}</p>

        <!-- Title -->
        <h1 class="post-title">{{ post.title }}</h1>

        <!-- Author -->
        <div class="author-row">
          <span class="author-avatar">{{ post.author[0] }}</span>
          <div class="author-info">
            <span class="author-name">{{ post.author }}</span>
            <span class="author-meta">{{ metaLine }}</span>
          </div>
        </div>

        <!-- Stats -->
        <div class="stats-bar">
          <span class="stat">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" stroke="currentColor" stroke-width="1.8"/>
              <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8"/>
            </svg>
            조회 {{ formatViews(post.views) }}
          </span>
          <span class="stat">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
            </svg>
            댓글 {{ post.comments }}
          </span>
        </div>

        <div class="divider"></div>

        <!-- Sections -->
        <div class="post-content">
          <template v-for="(section, i) in post.sections" :key="i">

            <!-- Text section -->
            <p v-if="section.type === 'text'" class="text-section">{{ section.content }}</p>

            <!-- Embedded component section -->
            <div v-else-if="section.type === 'component'" class="component-section">
              <div class="component-caption">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
                  <rect x="3" y="3" width="18" height="18" rx="2" stroke="currentColor" stroke-width="1.8"/>
                  <path d="M3 9h18M9 21V9" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
                </svg>
                {{ section.caption }}
              </div>
              <div
                class="component-wrap"
                :class="{ 'component-wrap--auto': isAutoHeightSlide(section) }"
              >
                <component
                  :is="COMPONENT_MAP[section.name]"
                  v-if="hasSlideData(section)"
                  :data="section.data ?? null"
                />
                <p v-else class="component-empty">작성자가 첨부한 재무 정보가 없습니다</p>
              </div>
            </div>

          </template>
        </div>

        <div class="divider"></div>

        <!-- Like -->
        <div class="like-area">
          <button
            class="like-btn"
            :class="{ liked: post.liked }"
            :disabled="communityStore.isLikePending(post.id)"
            @click="onToggleLike"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path
                d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"
                :fill="post.liked ? 'currentColor' : 'none'"
                stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
              />
            </svg>
            {{ post.liked ? '좋아요 취소' : '좋아요' }}
            <span class="like-count">{{ post.likes }}</span>
          </button>
        </div>

        <p v-if="likeError" class="like-error">{{ likeError }}</p>

      </article>

      <!-- Comments -->
      <section class="comment-section">
        <h2 class="comment-title">댓글 {{ post.comments }}개</h2>

        <div class="comment-input-wrap">
          <span class="my-avatar">나</span>
          <div class="comment-input-area">
            <textarea
              v-model="commentText"
              class="comment-textarea"
              placeholder="댓글을 입력하세요..."
              rows="3"
              @input="commentError = ''"
            />
            <p v-if="commentError" class="comment-error">{{ commentError }}</p>
            <div class="comment-input-footer">
              <span class="comment-char">{{ commentText.length }}</span>
              <button class="comment-submit-btn" :disabled="commentSubmitting" @click="submitComment">
                {{ commentSubmitting ? '등록 중…' : '등록' }}
              </button>
            </div>
          </div>
        </div>

        <!-- 댓글만 못 불러온 경우 — 본문은 그대로 두고 댓글 영역에서 재시도한다 -->
        <div v-if="commentLoadError" class="comment-load-error">
          <p>{{ commentLoadError }}</p>
          <button class="retry-btn" @click="loadComments">다시 시도</button>
        </div>

        <div v-else-if="post.commentList.length" class="comment-list">
          <div v-for="c in post.commentList" :key="c.id" class="comment-item">
            <span class="comment-avatar">{{ c.author[0] }}</span>
            <div class="comment-body">
              <div class="comment-meta">
                <span class="comment-author">{{ c.author }}</span>
                <span class="comment-time">{{ c.createdAt }}</span>
                <button v-if="c.isMine" class="comment-delete-btn" @click="removeComment(c.id)">삭제</button>
              </div>
              <p class="comment-text">{{ c.text }}</p>
            </div>
          </div>
        </div>

        <div v-else class="no-comments">첫 번째 댓글을 남겨보세요.</div>
      </section>

    </div>
  </div>
</template>

<style scoped>
/* ── Page ─────────────────────────────────────────── */
.post-page {
  min-height: calc(100vh - 80px);
  background: #f7f8fc;
  padding: 36px 24px 60px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

.content-wrap {
  max-width: 720px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.not-found {
  min-height: calc(100vh - 80px);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  font-size: 15px;
  color: #6b7280;
}

.not-found-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* ── Retry ───────────────────────────────────────── */
.retry-btn {
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

.retry-btn:hover { border-color: #5e5ce6; color: #5e5ce6; }
.retry-btn:disabled { opacity: 0.6; cursor: default; border-color: #e5e7eb; color: #6b7280; }

/* ── Back ────────────────────────────────────────── */
.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: none;
  border: none;
  font-size: 13px;
  color: #6b7280;
  cursor: pointer;
  padding: 0;
  transition: color 0.15s;
}

.back-btn:hover { color: #5e5ce6; }

/* ── Article ─────────────────────────────────────── */
.post-card {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 16px;
  padding: 32px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.post-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.category-badge {
  display: inline-block;
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
}

.post-time { font-size: 13px; color: #9ca3af; }

/* ── Post delete ─────────────────────────────────── */
.post-actions { display: flex; align-items: center; gap: 12px; }

.post-delete-btn {
  padding: 5px 12px;
  background: #fff;
  border: 1.5px solid #fecdd3;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 600;
  color: #f43f5e;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s;
}

.post-delete-btn:hover { background: #fff1f3; border-color: #f43f5e; }

.post-delete-confirm { display: flex; align-items: center; gap: 8px; }

.post-delete-ask { font-size: 12px; font-weight: 600; color: #dc2626; white-space: nowrap; }

.post-delete-yes {
  padding: 5px 12px;
  background: #f43f5e;
  border: 1.5px solid #f43f5e;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 600;
  color: #fff;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.12s;
}

.post-delete-yes:hover { background: #e11d48; }

.post-delete-no {
  padding: 5px 12px;
  background: #fff;
  border: 1.5px solid #e5e7eb;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 600;
  color: #6b7280;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s;
}

.post-delete-no:hover { border-color: #9ca3af; color: #374151; }

.post-delete-yes:disabled,
.post-delete-no:disabled { opacity: 0.6; cursor: default; }
.post-delete-yes:disabled:hover { background: #f43f5e; }
.post-delete-no:disabled:hover { border-color: #e5e7eb; color: #6b7280; }

.post-delete-error { font-size: 12px; color: #dc2626; margin: 0; text-align: right; }

.post-title {
  font-size: 22px;
  font-weight: 800;
  color: #111827;
  line-height: 1.45;
  letter-spacing: -0.3px;
  margin: 0;
}

.author-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.author-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: #5e5ce6;
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.author-info { display: flex; flex-direction: column; gap: 2px; }
.author-name { font-size: 14px; font-weight: 600; color: #111827; }
.author-meta { font-size: 12px; color: #9ca3af; }

.stats-bar { display: flex; gap: 16px; }

.stat {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 13px;
  color: #9ca3af;
}

.divider { height: 1px; background: #f3f4f6; }

/* ── Content Sections ────────────────────────────── */
.post-content {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.text-section {
  font-size: 15px;
  color: #374151;
  line-height: 1.85;
  margin: 0;
  white-space: pre-line;
  word-break: break-word;
}

/* Embedded component */
.component-section {
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  overflow: hidden;
  background: #f9fafb;
}

.component-caption {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 16px;
  font-size: 12px;
  font-weight: 600;
  color: #6b7280;
  background: #f3f4f6;
  border-bottom: 1px solid #e5e7eb;
  letter-spacing: 0.02em;
}

.component-wrap {
  height: 220px;
  overflow: hidden;
  background: #ffffff;
}

/* 고정 높이 슬라이드는 좁은 화면에서 요약 카드가 세로로 접히며 차트 자리가 사라진다 — 상자를 더 준다 */
@media (max-width: 600px) {
  .component-wrap:not(.component-wrap--auto) {
    height: 420px;
  }
}

/* 늘어나는 슬라이드는 높이를 내용에 맡겨 전부 보여준다.
   커뮤니티 본문 전용 override — 대시보드·AI·발표 화면은 각자 고정 높이 컨테이너를 쓴다 */
.component-wrap--auto {
  height: auto;
  min-height: 220px;
  overflow: visible;
}

/* 부모가 auto 가 되면 .slide 의 min-height:100% 가 0 이 되고
   overflow-y:auto 는 이중 스크롤을 만든다 — 이 화면에서만 풀어준다 */
.component-wrap--auto :deep(.slide) {
  min-height: 220px;
  overflow-y: visible;
}

.component-wrap--auto .component-empty {
  min-height: 220px;
}

.component-empty {
  height: 100%;
  margin: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  color: #9ca3af;
}

/* ── Like ────────────────────────────────────────── */
.like-area { display: flex; justify-content: center; }

.like-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 28px;
  border: 1.5px solid #e5e7eb;
  border-radius: 999px;
  background: #fff;
  font-size: 14px;
  font-weight: 500;
  color: #6b7280;
  cursor: pointer;
  transition: all 0.15s;
}

.like-btn:hover { border-color: #f43f5e; color: #f43f5e; }

.like-btn.liked {
  background: #fff1f3;
  border-color: #f43f5e;
  color: #f43f5e;
  font-weight: 600;
}

.like-btn:disabled { opacity: 0.6; cursor: default; }
.like-btn:disabled:hover { border-color: #e5e7eb; color: #6b7280; }
.like-btn.liked:disabled:hover { border-color: #f43f5e; color: #f43f5e; }

.like-count { font-weight: 700; }

.like-error { font-size: 12px; color: #dc2626; margin: 0; text-align: center; }

/* ── Comments ────────────────────────────────────── */
.comment-section {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 16px;
  padding: 28px 32px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.comment-title {
  font-size: 16px;
  font-weight: 700;
  color: #111827;
  margin: 0;
}

.comment-input-wrap { display: flex; gap: 12px; align-items: flex-start; }

.my-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #5e5ce6;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 2px;
}

.comment-input-area { flex: 1; display: flex; flex-direction: column; gap: 6px; }

.comment-textarea {
  width: 100%;
  padding: 10px 14px;
  border: 1.5px solid #e5e7eb;
  border-radius: 10px;
  font-size: 14px;
  color: #111827;
  background: #f9fafb;
  outline: none;
  resize: none;
  box-sizing: border-box;
  font-family: inherit;
  line-height: 1.6;
  transition: border-color 0.15s, background 0.15s;
}

.comment-textarea:focus { border-color: #5e5ce6; background: #fff; }
.comment-textarea::placeholder { color: #9ca3af; }

.comment-error { font-size: 12px; color: #dc2626; margin: 0; }

.comment-input-footer { display: flex; align-items: center; justify-content: space-between; }

.comment-char { font-size: 12px; color: #9ca3af; }

.comment-submit-btn {
  padding: 7px 18px;
  background: #5e5ce6;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #fff;
  cursor: pointer;
  transition: background 0.12s;
}

.comment-submit-btn:hover { background: #4c4ad4; }

.comment-submit-btn:disabled {
  background: #c7c6f2;
  cursor: default;
}

.comment-load-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 24px 0;
  border-top: 1px solid #f3f4f6;
}

.comment-load-error p {
  font-size: 14px;
  color: #dc2626;
  margin: 0;
}

.comment-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
  border-top: 1px solid #f3f4f6;
  padding-top: 20px;
}

.comment-item { display: flex; gap: 12px; align-items: flex-start; }

.comment-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #e5e7eb;
  color: #6b7280;
  font-size: 12px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.comment-body { flex: 1; display: flex; flex-direction: column; gap: 4px; }

.comment-meta { display: flex; align-items: center; gap: 8px; }

.comment-author { font-size: 13px; font-weight: 600; color: #374151; }
.comment-time { font-size: 12px; color: #9ca3af; }

.comment-delete-btn {
  margin-left: auto;
  background: none;
  border: none;
  font-size: 12px;
  color: #9ca3af;
  cursor: pointer;
  padding: 0;
  transition: color 0.15s;
}

.comment-delete-btn:hover { color: #dc2626; }

.comment-text {
  font-size: 14px;
  color: #4b5563;
  line-height: 1.6;
  margin: 0;
  white-space: pre-line;
}

.no-comments {
  text-align: center;
  padding: 24px 0;
  font-size: 14px;
  color: #9ca3af;
  border-top: 1px solid #f3f4f6;
}
</style>
