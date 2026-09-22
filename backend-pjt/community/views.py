from django.db import transaction
from django.db.models import BooleanField, Count, Exists, F, OuterRef, Q, Value
from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Comment, Like, Post
from .serializers import (
    CommentCreateSerializer,
    CommentSerializer,
    PostCreateUpdateSerializer,
    PostDetailSerializer,
    PostListSerializer,
)
from .services import derive_author_bands


class PostListCreateView(APIView):
    # DRF의 권한 설정
    # 조회(GET)는 누구나 가능
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get(self, request):
        # annotate 는 매니저를 우회하므로 삭제된 댓글은 filter=Q(...) 로 직접 제외
        posts = Post.objects.select_related('user').annotate(
            comment_count=Count(
                'comments', filter=Q(comments__deleted_at__isnull=True)),
        )
        if request.user.is_authenticated:
            # Exists 는 상관 서브쿼리라 Count(filter=Q) 와 조합해도 중복행이 없다
            posts = posts.annotate(liked=Exists(
                Like.objects.filter(post=OuterRef('pk'), user=request.user)))
        else:
            # AnonymousUser 를 Exists 에 넣으면 ValueError — 고정 False 로 대체
            posts = posts.annotate(
                liked=Value(False, output_field=BooleanField()))
        # Django 3.1 부터 GROUP BY 가 생기는 쿼리(위 Count)에서는 Meta.ordering 이 무시된다.
        # 명시하지 않으면 정렬 없는 순서로 나간다 — 시드 글의 동률 created_at 대비 -id 까지 지정한다.
        posts = posts.order_by('-created_at', '-id')
        serializer = PostListSerializer(
            posts, many=True, context={'request': request})
        return Response(serializer.data)

    def post(self, request):
        serializer = PostCreateUpdateSerializer(data=request.data)
        if serializer.is_valid():
            # 작성 시점의 작성자 밴드를 스냅샷으로 저장 (실패 시 '' — 작성은 계속)
            age_band, asset_band, income_band = derive_author_bands(request.user)
            serializer.save(
                user=request.user,
                author_age_band=age_band,
                author_asset_band=asset_band,
                author_income_band=income_band,
            )
            # 저장이 끝나면 serializer.instance 생성됨
            # : 실제로 생성된 Post 객체
            return Response(
                PostDetailSerializer(serializer.instance, context={'request': request}).data,
                status=status.HTTP_201_CREATED,
            )
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
        serializer = PostDetailSerializer(post, context={'request': request})
        return Response(serializer.data)

    def patch(self, request, pk):
        post = self.get_object(pk)
        if post.user != request.user:
            return Response(status=status.HTTP_403_FORBIDDEN)
        # partial=True: 일부 필드만 수정 가능
        # PATCH 는 밴드를 재계산하지 않는다 (작성 시점 스냅샷 유지)
        serializer = PostCreateUpdateSerializer(post, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(
                PostDetailSerializer(serializer.instance, context={'request': request}).data)
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


class PostCommentListCreateView(APIView):
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get(self, request, pk):
        # 소프트 삭제된 글 밑 댓글은 자동 404 (기본 매니저 조회)
        post = get_object_or_404(Post, pk=pk)
        # post.comments 는 기본 매니저 상속 → 삭제된 댓글 자동 제외
        comments = post.comments.select_related('user')
        serializer = CommentSerializer(
            comments, many=True, context={'request': request})
        return Response(serializer.data)

    def post(self, request, pk):
        post = get_object_or_404(Post, pk=pk)
        serializer = CommentCreateSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user, post=post)
            return Response(
                CommentSerializer(serializer.instance, context={'request': request}).data,
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CommentDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        # 기본 매니저 조회 → 이미 삭제된 댓글의 중복 삭제는 404
        comment = get_object_or_404(Comment, pk=pk)
        if comment.user != request.user:
            return Response(status=status.HTTP_403_FORBIDDEN)
        comment.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
