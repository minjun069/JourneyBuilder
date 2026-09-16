from django.db import models
from django.db.models import F
from django.conf import settings
from django.utils import timezone


# 삭제된 게시물 자동 제외
class PostManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class Post(models.Model):
    class Category(models.TextChoices):
        FINANCIAL_PLAN = '재무계획', '재무계획'
        INVESTMENT     = '투자', '투자'
        RETIREMENT     = '은퇴준비', '은퇴준비'
        SAVING         = '절약/지출', '절약/지출'
        DEBT           = '대출/부채', '대출/부채'
        ETC            = '기타', '기타'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='posts'
    )
    title        = models.CharField(max_length=200)
    content      = models.TextField()
    category     = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.ETC,
    )
    is_anonymous = models.BooleanField(default=False)
    like_count   = models.PositiveIntegerField(default=0)
    view_count   = models.PositiveIntegerField(default=0)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)
    deleted_at   = models.DateTimeField(null=True, blank=True)

    objects     = PostManager()  # 삭제된 글 제외-기본 조회
    all_objects = models.Manager()  # 삭제된 글 포함

    class Meta:
        db_table = 'community_posts'
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def increment_view_count(self):
        # F()-> 여러 명이 게시글 동시에 조회하는 경우 오류 방지 가능
        Post.objects.filter(pk=self.pk).update(view_count=F('view_count') + 1)

    # 실제 DB 삭제하지 않음
    def soft_delete(self):
        self.deleted_at = timezone.now()
        self.save(update_fields=['deleted_at'])


class Like(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='likes'
    )
    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name='likes'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'community_likes'
        # 한 사용자의 좋아요 중복 방지
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'post'],
                name='unique_user_post_like'
            )
        ]

    def __str__(self):
        return f"{self.user_id} → {self.post_id}"