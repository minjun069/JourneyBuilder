import uuid

from .models import (
    BasicInfo,
    FinancialMindset,
    FinancialSatisfaction,
    Message,
)
from .serializers import (
    BasicInfoSerializer,
    ExpenseSerializer,
    FinancialAssetSerializer,
    FinancialGoalMilestoneSerializer,
    FinancialGoalPeriodSerializer,
    FinancialMindsetSerializer,
    FinancialSatisfactionSerializer,
    IncomeSerializer,
    LiabilitySerializer,
    PensionAssetSerializer,
    RealEstateAssetSerializer,
)
from .form_actions import normalize_form_action


ONE_TO_ONE_SECTIONS = {
    'basic_info': BasicInfo,
    'financial_mindset': FinancialMindset,
    'financial_satisfaction': FinancialSatisfaction,
}

FIELD_SECTION_MAP = {}
for section_name, model_class in ONE_TO_ONE_SECTIONS.items():
    for field in model_class._meta.fields:
        if field.name not in ('id', 'session', 'updated_at'):
            FIELD_SECTION_MAP[field.name] = section_name


# 백엔드가 AI 서버에게 답변 만들어달라는 작업 각각의 고유 번호
def generate_request_id():
    return f'ai_req_{uuid.uuid4().hex}'


def build_thread_id(user, session):
    return f'user-{user.id}-session-{session.id}'


def serialize_one_to_one(session, attr_name, serializer_class):
    try:
        obj = getattr(session, attr_name)
    except serializer_class.Meta.model.DoesNotExist:
        return None
    return serializer_class(obj).data


def build_customer_info(session, form_data=None):
    goal_periods = session.goal_periods.prefetch_related('items').all()
    return {
        'saved_data': {
            'basic_info': serialize_one_to_one(session, 'basic_info', BasicInfoSerializer),
            'financial_mindset': serialize_one_to_one(
                session,
                'financial_mindset',
                FinancialMindsetSerializer,
            ),
            'financial_satisfaction': serialize_one_to_one(
                session,
                'financial_satisfaction',
                FinancialSatisfactionSerializer,
            ),
            'financial_assets': FinancialAssetSerializer(session.financial_assets.all(), many=True).data,
            'pension_assets': PensionAssetSerializer(session.pension_assets.all(), many=True).data,
            'real_estate_assets': RealEstateAssetSerializer(session.real_estate_assets.all(), many=True).data,
            'liabilities': LiabilitySerializer(session.liabilities.all(), many=True).data,
            'incomes': IncomeSerializer(session.incomes.all(), many=True).data,
            'expenses': ExpenseSerializer(session.expenses.all(), many=True).data,
            'goal_periods': FinancialGoalPeriodSerializer(goal_periods, many=True).data,
            'goal_milestones': FinancialGoalMilestoneSerializer(session.goal_milestones.all(), many=True).data,
        },
        'current_form': form_data or {},
    }


def normalize_pending_changes(update, default_section=None):
    if isinstance(update, list):
        changes = update
    elif isinstance(update, dict):
        changes = [
            {
                'section': FIELD_SECTION_MAP.get(field) or default_section,
                'field': field,
                'value': value,
                'label': field,
            }
            for field, value in update.items()
        ]
    else:
        return []

    normalized = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        field = change.get('field')
        normalized.append({
            **change,
            'section': change.get('section') or FIELD_SECTION_MAP.get(field) or default_section,
            'label': change.get('label') or field,
        })
    return normalized


def create_assistant_message_from_terminal(session, payload, step_id):
    output = payload.get('output') or {}
    content = payload.get('content') or output.get('response') or ''
    extracted_data = payload.get('extracted_data')
    form_action = normalize_form_action(output, step_id) if step_id else None

    if form_action:
        pending_changes = form_action['changes']
        extracted_data = {
            'intent': 'input_assist',
            'requires_approval': True,
            'action': form_action,
            'direct_changes': [],
            'pending_changes': pending_changes,
        }
    elif extracted_data is None:
        update = output.get('update') or {}
        pending_changes = normalize_pending_changes(update)
        if pending_changes:
            extracted_data = {
                'intent': 'input_assist',
                'requires_approval': True,
                'pending_changes': pending_changes,
            }
        else:
            extracted_data = {
                'intent': output.get('intent') or 'qa',
                'requires_approval': False,
                'pending_changes': [],
            }
    else:
        pending_changes = normalize_pending_changes(extracted_data.get('pending_changes') or [])
        extracted_data = {
            **extracted_data,
            'requires_approval': bool(pending_changes) or bool(extracted_data.get('requires_approval')),
            'pending_changes': pending_changes,
        }

    return Message.objects.create(
        session=session,
        role=Message.Role.ASSISTANT,
        content=content,
        extracted_data=extracted_data,
    )
