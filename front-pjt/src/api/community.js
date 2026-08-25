import api from '@/api'

export const getPosts   = (params)   => api.get('/posts/', { params })
export const createPost = (data)     => api.post('/posts/', data)
export const getPost    = (pk)       => api.get(`/posts/${pk}/`)
export const updatePost = (pk, data) => api.patch(`/posts/${pk}/`, data)
export const deletePost = (pk)       => api.delete(`/posts/${pk}/`)
export const toggleLike = (pk)       => api.post(`/posts/${pk}/like/`)
