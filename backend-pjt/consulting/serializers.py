from rest_framework import serializers
from .models import (
    ConsultingSession, Message,
    BasicInfo,
    FinancialMindset, FinancialSatisfaction,
    FinancialAsset, PensionAsset, RealEstateAsset,
    Liability, Income, Expense,
    FinancialGoalPeriod, FinancialGoalPeriodItem, FinancialGoalMilestone,
)


def parse_korean_money(value, default_unit='man'):
    small_unit_multiplier = 100000000 if default_unit == 'eok' else 10000

    if value in ('', None):
        return None
    if isinstance(value, (int, float)):
        if value == 0:
            return None
        amount = int(value)
        return amount * small_unit_multiplier if 0 < amount < 10000 else amount
    if not isinstance(value, str):
        return value

    raw = value.replace(',', '').replace(' ', '').strip()
    if not raw:
        return None
    if '억' not in raw and '만' not in raw:
        try:
            amount = int(float(raw))
        except ValueError:
            return value
        return amount * small_unit_multiplier if 0 < amount < 10000 else amount

    import re

    total = 0
    matched = False
    pattern = re.compile(r'(-?\d+(?:\.\d+)?)(억|만)')
    for amount, unit in pattern.findall(raw):
        matched = True
        multiplier = 100000000 if unit == '억' else 10000
        total += float(amount) * multiplier

    if not matched:
        return value

    remainder = pattern.sub('', raw)
    if remainder:
        try:
            extra = float(remainder)
        except ValueError:
            pass
        else:
            if '억' in raw and '만' not in raw and 0 < extra < 10000:
                total += extra * 10000
            else:
                total += extra

    return int(round(total)) if total != 0 else None


class KoreanMoneyFieldsMixin:
    money_fields = ()
    money_field_default_units = {}

    def to_internal_value(self, data):
        if isinstance(data, dict):
            data = data.copy()
            for field in self.money_fields:
                if field in data:
                    data[field] = parse_korean_money(
                        data[field],
                        self.money_field_default_units.get(field, 'man'),
                    )
        return super().to_internal_value(data)


# 기본 정보 
class BasicInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model  = BasicInfo
        fields = [
            'age', 'job', 'marital_status', 'dependents', 'tendency',
            'interest', 'need', 'monthly_income',
            'retirement_age', 'end_age', 'updated_at',
        ]
        read_only_fields = ['updated_at']


class BasicInfoWriteSerializer(KoreanMoneyFieldsMixin, serializers.ModelSerializer):
    money_fields = ('monthly_income',)

    class Meta:
        model  = BasicInfo
        fields = [
            'age', 'job', 'marital_status', 'dependents', 'tendency',
            'interest', 'need', 'monthly_income',
            'retirement_age', 'end_age',
        ]

    def validate_monthly_income(self, value):
        if value is not None and 0 < value < 10000:
            return value * 10000
        return value

    def save_to_session(self, session):
        obj, _ = BasicInfo.objects.update_or_create(
            session=session,
            defaults=self.validated_data,
        )
        return obj


# 경제 관념 
class FinancialMindsetSerializer(serializers.ModelSerializer):
    class Meta:
        model  = FinancialMindset
        fields = [
            'values', 'past_experience', 'current_situation',
            'current_challenge', 'invest_philosophy', 'future_goals',
            'updated_at',
        ]
        read_only_fields = ['updated_at']

    def save_to_session(self, session):
        obj, _ = FinancialMindset.objects.update_or_create(
            session=session,
            defaults=self.validated_data,
        )
        return obj


# 재무 만족도
class FinancialSatisfactionSerializer(serializers.ModelSerializer):
    class Meta:
        model  = FinancialSatisfaction
        fields = [
            'cashflow_score', 'risk_score', 'investment_score',
            'management_score', 'inheritance_score',
            'updated_at',
        ]
        read_only_fields = ['updated_at']

    def validate(self, attrs):
        for field, value in attrs.items():
            if field == 'updated_at':
                continue
            if value is not None and not (1 <= value <= 5):
                raise serializers.ValidationError(f'{field}는 1~5 사이 값이어야 합니다.')
        return attrs

    def save_to_session(self, session):
        obj, _ = FinancialSatisfaction.objects.update_or_create(
            session=session,
            defaults=self.validated_data,
        )
        return obj


# 금융자산
class FinancialAssetSerializer(KoreanMoneyFieldsMixin, serializers.ModelSerializer):
    money_fields = ('principal', 'current_value', 'monthly_savings')

    class Meta:
        model  = FinancialAsset
        fields = ['id', 'asset_type', 'name', 'principal', 'current_value', 'monthly_savings', 'purpose', 'created_at']
        read_only_fields = ['id', 'created_at']


# 연금자산
class PensionAssetSerializer(KoreanMoneyFieldsMixin, serializers.ModelSerializer):
    money_fields = ('current_value', 'monthly_savings', 'expected_amount')

    class Meta:
        model  = PensionAsset
        fields = [
            'id', 'type', 'name', 'current_value', 'monthly_savings',
            'expected_return_rate', 'subscription_date', 'payment_period_years',
            'receive_method', 'pension_start_age', 'pension_period', 'expected_amount',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


# 부동산자산
class RealEstateAssetSerializer(KoreanMoneyFieldsMixin, serializers.ModelSerializer):
    money_fields = (
        'current_value', 'acquisition_price', 'monthly_rental_income',
        'rental_deposit', 'secured_loan',
    )
    money_field_default_units = {
        'current_value': 'eok',
        'acquisition_price': 'eok',
        'secured_loan': 'eok',
    }

    class Meta:
        model  = RealEstateAsset
        fields = [
            'id', 'type', 'name', 'current_value', 'acquisition_price',
            'acquisition_date', 'monthly_rental_income', 'rental_deposit',
            'secured_loan', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


# 부채
class LiabilitySerializer(KoreanMoneyFieldsMixin, serializers.ModelSerializer):
    money_fields = ('balance',)
    money_field_default_units = {'balance': 'eok'}

    class Meta:
        model  = Liability
        fields = [
            'id', 'type', 'name', 'balance', 'interest_rate',
            'rate_type', 'base_rate', 'repayment_method',
            'loan_date', 'maturity_date', 'grace_period_years', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


# 소득
class IncomeSerializer(KoreanMoneyFieldsMixin, serializers.ModelSerializer):
    money_fields = ('amount',)

    class Meta:
        model  = Income
        fields = ['id', 'type', 'name', 'amount', 'is_fixed', 'created_at']
        read_only_fields = ['id', 'created_at']


# 지출
class ExpenseSerializer(KoreanMoneyFieldsMixin, serializers.ModelSerializer):
    money_fields = ('amount',)

    class Meta:
        model  = Expense
        fields = ['id', 'name', 'amount', 'is_fixed', 'frequency', 'created_at']
        read_only_fields = ['id', 'created_at']


# 재무목표
class FinancialGoalPeriodItemSerializer(KoreanMoneyFieldsMixin, serializers.ModelSerializer):
    money_fields = ('amount',)

    class Meta:
        model  = FinancialGoalPeriodItem
        fields = ['id', 'name', 'amount', 'is_fixed']
        read_only_fields = ['id']
        # amount: BigIntegerField, 프론트에서 원 단위 정수로 변환 후 전송


class FinancialGoalPeriodSerializer(serializers.ModelSerializer):
    items = FinancialGoalPeriodItemSerializer(many=True)

    class Meta:
        model  = FinancialGoalPeriod
        fields = ['id', 'phase', 'from_age', 'to_age', 'items', 'created_at']
        read_only_fields = ['id', 'created_at']

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        period = FinancialGoalPeriod.objects.create(**validated_data)
        FinancialGoalPeriodItem.objects.bulk_create([
            FinancialGoalPeriodItem(period=period, **item) for item in items_data
        ])
        return period


# 재무목표 - 마일스톤
class FinancialGoalMilestoneSerializer(KoreanMoneyFieldsMixin, serializers.ModelSerializer):
    money_fields = ('amount',)
    money_field_default_units = {'amount': 'eok'}

    class Meta:
        model  = FinancialGoalMilestone
        fields = ['id', 'phase', 'age', 'category', 'content', 'amount', 'note', 'created_at']
        read_only_fields = ['id', 'created_at']


# Message
class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Message
        fields = ['id', 'role', 'content', 'extracted_data', 'created_at']
        read_only_fields = fields


class MessageCreateSerializer(serializers.Serializer):
    step_id = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
    )
    content = serializers.CharField(
        min_length=1,
        max_length=2000,
        error_messages={
            'blank':      '메시지 내용을 입력해주세요.',
            'max_length': '메시지는 2000자 이하여야 합니다.',
        }
    )
    form_data = serializers.JSONField(required=False)

    def validate_content(self, value):
        return value.strip()

    def validate_step_id(self, value):
        value = value.strip()
        return value or 'general'

    def validate_form_data(self, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError('form_data는 객체 형식이어야 합니다.')
        return value


class ConsultingSessionListSerializer(serializers.ModelSerializer):
    progress_pct = serializers.SerializerMethodField()
    section_completion = serializers.SerializerMethodField()
    is_ready_for_reports = serializers.SerializerMethodField()

    class Meta:
        model  = ConsultingSession
        fields = [
            'id', 'status', 'progress_pct', 'section_completion',
            'is_ready_for_reports', 'completed_at', 'created_at',
        ]
        read_only_fields = fields

    def get_progress_pct(self, obj):
        return obj.get_progress_pct()

    def get_section_completion(self, obj):
        return obj.get_section_completion()

    def get_is_ready_for_reports(self, obj):
        return obj.is_ready_for_reports()


class ConsultingSessionSerializer(serializers.ModelSerializer):
    progress_pct = serializers.SerializerMethodField()
    section_completion = serializers.SerializerMethodField()
    is_ready_for_reports = serializers.SerializerMethodField()

    class Meta:
        model  = ConsultingSession
        fields = [
            'id', 'status', 'progress_pct', 'section_completion',
            'is_ready_for_reports', 'completed_at', 'created_at',
        ]
        read_only_fields = fields

    def get_progress_pct(self, obj):
        return obj.get_progress_pct()

    def get_section_completion(self, obj):
        return obj.get_section_completion()

    def get_is_ready_for_reports(self, obj):
        return obj.is_ready_for_reports()


class ConsultingSessionCreateSerializer(serializers.Serializer):
    pass
