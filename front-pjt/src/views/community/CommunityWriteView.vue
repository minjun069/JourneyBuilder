<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useCommunityStore, CATEGORIES } from './communityStore.js'
import { useReportStore } from '@/stores/report'

const communityStore = useCommunityStore()
const { addPost } = communityStore

const reportStore = useReportStore()

const router = useRouter()

const form = ref({ category: '재무계획', title: '', body: '' })
const error = ref('')
const submitting = ref(false)

// ─── 재무 정보 첨부 ────────────────────────────────────────
// 첨부할 수 있는 것은 리포트 값을 그려내는 5종뿐이다(나머지 4종은 정적 마크업)
const ATTACHABLE_SLIDES = [
  { name: 'BalanceSheetSlide',        reportKey: 'asset_statement',       caption: '재무상태표' },
  { name: 'CashFlowSlide',            reportKey: 'cash_flow',             caption: '현금흐름표' },
  { name: 'FinancialIndicatorsSlide', reportKey: 'financial-indicators',  caption: '가계재무지표' },
  { name: 'FinancialGoalSlide',       reportKey: 'financial-goal-result', caption: '재무목표 요약' },
  { name: 'LifetimeCashflowSlide',    reportKey: 'lifetime-cashflow',     caption: '생애현금흐름 및 생애순자산' },
]

const attachOpen     = ref(false)
const attachLoading  = ref(false)
const attachFailed   = ref(false)
const attachTried    = ref(false)
// 자기 재무를 공개하는 동작이라 기본값은 '첨부 안 함'이다
const selectedSlide  = ref(null)

// 글쓰기 화면은 마운트 시 요청 0건을 유지한다 — 첨부 영역을 처음 펼칠 때만 한 번 조회한다.
// generateReports/ensureReports는 서버에서 리포트를 재생성하므로 쓰지 않는다
async function toggleAttachPanel() {
  attachOpen.value = !attachOpen.value
  if (!attachOpen.value) return
  if (attachTried.value || attachLoading.value || reportStore.reports) return

  attachTried.value   = true
  attachLoading.value = true
  attachFailed.value  = false
  try {
    await reportStore.loadLatestReports()
    // store는 조회 실패를 예외 대신 error로 남긴다
    attachFailed.value = !!reportStore.error
  } finally {
    attachLoading.value = false
  }
}

// 작성자가 실제로 가진 표만 칩으로 내놓는다
const availableSlides = computed(() =>
  ATTACHABLE_SLIDES.filter((slide) => reportStore.hasReport(slide.reportKey)),
)

// ready 세션이 없거나 리포트가 아직 없는 경우 — 조회 실패와는 구분해서 안내한다
const attachEmpty = computed(() =>
  !attachLoading.value && !attachFailed.value && availableSlides.value.length === 0,
)

function selectSlide(name) {
  // 라디오 성격 — 한 번에 하나만, 같은 칩을 다시 누르면 해제
  selectedSlide.value = selectedSlide.value === name ? null : name
}

// 첨부가 선택된 경우에만 보낼 sections를 만든다. 리포트가 사라졌으면 조용히 첨부 없이 보낸다
function buildSections(content) {
  const slide = ATTACHABLE_SLIDES.find((s) => s.name === selectedSlide.value)
  const snapshot = slide ? reportStore.reports?.[slide.reportKey] : null
  if (!slide || !snapshot) return null
  return [
    { type: 'text', content },
    {
      type: 'component',
      name: slide.name,
      caption: slide.caption,
      // 반응형 객체를 그대로 싣지 않도록 평범한 값 사본을 만든다
      data: JSON.parse(JSON.stringify(snapshot)),
    },
  ]
}

async function submit() {
  if (!form.value.title.trim()) { error.value = '제목을 입력해 주세요.'; return }
  if (!form.value.body.trim())  { error.value = '내용을 입력해 주세요.'; return }
  if (submitting.value) return

  submitting.value = true
  error.value = ''
  try {
    const content  = form.value.body.trim()
    const sections = buildSections(content)
    const created = await addPost({
      title: form.value.title.trim(),
      content,
      category: form.value.category,
      // 첨부가 없으면 sections를 아예 보내지 않는다(서버가 []로 저장, 매퍼가 본문 1섹션을 합성)
      ...(sections ? { sections } : {}),
    })
    router.push(`/community/${created.id}`)
  } catch (err) {
    if (err.response?.status === 401) {
      error.value = '로그인이 필요합니다.'
    } else {
      const data = err.response?.data
      error.value = data
        ? Object.values(data).flat().join(' ')
        : '글 등록에 실패했습니다. 잠시 후 다시 시도해 주세요.'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="write-page">
    <div class="content-wrap">

      <!-- Header -->
      <div class="page-header">
        <button class="back-btn" @click="router.push('/community')">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
            <path d="M19 12H5M12 5l-7 7 7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          커뮤니티
        </button>
        <h1 class="page-title">새 글 작성</h1>
      </div>

      <!-- Form Card -->
      <div class="form-card">

        <div class="form-group">
          <label class="form-label">카테고리</label>
          <div class="category-grid">
            <button
              v-for="cat in CATEGORIES"
              :key="cat"
              type="button"
              class="cat-btn"
              :class="{ active: form.category === cat }"
              @click="form.category = cat"
            >
              {{ cat }}
            </button>
          </div>
        </div>

        <div class="form-group">
          <label class="form-label">제목</label>
          <input
            v-model="form.title"
            type="text"
            class="form-input"
            placeholder="제목을 입력하세요"
            maxlength="100"
            @input="error = ''"
          />
          <div class="char-count">{{ form.title.length }} / 100</div>
        </div>

        <div class="form-group">
          <label class="form-label">내용</label>
          <textarea
            v-model="form.body"
            class="form-textarea"
            placeholder="재무 경험, 질문, 노하우를 자유롭게 작성해 주세요."
            rows="16"
            @input="error = ''"
          />
        </div>

        <div class="form-group">
          <label class="form-label">재무 정보 첨부 (선택)</label>
          <button type="button" class="attach-toggle" @click="toggleAttachPanel">
            {{ attachOpen ? '첨부 영역 접기' : '내 재무 정보 첨부하기' }}
          </button>

          <div v-if="attachOpen" class="attach-panel">
            <!-- 조회 실패해도 글 등록은 막지 않는다 -->
            <p v-if="attachLoading" class="attach-note">불러오는 중…</p>
            <p v-else-if="attachFailed" class="attach-note">재무 정보를 불러오지 못했습니다.</p>
            <p v-else-if="attachEmpty" class="attach-note">
              첨부하려면 AI 재무 진단을 먼저 완료해 주세요.
              <RouterLink class="attach-link" to="/ai">AI 재무 진단 하러 가기</RouterLink>
            </p>

            <template v-else>
              <div class="category-grid">
                <button
                  v-for="slide in availableSlides"
                  :key="slide.name"
                  type="button"
                  class="cat-btn"
                  :class="{ active: selectedSlide === slide.name }"
                  @click="selectSlide(slide.name)"
                >
                  {{ slide.caption }}
                </button>
              </div>
              <p class="attach-note">
                {{
                  selectedSlide
                    ? '선택한 표의 수치가 글에 함께 공개됩니다.'
                    : '하나를 고르면 그 표가 글에 함께 실립니다. 고르지 않으면 첨부하지 않습니다.'
                }}
              </p>
            </template>
          </div>
        </div>

        <p v-if="error" class="form-error">{{ error }}</p>

        <div class="form-actions">
          <button class="btn-cancel" @click="router.push('/community')">취소</button>
          <button class="btn-submit" :disabled="submitting" @click="submit">
            {{ submitting ? '등록 중…' : '작성하기' }}
          </button>
        </div>

      </div>
    </div>
  </div>
</template>

<style scoped>
.write-page {
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
  gap: 20px;
}

/* ── Header ─────────────────────────────────────── */
.page-header {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

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

.page-title {
  font-size: 24px;
  font-weight: 800;
  color: #111827;
  letter-spacing: -0.4px;
  margin: 0;
}

/* ── Form Card ──────────────────────────────────── */
.form-card {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 16px;
  padding: 32px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.form-label {
  font-size: 13px;
  font-weight: 700;
  color: #374151;
  letter-spacing: 0.02em;
}

/* Category chips */
.category-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.cat-btn {
  padding: 6px 14px;
  border: 1.5px solid #e5e7eb;
  border-radius: 999px;
  background: #f9fafb;
  font-size: 13px;
  color: #6b7280;
  cursor: pointer;
  transition: all 0.15s;
}

.cat-btn:hover { border-color: #5e5ce6; color: #5e5ce6; }

.cat-btn.active {
  background: #5e5ce6;
  border-color: #5e5ce6;
  color: #fff;
  font-weight: 600;
}

/* Attach panel */
.attach-toggle {
  align-self: flex-start;
  padding: 7px 14px;
  border: 1.5px solid #e5e7eb;
  border-radius: 10px;
  background: #f9fafb;
  font-size: 13px;
  color: #6b7280;
  cursor: pointer;
  transition: all 0.15s;
}

.attach-toggle:hover { border-color: #5e5ce6; color: #5e5ce6; }

.attach-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 16px;
  border: 1px solid #f3f4f6;
  border-radius: 12px;
  background: #fafafd;
}

.attach-note {
  font-size: 12px;
  color: #6b7280;
  line-height: 1.6;
  margin: 0;
}

.attach-link { color: #5e5ce6; font-weight: 600; text-decoration: none; }
.attach-link:hover { text-decoration: underline; }

/* Inputs */
.form-input,
.form-textarea {
  width: 100%;
  padding: 12px 16px;
  border: 1.5px solid #e5e7eb;
  border-radius: 10px;
  font-size: 15px;
  color: #111827;
  background: #f9fafb;
  outline: none;
  box-sizing: border-box;
  font-family: inherit;
  transition: border-color 0.15s, background 0.15s;
}

.form-input:focus,
.form-textarea:focus {
  border-color: #5e5ce6;
  background: #fff;
}

.form-input::placeholder,
.form-textarea::placeholder { color: #9ca3af; }

.form-textarea {
  resize: vertical;
  min-height: 280px;
  line-height: 1.7;
}

.char-count {
  font-size: 12px;
  color: #9ca3af;
  text-align: right;
}

.form-error {
  font-size: 13px;
  color: #dc2626;
  margin: 0;
}

/* Buttons */
.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 8px;
  border-top: 1px solid #f3f4f6;
}

.btn-cancel {
  padding: 11px 22px;
  background: #f3f4f6;
  border: none;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 500;
  color: #374151;
  cursor: pointer;
  transition: background 0.12s;
}

.btn-cancel:hover { background: #e5e7eb; }

.btn-submit {
  padding: 11px 28px;
  background: #5e5ce6;
  border: none;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 600;
  color: #fff;
  cursor: pointer;
  transition: background 0.12s;
}

.btn-submit:hover { background: #4c4ad4; }
</style>
