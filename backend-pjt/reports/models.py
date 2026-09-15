from django.db import models
from django.db.models import Q
from consulting.models import ConsultingSession


class Report(models.Model):

    class ReportType(models.TextChoices):
        ASSET_STATEMENT  = 'asset_statement',  '자산현황표'
        CASH_FLOW        = 'cash_flow',        '현금흐름표'
        FINANCIAL_INDICATORS  = 'financial-indicators',  '가계재무지표'
        FINANCIAL_GOAL_RESULT = 'financial-goal-result', '재무목표'
        RETIREMENT_FUND       = 'retirement-fund',       '은퇴 필요자금'
        RETIREMENT_READINESS  = 'retirement-readiness',  '은퇴 준비자금'
        PENSION_SCHEDULE      = 'pension-schedule',      '연금 스케줄'
        LIFETIME_CASHFLOW     = 'lifetime-cashflow',     '생애현금흐름 및 생애순자산'
        REQUIRED_SAVINGS      = 'required-savings',      '필요저축액'

    class Status(models.TextChoices):
        PROCESSING = 'processing', '생성 중'
        READY      = 'ready',      '생성 완료'
        FAILED     = 'failed',     '생성 실패'

    session = models.ForeignKey(
        ConsultingSession,
        on_delete=models.CASCADE,
        related_name='reports'
    )
    report_type = models.CharField(
        max_length=30,
        choices=ReportType.choices
    )
    version   = models.PositiveIntegerField(default=1)
    is_latest = models.BooleanField(default=True)
    status    = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROCESSING
    )
    content      = models.JSONField(null=True, blank=True)
    pdf_url      = models.URLField(max_length=500, null=True, blank=True)
    generated_at = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'reports'
        ordering = ['-created_at']
        constraints = [
            # 한 세션에 동일 타입의 보고서-동일 버전은 1개만 존재
            models.UniqueConstraint(
                fields=['session', 'report_type', 'version'],
                name='unique_session_report_type_version'
            ),
            # 한 세션에 동일 타입의 보고서의 가장 최근 버전은 1개만 존재
            models.UniqueConstraint(
                fields=['session', 'report_type'],
                condition=Q(is_latest=True),
                # Q: 조건을 객체로 만들 수 있음
                name='unique_latest_per_session_report'
            ),
        ]

    def __str__(self):
        return f"[{self.get_report_type_display()}] 세션#{self.session_id} v{self.version}"
