from django.db import models
from django.conf import settings
from django.utils.timezone import now


class ConsultingSession(models.Model):

    class Status(models.TextChoices):
        ONBOARDING  = 'onboarding',  '온보딩'
        IN_PROGRESS = 'in_progress', '상담 진행 중'
        COMPLETED   = 'completed',   '상담 완료'
        ABANDONED   = 'abandoned',   '중도 이탈'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='consulting_sessions'
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ONBOARDING
    )
    completed_at   = models.DateTimeField(null=True, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'consulting_sessions'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.name}의 상담 세션 #{self.pk}"

    def get_section_completion(self):
        return {
            'basic_info': hasattr(self, 'basic_info'),
            'financial_mindset': hasattr(self, 'financial_mindset'),
            'financial_satisfaction': hasattr(self, 'financial_satisfaction'),
            'financial_assets': self.financial_assets.exists(),
            'pension_assets': self.pension_assets.exists(),
            'real_estate_assets': self.real_estate_assets.exists(),
            'incomes': self.incomes.exists(),
            'expenses': self.expenses.exists(),
            'financial_goals': (
                self.goal_periods.exists()
                or self.goal_milestones.exists()
            ),
        }

    def get_progress_pct(self):
        section_completion = self.get_section_completion()
        if not section_completion:
            return 0

        completed_count = sum(section_completion.values())
        return round(completed_count / len(section_completion) * 100)

    def is_ready_for_reports(self):
        return all(self.get_section_completion().values())

    def refresh_completion_status(self):
        if self.status == self.Status.ABANDONED:
            return

        if self.is_ready_for_reports():
            self.status = self.Status.COMPLETED
            if self.completed_at is None:
                self.completed_at = now()
        else:
            self.status = self.Status.IN_PROGRESS
            self.completed_at = None

        self.save(update_fields=['status', 'completed_at'])


class Message(models.Model):

    class Role(models.TextChoices):
        USER      = 'user',      '사용자'
        ASSISTANT = 'assistant', 'AI'

    session = models.ForeignKey(
        ConsultingSession,
        on_delete=models.CASCADE,
        related_name='messages'
    )
    role    = models.CharField(max_length=10, choices=Role.choices)
    content = models.TextField()
    extracted_data = models.JSONField(null=True, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'consulting_messages'
        ordering = ['created_at']

    def __str__(self):
        return f"[{self.role}] {self.content[:30]}"

    def to_ai_format(self):
        return {'role': self.role, 'content': self.content}


class AITerminalReceipt(models.Model):
    class Status(models.TextChoices):
        COMPLETED = 'completed', '완료'
        FAILED = 'failed', '실패'
        EXPIRED = 'expired', '만료'

    request_id = models.CharField(max_length=128, unique=True)
    session = models.ForeignKey(
        ConsultingSession,
        on_delete=models.CASCADE,
        related_name='ai_terminal_receipts',
    )
    thread_id = models.CharField(max_length=255)
    step_id = models.CharField(max_length=100)
    user_message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    assistant_message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    callback_digest = models.CharField(max_length=64, blank=True, default='')
    status = models.CharField(max_length=10, choices=Status.choices)
    completed_at = models.DateTimeField()
    error = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'consulting_ai_terminal_receipts'


# ──────────────────────────────────────────────────────────
# 사용자 정보 입력 처리
# ──────────────────────────────────────────────────────────

# 기본 정보 (1:1)
class BasicInfo(models.Model):
    class Job(models.TextChoices):
        EMPLOYEE   = '직장인'
        CIVIL      = '공무원'
        SELF       = '자영업자'
        FREELANCE  = '프리랜서'
        HOMEMAKER  = '전업주부'
        STUDENT    = '학생'
        RETIRED    = '퇴직자'
        OTHER      = '기타'

    class MaritalStatus(models.TextChoices):
        SINGLE  = '미혼'
        MARRIED = '기혼'

    class Need(models.TextChoices):
        RETIREMENT = '은퇴'
        INVESTMENT = '투자'
        HOUSING    = '주택자금'
        EDUCATION  = '교육자금'
        PROTECTION = '보장'
        OTHER      = '기타'

    session        = models.OneToOneField(ConsultingSession, on_delete=models.CASCADE, related_name='basic_info')
    age            = models.SmallIntegerField(null=True, blank=True)
    job            = models.CharField(max_length=10, choices=Job.choices, null=True, blank=True)
    marital_status = models.CharField(max_length=5, choices=MaritalStatus.choices, null=True, blank=True)
    dependents     = models.JSONField(default=dict)    # {children:[{age}], parents:[{age}], others:[{name,age}]}
    tendency       = models.JSONField(default=list)    # ["안전 최우선", ...]
    interest       = models.CharField(max_length=200, null=True, blank=True)
    need           = models.CharField(max_length=10, choices=Need.choices, null=True, blank=True)
    monthly_income = models.IntegerField(null=True, blank=True)
    retirement_age = models.SmallIntegerField(null=True, blank=True)
    end_age        = models.SmallIntegerField(null=True, blank=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'consulting_basic_info'

    def __str__(self):
        return f"세션 #{self.session_id} 기본 정보"


# 경제 관념 (1:1)
class FinancialMindset(models.Model):
    session           = models.OneToOneField(ConsultingSession, on_delete=models.CASCADE, related_name='financial_mindset')
    values            = models.TextField(null=True, blank=True)   # 가치관
    past_experience   = models.TextField(null=True, blank=True)   # 과거경험
    current_situation = models.TextField(null=True, blank=True)   # 현 재무상황
    current_challenge = models.TextField(null=True, blank=True)   # 당면과제
    invest_philosophy = models.TextField(null=True, blank=True)   # 투자철학
    future_goals      = models.TextField(null=True, blank=True)   # 미래인생목표
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'consulting_financial_mindset'

    def __str__(self):
        return f"세션 #{self.session_id} 경제 관념"


# 재무 만족도 (1:1)
class FinancialSatisfaction(models.Model):
    session          = models.OneToOneField(ConsultingSession, on_delete=models.CASCADE, related_name='financial_satisfaction')
    # 1~5 점
    cashflow_score    = models.SmallIntegerField(null=True, blank=True)   # 현금흐름 관리
    risk_score        = models.SmallIntegerField(null=True, blank=True)   # 위험 관리
    investment_score  = models.SmallIntegerField(null=True, blank=True)   # 투자
    management_score  = models.SmallIntegerField(null=True, blank=True)   # 재무관리
    inheritance_score = models.SmallIntegerField(null=True, blank=True)   # 상속
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'consulting_financial_satisfaction'

    def __str__(self):
        return f"세션 #{self.session_id} 재무 만족도"


# 금융자산 (1:N)
class FinancialAsset(models.Model):
    session         = models.ForeignKey(ConsultingSession, on_delete=models.CASCADE, related_name='financial_assets')
    asset_type      = models.CharField(max_length=50, null=True, blank=True)   # 예금/적금, 주식, 펀드/ETF, 채권, 가상자산, 기타(직접입력)
    name            = models.CharField(max_length=100, null=True, blank=True)
    principal       = models.BigIntegerField(null=True, blank=True)     # 원금
    current_value   = models.BigIntegerField(null=True, blank=True)     # 현재 평가금액
    monthly_savings = models.IntegerField(null=True, blank=True)        # 월 적립액 (선택)
    purpose         = models.CharField(max_length=50, null=True, blank=True)   # 목적: 자산증식, 목돈마련, 비상자금, 자녀교육, 노후준비, 기타(직접입력)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'consulting_financial_assets'

    def __str__(self):
        return f"세션 #{self.session_id} 금융자산: {self.name}"


# 연금자산 (1:N)
class PensionAsset(models.Model):
    class ReceiveMethod(models.TextChoices):
        ANNUITY  = '연금형'
        LUMP_SUM = '일시금'

    session              = models.ForeignKey(ConsultingSession, on_delete=models.CASCADE, related_name='pension_assets')
    type                 = models.CharField(max_length=50, null=True, blank=True)   # 연금저축, 연금보험, 저축보험, 퇴직연금(IRP), 국민연금, 기타(직접입력)
    name                 = models.CharField(max_length=100, null=True, blank=True)
    current_value        = models.BigIntegerField(null=True, blank=True)            # 평가금액
    monthly_savings      = models.IntegerField(null=True, blank=True)               
    expected_return_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)   # 기대수익률
    subscription_date    = models.DateField(null=True, blank=True)      # 가입일
    payment_period_years = models.SmallIntegerField(null=True, blank=True)      # 납입기간  
    receive_method       = models.CharField(max_length=5, choices=ReceiveMethod.choices, null=True, blank=True)     # 수령방식
    pension_start_age    = models.SmallIntegerField(null=True, blank=True)      # 개시연령
    pension_period       = models.CharField(max_length=50, null=True, blank=True)   # 연금 기간: 10년, 15년, ..., 종신, 기타(직접입력)
    expected_amount      = models.IntegerField(null=True, blank=True)       # 예상수령액 (원/월)
    created_at           = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'consulting_pension_assets'

    def __str__(self):
        return f"세션 #{self.session_id} 연금자산: {self.name}"


# 부동산자산 (1:N)
class RealEstateAsset(models.Model):
    session               = models.ForeignKey(ConsultingSession, on_delete=models.CASCADE, related_name='real_estate_assets')
    type                  = models.CharField(max_length=50, null=True, blank=True)   # 주택/아파트, 오피스텔, 빌라, 상가, 기타(직접입력)
    name                  = models.CharField(max_length=200, null=True, blank=True)
    current_value         = models.BigIntegerField(null=True, blank=True)       # 평가금액
    acquisition_price     = models.BigIntegerField(null=True, blank=True)       # 취득가액
    acquisition_date      = models.DateField(null=True, blank=True)             # 취득시기
    monthly_rental_income = models.IntegerField(null=True, blank=True)          # 월세소득
    rental_deposit        = models.BigIntegerField(null=True, blank=True)       # 임대보증금
    secured_loan          = models.BigIntegerField(null=True, blank=True)       # 담보대출금
    created_at            = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'consulting_real_estate_assets'

    def __str__(self):
        return f"세션 #{self.session_id} 부동산: {self.name}"


# 부채 현황(1:N)
class Liability(models.Model):
    class RateType(models.TextChoices):
        FIXED    = '고정'
        VARIABLE = '변동'

    session            = models.ForeignKey(ConsultingSession, on_delete=models.CASCADE, related_name='liabilities')
    type               = models.CharField(max_length=50, null=True, blank=True)    # 신용대출, 임대보증금, 담보대출, 학자금대출, 기타(직접입력)
    name               = models.CharField(max_length=200, null=True, blank=True)
    balance            = models.BigIntegerField(null=True, blank=True)      # 대출잔액
    interest_rate      = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)     # 대출금리
    rate_type          = models.CharField(max_length=5, choices=RateType.choices, null=True, blank=True)    # 금리유형
    base_rate          = models.CharField(max_length=20, null=True, blank=True)    # 기준금리: COFIX, CD 등 (변동금리일 때만)
    repayment_method   = models.CharField(max_length=50, null=True, blank=True)    # 상환방식: 만기일시, 원리금분할상환, 원금분할상환, 기타(직접입력)
    loan_date          = models.DateField(null=True, blank=True)        # 대출일시
    maturity_date      = models.DateField(null=True, blank=True)        # 만기일시
    grace_period_years = models.SmallIntegerField(null=True, blank=True)    # 거치기간
    created_at         = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'consulting_liabilities'

    def __str__(self):
        return f"세션 #{self.session_id} 부채: {self.name}"


# 소득 현황(1:N)
class Income(models.Model):
    session    = models.ForeignKey(ConsultingSession, on_delete=models.CASCADE, related_name='incomes')
    type       = models.CharField(max_length=50, null=True, blank=True)   # 근로소득, 사업소득, 이자소득, 배당소득, 연금소득, 기타(직접입력)
    name       = models.CharField(max_length=100, null=True, blank=True)  # 내용: 급여, 이자수익 등
    amount     = models.BigIntegerField(null=True, blank=True)
    is_fixed   = models.BooleanField(null=True, blank=True)   # True=고정, False=변동
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'consulting_incomes'

    def __str__(self):
        return f"세션 #{self.session_id} 소득({self.type})"


# 지출 현황(1:N)
class Expense(models.Model):
    session    = models.ForeignKey(ConsultingSession, on_delete=models.CASCADE, related_name='expenses')
    name       = models.CharField(max_length=100, null=True, blank=True)   # 주거비/식비/생활비/... (기타 선택 시 자유 텍스트)
    amount     = models.BigIntegerField(null=True, blank=True)
    is_fixed   = models.BooleanField(null=True, blank=True)        # True=고정, False=변동
    frequency  = models.JSONField(null=True, blank=True)           # 주기: {"none": bool, "unit": "연"|"월"|"주", "count": int}
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'consulting_expenses'

    def __str__(self):
        return f"세션 #{self.session_id} 지출: {self.name}"


# 재무목표 — 기간별 지출 (1:N + 중첩 1:N)
class FinancialGoalPeriod(models.Model):
    PHASE_CHOICES = [
        ('before', '은퇴 전'),
        ('after',  '은퇴 후'),
    ]
    session    = models.ForeignKey(ConsultingSession, on_delete=models.CASCADE, related_name='goal_periods')
    phase      = models.CharField(max_length=10, choices=PHASE_CHOICES)
    from_age   = models.SmallIntegerField()
    to_age     = models.SmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'consulting_goal_periods'

    def __str__(self):
        return f"세션 #{self.session_id} 목표기간({self.phase}): {self.from_age}~{self.to_age}세"


class FinancialGoalPeriodItem(models.Model):
    period   = models.ForeignKey(FinancialGoalPeriod, on_delete=models.CASCADE, related_name='items')
    name     = models.CharField(max_length=100, null=True, blank=True)
    amount   = models.BigIntegerField(null=True, blank=True)   # 원 단위 정수 (프론트에서 변환)
    is_fixed = models.BooleanField(null=True, blank=True)

    class Meta:
        db_table = 'consulting_goal_period_items'

    def __str__(self):
        return f"기간 #{self.period_id} 항목: {self.name}"


# 재무목표 — 마일스톤: 예상 달성자금/예상 지출 (1:N) 
class FinancialGoalMilestone(models.Model):
    PHASE_CHOICES = [
        ('before', '은퇴 전'),
        ('after',  '은퇴 후'),
    ]

    class Category(models.TextChoices):
        INCOME  = '소득'
        EXPENSE = '지출'

    session    = models.ForeignKey(ConsultingSession, on_delete=models.CASCADE, related_name='goal_milestones')
    phase      = models.CharField(max_length=10, choices=PHASE_CHOICES)
    age        = models.SmallIntegerField(null=True, blank=True)
    category   = models.CharField(max_length=5, choices=Category.choices, null=True, blank=True)
    content    = models.CharField(max_length=200, null=True, blank=True)
    amount     = models.BigIntegerField(null=True, blank=True)               # 원 단위 정수 (프론트에서 변환)
    note       = models.CharField(max_length=200, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'consulting_goal_milestones'

    def __str__(self):
        return f"세션 #{self.session_id} 마일스톤({self.phase}): {self.age}세"
