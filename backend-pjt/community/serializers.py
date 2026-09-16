from rest_framework import serializers

from .models import Post


class PostListSerializer(serializers.ModelSerializer):
    # 기존 Post 모델에 author 필드 없지만 JSON으로 보내고 싶으므로 새로 생성
    author = serializers.SerializerMethodField()
    # -> DRF: 자동으로 get_author() 호출

    class Meta:
        model = Post
        fields = ['id', 'author', 'title', 'category', 'like_count', 'view_count', 'created_at']

    def get_author(self, obj):
        if obj.is_anonymous:
            return '익명'
        return obj.user.name


class PostDetailSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            'id', 'author', 'title', 'content', 'category', 'is_anonymous',
            'like_count', 'view_count', 'created_at', 'updated_at',
        ]
        read_only_fields = ['like_count', 'view_count', 'created_at', 'updated_at']

    def get_author(self, obj):
        if obj.is_anonymous:
            return '익명'
        return obj.user.name


class PostCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Post
        fields = ['title', 'content', 'category', 'is_anonymous']
