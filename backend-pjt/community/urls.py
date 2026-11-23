from django.urls import path

from .views import (
    CommentDetailView,
    PostCommentListCreateView,
    PostDetailView,
    PostLikeView,
    PostListCreateView,
)

urlpatterns = [
    path('posts/', PostListCreateView.as_view(), name='post-list-create'),
    path('posts/<int:pk>/', PostDetailView.as_view(), name='post-detail'),
    path('posts/<int:pk>/like/', PostLikeView.as_view(), name='post-like'),
    path('posts/<int:pk>/comments/', PostCommentListCreateView.as_view(), name='post-comments'),
    path('comments/<int:pk>/', CommentDetailView.as_view(), name='comment-detail'),
]
