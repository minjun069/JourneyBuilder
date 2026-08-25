import { ref } from 'vue'
import { defineStore } from 'pinia'
import * as communityApi from '@/api/community'

import { MOCK_POSTS } from '../../mock/CommunityData.js'

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
  const posts   = ref(MOCK_POSTS)
  const loading = ref(false)
  const error   = ref(null)

  async function fetchPosts(params) {
    loading.value = true
    error.value   = null
    try {
      const { data } = await communityApi.getPosts(params)
      posts.value = data
    } catch (err) {
      error.value = err
    } finally {
      loading.value = false
    }
  }

  async function addPost(postData) {
    const { data } = await communityApi.createPost(postData)
    posts.value.unshift(data)
    return data
  }

  async function editPost(pk, postData) {
    const { data } = await communityApi.updatePost(pk, postData)
    const idx = posts.value.findIndex(p => p.id === pk)
    if (idx !== -1) posts.value[idx] = data
    return data
  }

  async function removePost(pk) {
    await communityApi.deletePost(pk)
    posts.value = posts.value.filter(p => p.id !== pk)
  }

  async function toggleLike(pk) {
    const { data } = await communityApi.toggleLike(pk)
    const idx = posts.value.findIndex(p => p.id === pk)
    if (idx !== -1) posts.value[idx].like_count = data.like_count
    return data
  }

  return {
    posts, loading, error,
    fetchPosts, addPost, editPost, removePost, toggleLike,
  }
})

export function formatViews(n) {
  return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n)
}
