from rest_framework import serializers

from .models import Comment, Like, Post
from .services import mask_display_name

# sections 의 component 타입에서 허용하는 차트 컴포넌트 화이트리스트 (프론트 슬라이드 9종)
SECTION_COMPONENT_WHITELIST = (
    'BalanceSheetSlide',
    'CashFlowSlide',
    'FinancialIndicatorsSlide',
    'FinancialGoalSlide',
    'RetirementFundSlide',
    'RetirementReadinessSlide',
    'PensionScheduleSlide',
    'LifetimeCashflowSlide',
    'RequiredSavingsSlide',
)

# 본문/댓글/섹션 길이 상한 — 목록·상세가 비인증 GET 으로 무제한 조회되므로
# 한 계정이 거대한 본문을 넣어 모든 조회자에게 전송시키는 것을 막는다.
POST_CONTENT_MAX_LENGTH = 5000
COMMENT_CONTENT_MAX_LENGTH = 1000
SECTION_MAX_COUNT = 30
SECTION_TEXT_MAX_LENGTH = 5000


class AuthorBandMixin:
    """작성자 표시 이름과 밴드 3종을 함께 가리는 공통 로직.

    익명 글은 이름뿐 아니라 밴드도 비워야 한다 — 밴드 조합은 사용자별로
    결정적이라, 같은 조합의 실명 글과 대조하면 익명이 풀리기 때문.
    """

    def get_author(self, obj):
        if obj.is_anonymous:
            return '익명'
        return mask_display_name(obj.user.name)

    def get_age_band(self, obj):
        return '' if obj.is_anonymous else obj.author_age_band

    def get_asset_band(self, obj):
        return '' if obj.is_anonymous else obj.author_asset_band

    def get_income_band(self, obj):
        return '' if obj.is_anonymous else obj.author_income_band


class PostListSerializer(AuthorBandMixin, serializers.ModelSerializer):
    # 기존 Post 모델에 author 필드 없지만 JSON으로 보내고 싶으므로 새로 생성
    author = serializers.SerializerMethodField()
    # -> DRF: 자동으로 get_author() 호출
    # annotation 기반 필드는 명시 선언 (annotate 누락 시에도 500 대신 기본값)
    comment_count = serializers.IntegerField(read_only=True)
    liked = serializers.BooleanField(read_only=True, default=False)
    # 익명 글은 밴드도 감춰야 하므로 source= 대신 메서드 필드
    age_band = serializers.SerializerMethodField()
    asset_band = serializers.SerializerMethodField()
    income_band = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            'id', 'author', 'title', 'category', 'content',
            'like_count', 'view_count', 'comment_count',
            'is_recommended', 'liked',
            'age_band', 'asset_band', 'income_band', 'created_at',
        ]


class PostDetailSerializer(AuthorBandMixin, serializers.ModelSerializer):
    author = serializers.SerializerMethodField()
    # annotation 에 의존하지 않는다 (생성/PATCH 응답 재사용 대비)
    liked = serializers.SerializerMethodField()
    comment_count = serializers.SerializerMethodField()
    age_band = serializers.SerializerMethodField()
    asset_band = serializers.SerializerMethodField()
    income_band = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            'id', 'author', 'title', 'content', 'category', 'is_anonymous',
            'sections', 'like_count', 'view_count', 'comment_count',
            'is_recommended', 'liked',
            'age_band', 'asset_band', 'income_band',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'sections', 'like_count', 'view_count', 'is_recommended',
            'created_at', 'updated_at',
        ]

    def get_liked(self, obj):
        request = self.context.get('request')
        if request is None or not request.user.is_authenticated:
            return False
        return Like.objects.filter(post=obj, user=request.user).exists()

    def get_comment_count(self, obj):
        # 기본 매니저 상속 → 삭제된 댓글 자동 제외
        return obj.comments.count()


class PostCreateUpdateSerializer(serializers.ModelSerializer):
    # is_recommended/밴드는 클라이언트 쓰기 불가 (fields 에서 제외)
    sections = serializers.JSONField(required=False)
    # 모델은 TextField(무제한)라 직렬화 단계에서 상한을 건다
    content = serializers.CharField(
        max_length=POST_CONTENT_MAX_LENGTH,
        error_messages={
            'max_length': f'본문은 {POST_CONTENT_MAX_LENGTH}자 이내로 입력해 주세요.',
        },
    )

    class Meta:
        model = Post
        fields = ['title', 'content', 'category', 'is_anonymous', 'sections']

    def validate_sections(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError('sections는 리스트여야 합니다.')
        if len(value) > SECTION_MAX_COUNT:
            raise serializers.ValidationError(
                f'섹션은 최대 {SECTION_MAX_COUNT}개까지 등록할 수 있습니다.')
        for section in value:
            if not isinstance(section, dict):
                raise serializers.ValidationError('sections의 각 항목은 객체여야 합니다.')
            section_type = section.get('type')
            if section_type == 'text':
                content = section.get('content')
                if not isinstance(content, str) or not content.strip():
                    raise serializers.ValidationError(
                        'text 섹션의 content는 비어있지 않은 문자열이어야 합니다.')
                if len(content) > SECTION_TEXT_MAX_LENGTH:
                    raise serializers.ValidationError(
                        f'text 섹션의 content는 {SECTION_TEXT_MAX_LENGTH}자 이내여야 합니다.')
            elif section_type == 'component':
                if section.get('name') not in SECTION_COMPONENT_WHITELIST:
                    raise serializers.ValidationError(
                        '허용되지 않은 component 이름입니다.')
                caption = section.get('caption')
                if caption is not None and not isinstance(caption, str):
                    raise serializers.ValidationError(
                        'component 섹션의 caption은 문자열이어야 합니다.')
                if caption is not None and len(caption) > SECTION_TEXT_MAX_LENGTH:
                    raise serializers.ValidationError(
                        f'component 섹션의 caption은 {SECTION_TEXT_MAX_LENGTH}자 이내여야 합니다.')
            else:
                raise serializers.ValidationError(
                    "섹션 type은 'text' 또는 'component'여야 합니다.")
        return value


class CommentSerializer(serializers.ModelSerializer):
    author = serializers.SerializerMethodField()
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = Comment
        fields = ['id', 'author', 'content', 'created_at', 'is_mine']

    def get_author(self, obj):
        if obj.is_anonymous:
            return '익명'
        return mask_display_name(obj.user.name)

    def get_is_mine(self, obj):
        request = self.context.get('request')
        if request is None or not request.user.is_authenticated:
            return False
        return obj.user_id == request.user.id


class CommentCreateSerializer(serializers.ModelSerializer):
    # 댓글 목록은 페이지네이션 없이 비인증 조회되므로 본문 길이를 제한한다
    content = serializers.CharField(
        max_length=COMMENT_CONTENT_MAX_LENGTH,
        error_messages={
            'max_length': f'댓글은 {COMMENT_CONTENT_MAX_LENGTH}자 이내로 입력해 주세요.',
        },
    )

    class Meta:
        model = Comment
        fields = ['content', 'is_anonymous']
