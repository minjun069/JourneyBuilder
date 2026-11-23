import { communityApi } from '@/api'

export const getPosts = (params) => communityApi.get('/posts/', { params })
export const createPost = (data) => communityApi.post('/posts/', data)
export const getPost = (pk) => communityApi.get(`/posts/${pk}/`)
export const updatePost = (pk, data) => communityApi.patch(`/posts/${pk}/`, data)
export const deletePost = (pk) => communityApi.delete(`/posts/${pk}/`)
export const toggleLike = (pk) => communityApi.post(`/posts/${pk}/like/`)
export const getComments = (pk) => communityApi.get(`/posts/${pk}/comments/`)
export const createComment = (pk, data) => communityApi.post(`/posts/${pk}/comments/`, data)
export const deleteComment = (pk) => communityApi.delete(`/comments/${pk}/`)
