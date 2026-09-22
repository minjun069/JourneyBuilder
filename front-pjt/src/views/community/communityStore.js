import { ref } from 'vue'
import { defineStore } from 'pinia'
import * as communityApi from '@/api/community'

import { toViewPost, toViewComment, relativeTime, formatViews } from './postMapper.js'

// 하위 호환 re-export (기존에 store에서 import하던 코드 유지)
export { toViewPost, toViewComment, relativeTime, formatViews }

export const CATEGORIES = ['재무계획', '투자', '은퇴준비', '절약/지출', '대출/부채', '기타']

export const CATEGORY_STYLE = {
  '재무계획':  { background: '#eef0ff', color: '#5e5ce6' },
  '투자':      { background: '#dcfce7', color: '#16a34a' },
  '은퇴준비':  { background: '#fef3c7', color: '#d97706' },
  '절약/지출': { background: '#fee2e2', color: '#dc2626' },
  '대출/부채': { background: '#e0f2fe', color: '#0284c7' },
  '기타':      { background: '#f3f4f6', color: '#6b7280' },
}

export const useCommunityStore = defineStore('community', () => {
  const posts         = ref([])
  const loading       = ref(false)
  const detailLoading = ref(false)
  const error         = ref(null)
  // 좋아요 요청이 진행 중인 게시글 id 집합 (중복 요청 방지 + 버튼 비활성화용)
  const likePending   = ref(new Set())

  // route.params.id가 문자열로 들어오므로 모든 액션에서 id를 숫자로 정규화한다
  function findPost(pk) {
    const id = Number(pk)
    return posts.value.find((p) => p.id === id)
  }

  // 상세 응답 upsert — 기존 항목의 commentList는 보존한다
  function upsertPost(viewPost) {
    const idx = posts.value.findIndex((p) => p.id === viewPost.id)
    if (idx !== -1) {
      viewPost.commentList = posts.value[idx].commentList
      posts.value[idx] = viewPost
    } else {
      posts.value.unshift(viewPost)
    }
    return viewPost
  }

  async function fetchPosts(params) {
    loading.value = true
    error.value   = null
    try {
      const { data } = await communityApi.getPosts(params)
      posts.value = data.map(toViewPost)
    } catch (err) {
      error.value = err
    } finally {
      loading.value = false
    }
  }

  async function fetchPost(pk) {
    detailLoading.value = true
    try {
      const { data } = await communityApi.getPost(pk)
      return upsertPost(toViewPost(data))
    } finally {
      detailLoading.value = false
    }
  }

  async function addPost(postData) {
    const { data } = await communityApi.createPost(postData)
    const viewPost = toViewPost(data)
    posts.value.unshift(viewPost)
    return viewPost
  }

  async function editPost(pk, postData) {
    const { data } = await communityApi.updatePost(pk, postData)
    return upsertPost(toViewPost(data))
  }

  async function removePost(pk) {
    await communityApi.deletePost(pk)
    const id = Number(pk)
    posts.value = posts.value.filter((p) => p.id !== id)
  }

  // 좋아요 요청 진행 여부 — 화면에서 버튼 disabled 바인딩에 쓴다
  function isLikePending(pk) {
    return likePending.value.has(Number(pk))
  }

  // 더블클릭 시 두 요청의 응답이 뒤바뀌어 UI와 DB가 어긋나는 것을 막는다.
  // 진행 중인 요청이 있으면 두 번째 토글은 무시하고 null을 돌려준다.
  async function toggleLike(pk) {
    const id = Number(pk)
    if (likePending.value.has(id)) return null

    likePending.value.add(id)
    try {
      const { data } = await communityApi.toggleLike(id)
      const post = findPost(id)
      if (post) {
        post.liked = !!data.liked
        post.likes = data.like_count ?? post.likes
      }
      return data
    } finally {
      // 성공/실패와 무관하게 잠금을 풀어 다음 시도를 허용한다.
      // 낙관적 갱신을 하지 않으므로 실패해도 상태는 그대로 유지된다(호출부에서 안내).
      likePending.value.delete(id)
    }
  }

  // ─── 댓글 ─────────────────────────────────────────────
  async function fetchComments(postId) {
    const { data } = await communityApi.getComments(postId)
    const post = findPost(postId)
    if (post) {
      post.commentList = data.map(toViewComment)
      post.comments    = data.length
    }
    return data
  }

  async function addComment(postId, content) {
    const { data } = await communityApi.createComment(postId, { content })
    const comment = toViewComment(data)
    const post = findPost(postId)
    if (post) {
      post.commentList.push(comment)
      post.comments += 1
    }
    return comment
  }

  async function removeComment(postId, commentId) {
    await communityApi.deleteComment(commentId)
    const post = findPost(postId)
    if (post) {
      post.commentList = post.commentList.filter((c) => c.id !== commentId)
      post.comments = Math.max(0, post.comments - 1)
    }
  }

  return {
    posts, loading, detailLoading, error, likePending,
    fetchPosts, fetchPost, addPost, editPost, removePost, toggleLike, isLikePending,
    fetchComments, addComment, removeComment,
  }
})
