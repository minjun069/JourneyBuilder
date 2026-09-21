from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from consulting.models import BasicInfo, ConsultingSession, FinancialAsset

from .models import Comment, Post
from .serializers import (
    COMMENT_CONTENT_MAX_LENGTH,
    POST_CONTENT_MAX_LENGTH,
    SECTION_MAX_COUNT,
    SECTION_TEXT_MAX_LENGTH,
)
from .services import (
    age_band_label,
    asset_band_label,
    derive_author_bands,
    income_band_label,
    mask_display_name,
)

User = get_user_model()

POSTS = '/api/community/v1/posts/'
COMMENTS = '/api/community/v1/comments/'


def bearer_client(user):
    client = APIClient()
    token = RefreshToken.for_user(user).access_token
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
    return client


def make_banded_user(email='writer@test.com', name='작성자'):
    """결정적 밴드의 완료 고객 — 35세('30대') / 순자산 1.5억('1~3억') / 연 6천('5~7천')."""
    user = User.objects.create_user(email=email, password='pass-1234-ok', name=name)
    session = ConsultingSession.objects.create(
        user=user, status=ConsultingSession.Status.COMPLETED)
    BasicInfo.objects.create(session=session, age=35, monthly_income=5_000_000)
    FinancialAsset.objects.create(
        session=session, asset_type='예금/적금', name='예금',
        current_value=150_000_000)
    return user


VALID_SECTIONS = [
    {'type': 'text', 'content': '본문 텍스트'},
    {'type': 'component', 'name': 'BalanceSheetSlide', 'caption': '재무상태표'},
]


class PostCreateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.writer = make_banded_user()

    def setUp(self):
        self.client = bearer_client(self.writer)

    def test_create_saves_band_snapshot(self):
        response = self.client.post(POSTS, {
            'title': '글', 'content': '내용', 'category': '재무계획',
            'sections': VALID_SECTIONS,
        }, format='json')
        self.assertEqual(response.status_code, 201)
        post = Post.objects.get(pk=response.json()['id'])
        self.assertEqual(post.author_age_band, '30대')
        self.assertEqual(post.author_asset_band, '1~3억')
        self.assertEqual(post.author_income_band, '5~7천')
        self.assertEqual(post.sections, VALID_SECTIONS)

    def test_create_response_includes_detail_fields(self):
        # 생성 응답이 annotation 없이도 liked/comment_count 를 렌더링한다 (500 회귀 방지)
        response = self.client.post(POSTS, {
            'title': '글', 'content': '내용', 'category': '투자',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body['liked'])
        self.assertEqual(body['comment_count'], 0)
        self.assertFalse(body['is_recommended'])
        self.assertEqual(body['sections'], [])
        self.assertEqual(body['age_band'], '30대')
        self.assertEqual(body['asset_band'], '1~3억')
        self.assertEqual(body['income_band'], '5~7천')

    def test_sections_must_be_list(self):
        response = self.client.post(POSTS, {
            'title': '글', 'content': '내용', 'sections': {'type': 'text'},
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_sections_component_must_be_whitelisted(self):
        response = self.client.post(POSTS, {
            'title': '글', 'content': '내용',
            'sections': [{'type': 'component', 'name': 'EvilSlide'}],
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_sections_text_requires_nonempty_content(self):
        response = self.client.post(POSTS, {
            'title': '글', 'content': '내용',
            'sections': [{'type': 'text', 'content': '   '}],
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_content_length_is_capped(self):
        response = self.client.post(POSTS, {
            'title': '글', 'content': '가' * (POST_CONTENT_MAX_LENGTH + 1),
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('content', response.json())

    def test_sections_count_is_capped(self):
        response = self.client.post(POSTS, {
            'title': '글', 'content': '내용',
            'sections': [{'type': 'text', 'content': '본문'}] * (SECTION_MAX_COUNT + 1),
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_section_text_length_is_capped(self):
        response = self.client.post(POSTS, {
            'title': '글', 'content': '내용',
            'sections': [{'type': 'text', 'content': '가' * (SECTION_TEXT_MAX_LENGTH + 1)}],
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_patch_cannot_bypass_section_caps(self):
        response = self.client.post(POSTS, {'title': '글', 'content': '내용'}, format='json')
        pk = response.json()['id']
        response = self.client.patch(f'{POSTS}{pk}/', {
            'sections': [{'type': 'text', 'content': '본문'}] * (SECTION_MAX_COUNT + 1),
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_client_cannot_write_is_recommended_or_bands(self):
        response = self.client.post(POSTS, {
            'title': '글', 'content': '내용',
            'is_recommended': True, 'age_band': '60대+',
            'author_age_band': '60대+',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        post = Post.objects.get(pk=response.json()['id'])
        self.assertFalse(post.is_recommended)
        self.assertEqual(post.author_age_band, '30대')

    def test_patch_keeps_band_snapshot(self):
        response = self.client.post(POSTS, {'title': '글', 'content': '내용'}, format='json')
        pk = response.json()['id']
        # 상담 데이터가 바뀌어도 PATCH 는 밴드를 재계산하지 않는다
        BasicInfo.objects.filter(session__user=self.writer).update(age=61)
        response = self.client.patch(f'{POSTS}{pk}/', {'title': '수정'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['age_band'], '30대')


class AnonymousReadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.writer = make_banded_user()
        cls.post = Post.objects.create(
            user=cls.writer, title='글', content='내용', sections=VALID_SECTIONS)

    def test_anonymous_list_returns_200_with_liked_false(self):
        # AnonymousUser 를 Exists 에 넣는 크래시 회귀 방지
        response = APIClient().get(POSTS)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()[0]['liked'])

    def test_anonymous_detail_returns_200(self):
        response = APIClient().get(f'{POSTS}{self.post.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['liked'])


class AuthorPrivacyTests(TestCase):
    """비인증 조회에서 작성자가 식별되지 않아야 한다."""

    @classmethod
    def setUpTestData(cls):
        cls.writer = make_banded_user(name='김민수')
        cls.post = Post.objects.create(
            user=cls.writer, title='실명 글', content='내용',
            author_age_band='30대', author_asset_band='1~3억',
            author_income_band='5~7천')
        cls.anon_post = Post.objects.create(
            user=cls.writer, title='익명 글', content='내용', is_anonymous=True,
            author_age_band='30대', author_asset_band='1~3억',
            author_income_band='5~7천')

    def test_mask_display_name_rules(self):
        self.assertEqual(mask_display_name('김민수'), '김○○')
        self.assertEqual(mask_display_name('남궁민수'), '남○○○')
        self.assertEqual(mask_display_name('김철'), '김○')
        self.assertEqual(mask_display_name('김'), '김')
        self.assertEqual(mask_display_name(''), '익명')
        self.assertEqual(mask_display_name(None), '익명')

    def test_list_and_detail_never_expose_real_name(self):
        rows = APIClient().get(POSTS).json()
        by_title = {row['title']: row for row in rows}
        self.assertEqual(by_title['실명 글']['author'], '김○○')
        detail = APIClient().get(f'{POSTS}{self.post.pk}/').json()
        self.assertEqual(detail['author'], '김○○')
        self.assertNotIn('김민수', str(rows))

    def test_anonymous_post_exposes_neither_name_nor_bands(self):
        # 밴드 3종은 사용자별로 결정적이라, 남겨두면 실명 글과 대조해 익명이 풀린다
        rows = APIClient().get(POSTS).json()
        row = next(r for r in rows if r['title'] == '익명 글')
        self.assertEqual(row['author'], '익명')
        self.assertEqual(row['age_band'], '')
        self.assertEqual(row['asset_band'], '')
        self.assertEqual(row['income_band'], '')

        detail = APIClient().get(f'{POSTS}{self.anon_post.pk}/').json()
        self.assertEqual(detail['author'], '익명')
        self.assertEqual(detail['age_band'], '')
        self.assertEqual(detail['asset_band'], '')
        self.assertEqual(detail['income_band'], '')
        # 스냅샷 자체는 DB 에 남아 있다 (노출만 막는다)
        self.anon_post.refresh_from_db()
        self.assertEqual(self.anon_post.author_age_band, '30대')

    def test_comment_author_name_is_masked(self):
        Comment.objects.create(post=self.post, user=self.writer, content='댓글')
        rows = APIClient().get(f'{POSTS}{self.post.pk}/comments/').json()
        self.assertEqual(rows[0]['author'], '김○○')


class PostListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.writer = make_banded_user()
        cls.other = User.objects.create_user(
            email='other@test.com', password='pass-1234-ok', name='다른유저')
        cls.post = Post.objects.create(
            user=cls.writer, title='글', content='내용',
            author_age_band='30대', author_asset_band='1~3억',
            author_income_band='5~7천', is_recommended=True)

    def test_list_includes_new_fields(self):
        row = bearer_client(self.writer).get(POSTS).json()[0]
        self.assertEqual(row['comment_count'], 0)
        self.assertTrue(row['is_recommended'])
        self.assertEqual(row['age_band'], '30대')
        self.assertEqual(row['asset_band'], '1~3억')
        self.assertEqual(row['income_band'], '5~7천')
        self.assertIn('content', row)

    def test_liked_differs_per_user(self):
        bearer_client(self.writer).post(f'{POSTS}{self.post.pk}/like/')
        self.assertTrue(bearer_client(self.writer).get(POSTS).json()[0]['liked'])
        self.assertFalse(bearer_client(self.other).get(POSTS).json()[0]['liked'])

    def test_deleted_comment_excluded_from_count(self):
        Comment.objects.create(post=self.post, user=self.other, content='유지')
        deleted = Comment.objects.create(post=self.post, user=self.other, content='삭제')
        deleted.soft_delete()
        row = bearer_client(self.writer).get(POSTS).json()[0]
        self.assertEqual(row['comment_count'], 1)
        detail = bearer_client(self.writer).get(f'{POSTS}{self.post.pk}/').json()
        self.assertEqual(detail['comment_count'], 1)

    def test_list_is_ordered_newest_first(self):
        """Count annotate 가 GROUP BY 를 만들면 Meta.ordering 이 무시된다(Django 3.1+).
        뷰가 명시적으로 정렬하지 않으면 목록이 뒤섞여 나간다."""
        now = timezone.now()
        for days, title in ((3, '사흘 전'), (1, '하루 전'), (7, '일주일 전')):
            created = Post.objects.create(user=self.writer, title=title, content='내용')
            Post.objects.filter(pk=created.pk).update(created_at=now - timedelta(days=days))
        titles = [row['title'] for row in bearer_client(self.writer).get(POSTS).json()]
        self.assertEqual(titles, ['글', '하루 전', '사흘 전', '일주일 전'])

    def test_list_breaks_created_at_ties_by_id(self):
        """시드 글은 created_at 이 동률인 쌍이 있어 타이브레이커가 없으면 순서가 계획에 좌우된다."""
        same = timezone.now() - timedelta(days=2)
        ids = []
        for title in ('동률 A', '동률 B'):
            created = Post.objects.create(user=self.writer, title=title, content='내용')
            Post.objects.filter(pk=created.pk).update(created_at=same)
            ids.append(created.pk)
        rows = bearer_client(self.writer).get(POSTS).json()
        tied = [row['id'] for row in rows if row['id'] in ids]
        self.assertEqual(tied, sorted(ids, reverse=True))


class PostDetailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.writer = make_banded_user()
        cls.post = Post.objects.create(
            user=cls.writer, title='글', content='내용', sections=VALID_SECTIONS)

    def test_detail_returns_sections_and_increments_view_count(self):
        client = bearer_client(self.writer)
        first = client.get(f'{POSTS}{self.post.pk}/').json()
        self.assertEqual(first['sections'], VALID_SECTIONS)
        self.assertEqual(first['view_count'], 1)  # 이번 조회가 응답에 반영
        second = client.get(f'{POSTS}{self.post.pk}/').json()
        self.assertEqual(second['view_count'], 2)


class PostIsMineTests(TestCase):
    """is_mine 은 요청 사용자에서만 파생된다 (삭제 버튼 노출 판단 근거)."""

    @classmethod
    def setUpTestData(cls):
        cls.writer = make_banded_user()
        cls.other = User.objects.create_user(
            email='other@test.com', password='pass-1234-ok', name='다른유저')
        cls.post = Post.objects.create(user=cls.writer, title='글', content='내용')

    def row_for(self, client):
        rows = client.get(POSTS).json()
        return next(row for row in rows if row['id'] == self.post.pk)

    def test_list_is_mine_differs_per_user(self):
        self.assertTrue(self.row_for(bearer_client(self.writer))['is_mine'])
        self.assertFalse(self.row_for(bearer_client(self.other))['is_mine'])

    def test_detail_is_mine_differs_per_user(self):
        url = f'{POSTS}{self.post.pk}/'
        self.assertTrue(bearer_client(self.writer).get(url).json()['is_mine'])
        self.assertFalse(bearer_client(self.other).get(url).json()['is_mine'])

    def test_anonymous_request_gets_is_mine_false(self):
        # 목록/상세는 IsAuthenticatedOrReadOnly — AnonymousUser 에서 크래시하면 안 된다
        self.assertFalse(self.row_for(APIClient())['is_mine'])
        detail = APIClient().get(f'{POSTS}{self.post.pk}/')
        self.assertEqual(detail.status_code, 200)
        self.assertFalse(detail.json()['is_mine'])

    def test_create_response_includes_is_mine_true(self):
        # 생성 응답은 annotation 없는 새 인스턴스를 직렬화한다
        response = bearer_client(self.writer).post(
            POSTS, {'title': '새 글', 'content': '내용'}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()['is_mine'])

    def test_client_cannot_write_is_mine(self):
        response = bearer_client(self.writer).post(
            POSTS, {'title': '새 글', 'content': '내용', 'is_mine': False},
            format='json')
        self.assertEqual(response.status_code, 201)
        # 페이로드가 아니라 요청 사용자에서만 결정된다
        self.assertTrue(response.json()['is_mine'])
        pk = response.json()['id']
        other_view = bearer_client(self.other).get(f'{POSTS}{pk}/').json()
        self.assertFalse(other_view['is_mine'])

    def test_other_user_cannot_forge_is_mine_on_create(self):
        response = bearer_client(self.other).post(
            POSTS, {'title': '남의 글', 'content': '내용', 'is_mine': True},
            format='json')
        self.assertEqual(response.status_code, 201)
        pk = response.json()['id']
        self.assertFalse(bearer_client(self.writer).get(f'{POSTS}{pk}/').json()['is_mine'])


class PostDeleteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.writer = make_banded_user()
        cls.other = User.objects.create_user(
            email='other@test.com', password='pass-1234-ok', name='다른유저')

    def setUp(self):
        self.post = Post.objects.create(user=self.writer, title='글', content='내용')

    def test_owner_can_soft_delete_and_post_leaves_list(self):
        response = bearer_client(self.writer).delete(f'{POSTS}{self.post.pk}/')
        self.assertEqual(response.status_code, 204)
        self.post.refresh_from_db()
        self.assertIsNotNone(self.post.deleted_at)
        ids = [row['id'] for row in bearer_client(self.writer).get(POSTS).json()]
        self.assertNotIn(self.post.pk, ids)
        # 기본 매니저 조회 → 상세도 404
        self.assertEqual(
            bearer_client(self.writer).get(f'{POSTS}{self.post.pk}/').status_code, 404)

    def test_other_user_delete_returns_403_and_post_survives(self):
        response = bearer_client(self.other).delete(f'{POSTS}{self.post.pk}/')
        self.assertEqual(response.status_code, 403)
        self.post.refresh_from_db()
        self.assertIsNone(self.post.deleted_at)
        ids = [row['id'] for row in bearer_client(self.writer).get(POSTS).json()]
        self.assertIn(self.post.pk, ids)

    def test_anonymous_delete_is_rejected(self):
        response = APIClient().delete(f'{POSTS}{self.post.pk}/')
        self.assertIn(response.status_code, (401, 403))
        self.post.refresh_from_db()
        self.assertIsNone(self.post.deleted_at)


class CommentApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.writer = make_banded_user()
        cls.other = User.objects.create_user(
            email='other@test.com', password='pass-1234-ok', name='다른유저')
        cls.post = Post.objects.create(user=cls.writer, title='글', content='내용')

    def comments_url(self, post=None):
        return f'{POSTS}{(post or self.post).pk}/comments/'

    def test_create_and_list(self):
        client = bearer_client(self.writer)
        response = client.post(self.comments_url(), {'content': '첫 댓글'}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['author'], '작○○')  # 실명 가림
        self.assertTrue(response.json()['is_mine'])

        rows = APIClient().get(self.comments_url()).json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['content'], '첫 댓글')
        self.assertFalse(rows[0]['is_mine'])  # 비인증 조회는 is_mine=False

    def test_anonymous_cannot_create(self):
        response = APIClient().post(self.comments_url(), {'content': '댓글'}, format='json')
        self.assertEqual(response.status_code, 401)

    def test_anonymous_flag_masks_author(self):
        client = bearer_client(self.writer)
        response = client.post(
            self.comments_url(), {'content': '익명 댓글', 'is_anonymous': True}, format='json')
        self.assertEqual(response.json()['author'], '익명')

    def test_content_length_is_capped(self):
        # 댓글 목록은 페이지네이션 없이 비인증 조회되므로 길이 상한이 필요하다
        client = bearer_client(self.writer)
        response = client.post(
            self.comments_url(),
            {'content': '가' * (COMMENT_CONTENT_MAX_LENGTH + 1)}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('content', response.json())
        response = client.post(
            self.comments_url(),
            {'content': '가' * COMMENT_CONTENT_MAX_LENGTH}, format='json')
        self.assertEqual(response.status_code, 201)

    def test_is_mine_differs_per_user(self):
        comment = Comment.objects.create(post=self.post, user=self.writer, content='댓글')
        mine = bearer_client(self.writer).get(self.comments_url()).json()
        self.assertTrue(mine[0]['is_mine'])
        others = bearer_client(self.other).get(self.comments_url()).json()
        self.assertFalse(others[0]['is_mine'])
        self.assertEqual(mine[0]['id'], comment.pk)

    def test_owner_can_soft_delete(self):
        comment = Comment.objects.create(post=self.post, user=self.writer, content='댓글')
        response = bearer_client(self.writer).delete(f'{COMMENTS}{comment.pk}/')
        self.assertEqual(response.status_code, 204)
        comment.refresh_from_db()
        self.assertIsNotNone(comment.deleted_at)
        # 중복 삭제 — 기본 매니저 조회라 404
        response = bearer_client(self.writer).delete(f'{COMMENTS}{comment.pk}/')
        self.assertEqual(response.status_code, 404)

    def test_other_user_delete_returns_403(self):
        comment = Comment.objects.create(post=self.post, user=self.writer, content='댓글')
        response = bearer_client(self.other).delete(f'{COMMENTS}{comment.pk}/')
        self.assertEqual(response.status_code, 403)

    def test_comments_under_soft_deleted_post_return_404(self):
        self.post.soft_delete()
        response = APIClient().get(self.comments_url())
        self.assertEqual(response.status_code, 404)


class BandServiceTests(TestCase):
    def test_age_band_boundaries(self):
        self.assertEqual(age_band_label(None), '')
        self.assertEqual(age_band_label(29), '20대')
        self.assertEqual(age_band_label(30), '30대')
        self.assertEqual(age_band_label(39), '30대')
        self.assertEqual(age_band_label(40), '40대')
        self.assertEqual(age_band_label(49), '40대')
        self.assertEqual(age_band_label(50), '50대')
        self.assertEqual(age_band_label(59), '50대')
        self.assertEqual(age_band_label(60), '60대+')

    def test_asset_band_boundaries(self):
        self.assertEqual(asset_band_label(None), '')
        self.assertEqual(asset_band_label(99_999_999), '1억 미만')
        self.assertEqual(asset_band_label(100_000_000), '1~3억')
        self.assertEqual(asset_band_label(299_999_999), '1~3억')
        self.assertEqual(asset_band_label(300_000_000), '3~5억')
        self.assertEqual(asset_band_label(499_999_999), '3~5억')
        self.assertEqual(asset_band_label(500_000_000), '5~10억')
        self.assertEqual(asset_band_label(999_999_999), '5~10억')
        self.assertEqual(asset_band_label(1_000_000_000), '10억+')

    def test_income_band_boundaries(self):
        self.assertEqual(income_band_label(None), '')
        self.assertEqual(income_band_label(29_999_999), '3천 미만')
        self.assertEqual(income_band_label(30_000_000), '3~5천')
        self.assertEqual(income_band_label(49_999_999), '3~5천')
        self.assertEqual(income_band_label(50_000_000), '5~7천')
        self.assertEqual(income_band_label(69_999_999), '5~7천')
        self.assertEqual(income_band_label(70_000_000), '7천~1억')
        self.assertEqual(income_band_label(99_999_999), '7천~1억')
        self.assertEqual(income_band_label(100_000_000), '1억+')

    def test_no_session_returns_all_empty(self):
        user = User.objects.create_user(
            email='nosession@test.com', password='pass-1234-ok', name='무세션')
        self.assertEqual(derive_author_bands(user), ('', '', ''))

    def test_abandoned_session_is_excluded(self):
        user = User.objects.create_user(
            email='aband@test.com', password='pass-1234-ok', name='이탈')
        ConsultingSession.objects.create(
            user=user, status=ConsultingSession.Status.ABANDONED)
        self.assertEqual(derive_author_bands(user), ('', '', ''))

    def test_no_financial_input_gives_empty_asset_band(self):
        # 자산·부채 모두 0 → 순자산 0 을 '1억 미만'으로 오분류하지 않는다
        user = User.objects.create_user(
            email='noasset@test.com', password='pass-1234-ok', name='미입력')
        session = ConsultingSession.objects.create(
            user=user, status=ConsultingSession.Status.IN_PROGRESS)
        BasicInfo.objects.create(session=session, age=35, monthly_income=5_000_000)
        age_band, asset_band, income_band = derive_author_bands(user)
        self.assertEqual(age_band, '30대')
        self.assertEqual(asset_band, '')
        self.assertEqual(income_band, '5~7천')

    def test_monthly_income_none_gives_empty_income_band(self):
        # None * 12 TypeError 방지 — income 만 '' 이고 다른 밴드는 유지
        user = User.objects.create_user(
            email='noincome@test.com', password='pass-1234-ok', name='소득없음')
        session = ConsultingSession.objects.create(
            user=user, status=ConsultingSession.Status.COMPLETED)
        BasicInfo.objects.create(session=session, age=45, monthly_income=None)
        FinancialAsset.objects.create(
            session=session, asset_type='예금/적금', name='예금',
            current_value=350_000_000)
        self.assertEqual(derive_author_bands(user), ('40대', '3~5억', ''))

    def test_birth_year_fallback_when_basic_info_age_missing(self):
        user = User.objects.create_user(
            email='birth@test.com', password='pass-1234-ok', name='출생연도',
            birth_year=timezone.localdate().year - 52)
        session = ConsultingSession.objects.create(
            user=user, status=ConsultingSession.Status.COMPLETED)
        BasicInfo.objects.create(session=session, age=None)
        age_band, _, _ = derive_author_bands(user)
        self.assertEqual(age_band, '50대')


class SeedCommunityCommandTests(TestCase):
    def test_requires_demo_customers(self):
        with self.assertRaises(CommandError):
            call_command('seed_community', verbosity=0)

    def test_seed_creates_posts_and_comments(self):
        call_command('seed_demo', '--count', '12', verbosity=0)
        call_command('seed_community', verbosity=0)
        self.assertEqual(Post.objects.count(), 10)
        self.assertEqual(Comment.objects.count(), 5)
        self.assertEqual(Post.objects.filter(is_recommended=True).count(), 6)
        # 글은 모두 데모 고객 명의
        self.assertFalse(
            Post.objects.exclude(user__email__endswith='@retirewise.demo').exists())
        # 백데이트 적용 — created_at 이 now 보다 과거이고, 댓글은 글 이후 시각
        now = timezone.now()
        for post in Post.objects.all():
            self.assertLess(post.created_at, now)
        for comment in Comment.objects.select_related('post'):
            self.assertLess(comment.created_at, now)
            self.assertGreater(comment.created_at, comment.post.created_at)

    def test_rerun_is_idempotent(self):
        call_command('seed_demo', '--count', '12', verbosity=0)
        call_command('seed_community', verbosity=0)
        call_command('seed_community', verbosity=0)
        self.assertEqual(Post.objects.count(), 10)
        self.assertEqual(Comment.objects.count(), 5)

    def test_seeded_posts_have_no_created_at_ties(self):
        """상대시각이 같은 글이 두 쌍 있어, 분 단위로 밀어두지 않으면 동률이 된다.
        동률이면 목록 순서가 타이브레이커에 좌우돼 원본 노출 순서와 어긋난다."""
        call_command('seed_demo', '--count', '12', verbosity=0)
        call_command('seed_community', verbosity=0)
        stamps = list(Post.objects.values_list('created_at', flat=True))
        self.assertEqual(len(stamps), len(set(stamps)))
        # 목록 정렬(-created_at) 결과가 시드 파일 순서와 같아야 한다
        ordered = list(Post.objects.order_by('-created_at', '-id')
                       .values_list('pk', flat=True))
        self.assertEqual(ordered, sorted(ordered))

    def test_age_band_matching_holds_at_default_count(self):
        # seed_community 의 age_band 매칭은 후보가 없으면 조용히 완화된다.
        # 기본 시드 고객 수(40명)에서는 완화가 일어나지 않음을 고정한다
        # (커맨드 docstring 의 "기본 고객 수에서는 일치가 보장된다" 주장의 근거).
        call_command('seed_demo', verbosity=0)
        call_command('seed_community', verbosity=0)

        this_year = timezone.localdate().year
        basic_ages = dict(
            BasicInfo.objects.values_list('session__user_id', 'age'))
        for post in Post.objects.select_related('user'):
            age = basic_ages.get(post.user_id)
            if age is None and post.user.birth_year:
                age = this_year - post.user.birth_year
            self.assertEqual(
                age_band_label(age), post.author_age_band,
                f'{post.title}: 작성자 연령 밴드가 글의 age_band 와 다르다')

    def test_real_user_posts_are_preserved(self):
        call_command('seed_demo', '--count', '12', verbosity=0)
        real_user = User.objects.create_user(
            email='real@test.com', password='pass-1234-ok', name='실사용자')
        real_post = Post.objects.create(user=real_user, title='실사용 글', content='내용')
        call_command('seed_community', verbosity=0)
        self.assertTrue(Post.objects.filter(pk=real_post.pk).exists())
        self.assertEqual(Post.objects.count(), 11)
