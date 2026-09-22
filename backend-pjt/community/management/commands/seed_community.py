"""커뮤니티 시연용 게시글 시드 — 목데이터 10건 + 댓글 5건을 DB 로 이식.

seed_data/community_posts.json 의 글을 데모 고객(@retirewise.demo) 명의로 생성한다.
글의 age_band 와 BasicInfo.age 가 맞는 고객을 우선 배정하고, 글마다 가급적
다른 작성자를 쓴다. 밴드/좋아요/조회수는 목데이터 값을 그대로 저장한다
(Like 행은 만들지 않으므로 토글 정합 문제 없음).

멱등성: 데모 고객 명의의 글을 hard delete 후 재생성한다. 이 hard delete 는
실사용자가 데모 글에 단 댓글/좋아요도 cascade 로 함께 지운다 (시연 피드라 허용).
실사용 계정 명의의 글은 건드리지 않는다.

주의: seed_demo 를 단독 재실행하면 데모 유저 삭제 → 그 명의의 글 전체가
cascade 로 소멸하므로 seed_community 를 다시 실행해야 한다
(bootstrap_demo 가 이 순서를 보장한다).

알려진 한계 (의도된 동작):

1. 작성자 배정은 age_band 만 맞춘다. 글에 저장되는 asset_band/income_band 는
   목데이터 값을 그대로 쓰므로, 배정된 작성자의 실제 상담 데이터로
   derive_author_bands() 를 돌린 결과와 어긋날 수 있다. 필터 시연용 분포를
   목데이터 그대로 유지하기 위한 선택이며, 이 글들의 밴드는 작성자의 실제
   재무 상태를 나타내지 않는다.
2. _pick_author 의 age_band 매칭은 후보가 없으면 조용히 완화된다
   (밴드 일치 미사용 고객 → 아무 미사용 고객 → 아무 데모 고객). 따라서
   --count 가 작으면 age_band 불일치 작성자가 배정될 수 있다. 기본 시드
   고객 수(40명)에서는 일치가 보장되며, 이를 테스트로 고정해 두었다
   (community/tests.py SeedCommunityCommandTests).
"""
import copy
import json
import random
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from community.models import Comment, Post
from community.services import age_band_label
from consulting.models import BasicInfo

DEMO_DOMAIN = 'retirewise.demo'
SEED_DATA_PATH = Path(__file__).resolve().parents[2] / 'seed_data' / 'community_posts.json'


class Command(BaseCommand):
    help = '커뮤니티 시연용 게시글·댓글 시드 데이터를 생성한다 (목데이터 이식).'

    def add_arguments(self, parser):
        parser.add_argument('--seed', type=int, default=42, help='난수 시드 (기본 42, 재현 가능)')

    @transaction.atomic
    def handle(self, *args, **options):
        rng = random.Random(options['seed'])
        User = get_user_model()

        demo_users = list(
            User.objects
            .filter(email__endswith=f'@{DEMO_DOMAIN}', is_staff=False)
            .order_by('id')
        )
        if not demo_users:
            raise CommandError('데모 고객이 없습니다. seed_demo를 먼저 실행하세요.')

        with open(SEED_DATA_PATH, encoding='utf-8') as f:
            entries = json.load(f)

        # 멱등성 — 데모 고객 명의의 글만 hard delete (실사용 계정 글은 보존)
        deleted, _ = Post.all_objects.filter(
            user__email__endswith=f'@{DEMO_DOMAIN}').delete()
        if deleted:
            self.stdout.write(f'기존 데모 게시글 데이터 {deleted}행 삭제')

        # 데모 고객의 나이 밴드 — BasicInfo.age 우선, 없으면 birth_year 환산
        this_year = timezone.localdate().year
        basic_ages = dict(
            BasicInfo.objects
            .filter(session__user__in=demo_users)
            .values_list('session__user_id', 'age')
        )
        user_bands = {}
        for user in demo_users:
            age = basic_ages.get(user.id)
            if age is None and user.birth_year:
                age = this_year - user.birth_year
            user_bands[user.id] = age_band_label(age)

        now = timezone.now()
        used_author_ids = set()
        post_count = 0
        comment_count = 0
        for index, entry in enumerate(entries):
            author = self._pick_author(entry['age_band'], demo_users, user_bands,
                                       used_author_ids, rng)
            used_author_ids.add(author.id)

            post = Post.objects.create(
                user=author,
                title=entry['title'],
                content=entry['content'],
                category=entry['category'],
                is_anonymous=False,
                # 섹션은 리포트 스냅샷(data)까지 담은 중첩 구조라 깊은 복사로 저장한다.
                # entries 를 그대로 넘기면 같은 dict 객체가 글마다 공유돼
                # 한 글의 수정이 다른 글로 새어나갈 수 있다.
                sections=copy.deepcopy(entry['sections']),
                is_recommended=entry['is_recommended'],
                author_age_band=entry['age_band'],
                author_asset_band=entry['asset_band'],
                author_income_band=entry['income_band'],
                like_count=entry['like_count'],
                view_count=entry['view_count'],
            )
            post_count += 1
            # 백데이트 — auto_now_add/auto_now 를 우회하려면 update() 필수.
            # '1주 전' 처럼 상대시각이 같은 글이 두 쌍 있는데, 동률이면 목록 순서가
            # 타이브레이커(-id)에 좌우돼 원본 노출 순서와 뒤집힌다. 목록 순서를
            # 원본 그대로 유지하려고 뒤 항목일수록 1분씩 더 과거로 민다.
            created_at = now - timedelta(
                hours=entry['created_ago_hours'], minutes=index)
            Post.objects.filter(pk=post.pk).update(
                created_at=created_at, updated_at=created_at)

            for comment_entry in entry['comments']:
                commenters = [u for u in demo_users if u.id != author.id] or demo_users
                comment = Comment.objects.create(
                    post=post,
                    user=rng.choice(commenters),
                    content=comment_entry['content'],
                )
                comment_count += 1
                # 댓글도 백데이트 누락 시 전부 '방금 전'으로 보이므로 동일 처리
                comment_created_at = now - timedelta(
                    hours=comment_entry['created_ago_hours'])
                Comment.objects.filter(pk=comment.pk).update(
                    created_at=comment_created_at, updated_at=comment_created_at)

        self.stdout.write(self.style.SUCCESS(
            f'커뮤니티 게시글 {post_count}건 + 댓글 {comment_count}건 생성 완료'
        ))

    @staticmethod
    def _pick_author(age_band, demo_users, user_bands, used_author_ids, rng):
        """글의 age_band 와 맞는 미사용 고객 우선 → 미사용 고객 → 아무 데모 고객."""
        for candidates in (
            [u for u in demo_users
             if user_bands[u.id] == age_band and u.id not in used_author_ids],
            [u for u in demo_users if u.id not in used_author_ids],
            demo_users,
        ):
            if candidates:
                return rng.choice(candidates)
        return rng.choice(demo_users)
