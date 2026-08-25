from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Like, Post
from .serializers import PostCreateUpdateSerializer, PostDetailSerializer, PostListSerializer


class PostListCreateView(APIView):
    # DRF의 권한 설정
    # 조회(GET)는 누구나 가능
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get(self, request):
        posts = Post.objects.all()
        serializer = PostListSerializer(posts, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = PostCreateUpdateSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user)
            # 저장이 끝나면 serializer.instance 생성됨
            # : 실제로 생성된 Post 객체
            return Response(PostDetailSerializer(serializer.instance).data, status=status.HTTP_201_CREATED)
            # 모델 객체 -> JSON 응답
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PostDetailView(APIView):
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_object(self, pk):
        return get_object_or_404(Post, pk=pk)

    def get(self, request, pk):
        post = self.get_object(pk)
        post.increment_view_count()
        # DB와 메모리의 view_count 값 동일하게 업데이트하기 위해
        post.refresh_from_db(fields=['view_count'])
        serializer = PostDetailSerializer(post)
        return Response(serializer.data)

    def patch(self, request, pk):
        post = self.get_object(pk)
        if post.user != request.user:
            return Response(status=status.HTTP_403_FORBIDDEN)
        # partial=True: 일부 필드만 수정 가능
        serializer = PostCreateUpdateSerializer(post, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(PostDetailSerializer(serializer.instance).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        post = self.get_object(pk)
        if post.user != request.user:
            return Response(status=status.HTTP_403_FORBIDDEN)
        post.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PostLikeView(APIView):
    # 로그인한 사용자만 접근 가능
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        post = get_object_or_404(Post, pk=pk)
        with transaction.atomic():
            like, created = Like.objects.get_or_create(user=request.user, post=post)
            # 좋아요 토글 ON
            if created:
                # F()-> 동시에 여러 명이 좋아요 누르는 경우 에러 발생 방지
                Post.objects.filter(pk=pk).update(like_count=F('like_count') + 1)
                post.refresh_from_db(fields=['like_count'])
                return Response({'liked': True, 'like_count': post.like_count}, status=status.HTTP_201_CREATED)
            # 좋아요 토글 OFF
            like.delete()
            Post.objects.filter(pk=pk).update(like_count=F('like_count') - 1)
            post.refresh_from_db(fields=['like_count'])
            return Response({'liked': False, 'like_count': post.like_count}, status=status.HTTP_200_OK)
