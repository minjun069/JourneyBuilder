from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from consulting.ai_execution import (
    AI_PROCESSING_EXPIRY_SECONDS,
    expire_stale_execution,
)
from consulting.ai_lifecycle import RedisLifecycleStore
from consulting.models import AIRequestExecution, ConsultingSession


class Command(BaseCommand):
    help = '안전 만료 시각을 지난 processing AI 실행을 조회하거나 expired로 전환합니다.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='지정할 때만 stale 실행을 실제로 expired 처리합니다.',
        )
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        limit = options['limit']
        if limit < 1 or limit > 1000:
            raise CommandError('--limit은 1 이상 1000 이하여야 합니다.')

        now = timezone.now()
        fallback_cutoff = now - timedelta(seconds=AI_PROCESSING_EXPIRY_SECONDS)
        candidates = list(
            AIRequestExecution.objects.filter(
                status=AIRequestExecution.Status.PROCESSING,
            )
            .filter(
                Q(expires_at__lte=now)
                | Q(expires_at__isnull=True, created_at__lte=fallback_cutoff)
            )
            .order_by('created_at')
            .values_list('id', 'session_id')[:limit]
        )
        if not options['apply']:
            self.stdout.write(
                f'stale processing AI 실행 {len(candidates)}건 확인; 변경 없음'
            )
            return

        store = RedisLifecycleStore()
        expired = 0
        for execution_id, session_id in candidates:
            with transaction.atomic():
                ConsultingSession.objects.select_for_update().filter(
                    id=session_id,
                ).first()
                execution = (
                    AIRequestExecution.objects.select_for_update()
                    .filter(id=execution_id)
                    .first()
                )
                if execution is not None:
                    did_expire, _ = expire_stale_execution(
                        execution,
                        store,
                        now=now,
                    )
                    expired += int(did_expire)
        self.stdout.write(self.style.SUCCESS(f'stale AI 실행 {expired}건 만료 처리'))
