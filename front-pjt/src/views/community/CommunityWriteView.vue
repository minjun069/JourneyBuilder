<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useCommunityStore, CATEGORIES } from './communityStore.js'

const communityStore = useCommunityStore()
const { addPost } = communityStore

const router = useRouter()

const form = ref({ category: '재무계획', title: '', body: '' })
const error = ref('')

function submit() {
  if (!form.value.title.trim()) { error.value = '제목을 입력해 주세요.'; return }
  if (!form.value.body.trim())  { error.value = '내용을 입력해 주세요.'; return }

  const newPost = {
    id: Date.now(),
    category: form.value.category,
    title: form.value.title.trim(),
    body: form.value.body.trim(),
    author: '나',
    age: '30대', asset: '1~3억', income: '5~7천',
    likes: 0, liked: false, comments: 0, views: 0,
    recommended: false,
    createdAt: '방금 전',
    commentList: [],
  }

  addPost(newPost)
  router.push(`/community/${newPost.id}`)
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

        <p v-if="error" class="form-error">{{ error }}</p>

        <div class="form-actions">
          <button class="btn-cancel" @click="router.push('/community')">취소</button>
          <button class="btn-submit" @click="submit">작성하기</button>
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
