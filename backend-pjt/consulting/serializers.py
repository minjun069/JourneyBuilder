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
    if value in ('', None):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return value

    raw = value.replace(',', '').replace(' ', '').strip()
    if not raw:
        return None
    if '억' not in raw and '만' not in raw:
        try:
            return int(float(raw))
        except ValueError:
            return value

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

    return int(round(total))


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


def validate_non_negative(attrs, *fields):
    errors = {}
    for field in fields:
        value = attrs.get(field)
        if value is not None and value < 0:
            errors[field] = '0 이상이어야 합니다.'
    if errors:
        raise serializers.ValidationError(errors)


def validate_range(attrs, field, minimum, maximum):
    value = attrs.get(field)
    if value is not None and not (minimum <= value <= maximum):
        raise serializers.ValidationError({
            field: f'{minimum}~{maximum} 사이 값이어야 합니다.'
        })


def validate_frequency(value):
    if value is None:
        return value
    if not isinstance(value, dict):
        raise serializers.ValidationError('객체 형식이어야 합니다.')
    none = value.get('none')
    if not isinstance(none, bool):
        raise serializers.ValidationError('none은 boolean이어야 합니다.')
    if none:
        return {'none': True, 'unit': value.get('unit') or '월', 'count': None}
    unit = value.get('unit')
    count = value.get('count')
    if unit not in ('연', '월', '주'):
        raise serializers.ValidationError('unit은 연, 월, 주 중 하나여야 합니다.')
    try:
        count = int(count)
    except (TypeError, ValueError):
        raise serializers.ValidationError('count는 1 이상의 정수여야 합니다.')
    if count < 1:
        raise serializers.ValidationError('count는 1 이상의 정수여야 합니다.')
    return {'none': False, 'unit': unit, 'count': count}


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

    def validate(self, attrs):
        validate_range(attrs, 'age', 18, 100)
        validate_range(attrs, 'retirement_age', 18, 100)
        validate_range(attrs, 'end_age', 18, 120)
        validate_non_negative(attrs, 'monthly_income')

        def current_value(field_name):
            if field_name in attrs:
                return attrs[field_name]
            return getattr(self.instance, field_name, None)

        age = current_value('age')
        retirement_age = current_value('retirement_age')
        end_age = current_value('end_age')
        if age is not None and retirement_age is not None and retirement_age < age:
            raise serializers.ValidationError({
                'retirement_age': '현재 나이 이상이어야 합니다.'
            })
        if retirement_age is not None and end_age is not None and end_age <= retirement_age:
            raise serializers.ValidationError({
                'end_age': '은퇴 예정 나이보다 커야 합니다.'
            })
        return attrs

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

    def validate(self, attrs):
        validate_non_negative(attrs, 'principal', 'current_value', 'monthly_savings')
        return attrs


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

    def validate(self, attrs):
        validate_non_negative(
            attrs, 'current_value', 'monthly_savings', 'expected_return_rate',
            'expected_amount',
        )
        validate_range(attrs, 'expected_return_rate', 0, 20)
        validate_range(attrs, 'payment_period_years', 1, 50)
        validate_range(attrs, 'pension_start_age', 55, 80)
        return attrs


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

    def validate(self, attrs):
        validate_non_negative(
            attrs, 'current_value', 'acquisition_price',
            'monthly_rental_income', 'rental_deposit', 'secured_loan',
        )
        return attrs


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

    def validate(self, attrs):
        validate_non_negative(attrs, 'balance', 'interest_rate', 'grace_period_years')
        validate_range(attrs, 'interest_rate', 0, 30)
        validate_range(attrs, 'grace_period_years', 0, 10)

        def current_value(field_name):
            if field_name in attrs:
                return attrs[field_name]
            return getattr(self.instance, field_name, None)

        loan_date = current_value('loan_date')
        maturity_date = current_value('maturity_date')
        if loan_date and maturity_date and maturity_date < loan_date:
            raise serializers.ValidationError({
                'maturity_date': '대출일 이후여야 합니다.'
            })
        return attrs


# 소득
class IncomeSerializer(KoreanMoneyFieldsMixin, serializers.ModelSerializer):
    money_fields = ('amount',)

    class Meta:
        model  = Income
        fields = ['id', 'type', 'name', 'amount', 'is_fixed', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate(self, attrs):
        validate_non_negative(attrs, 'amount')
        return attrs


# 지출
class ExpenseSerializer(KoreanMoneyFieldsMixin, serializers.ModelSerializer):
    money_fields = ('amount',)

    class Meta:
        model  = Expense
        fields = ['id', 'name', 'amount', 'is_fixed', 'frequency', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate_amount(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError('0 이상이어야 합니다.')
        return value

    def validate_frequency(self, value):
        return validate_frequency(value)


# 재무목표
class FinancialGoalPeriodItemSerializer(KoreanMoneyFieldsMixin, serializers.ModelSerializer):
    money_fields = ('amount',)

    class Meta:
        model  = FinancialGoalPeriodItem
        fields = ['id', 'name', 'amount', 'is_fixed']
        read_only_fields = ['id']
        # amount: BigIntegerField, 프론트에서 원 단위 정수로 변환 후 전송

    def validate_amount(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError('0 이상이어야 합니다.')
        return value


class FinancialGoalPeriodSerializer(serializers.ModelSerializer):
    items = FinancialGoalPeriodItemSerializer(many=True)

    class Meta:
        model  = FinancialGoalPeriod
        fields = ['id', 'phase', 'from_age', 'to_age', 'items', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate(self, attrs):
        def current_value(field_name):
            if field_name in attrs:
                return attrs[field_name]
            return getattr(self.instance, field_name, None)

        from_age = current_value('from_age')
        to_age = current_value('to_age')
        if from_age is not None and not (1 <= from_age <= 120):
            raise serializers.ValidationError({'from_age': '1~120 사이 값이어야 합니다.'})
        if to_age is not None and not (1 <= to_age <= 120):
            raise serializers.ValidationError({'to_age': '1~120 사이 값이어야 합니다.'})
        if from_age is not None and to_age is not None and from_age >= to_age:
            raise serializers.ValidationError({'to_age': '시작 나이보다 커야 합니다.'})
        return attrs

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

    def validate(self, attrs):
        validate_range(attrs, 'age', 1, 120)
        validate_non_negative(attrs, 'amount')
        return attrs


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
