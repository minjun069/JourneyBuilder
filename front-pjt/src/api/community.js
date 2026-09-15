import { communityApi } from '@/api'

export const getPosts = (params) => communityApi.get('/posts/', { params })
export const createPost = (data) => communityApi.post('/posts/', data)
export const getPost = (pk) => communityApi.get(`/posts/${pk}/`)
export const updatePost = (pk, data) => communityApi.patch(`/posts/${pk}/`, data)
export const deletePost = (pk) => communityApi.delete(`/posts/${pk}/`)
export const toggleLike = (pk) => communityApi.post(`/posts/${pk}/like/`)
