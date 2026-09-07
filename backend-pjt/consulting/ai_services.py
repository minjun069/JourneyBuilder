import json
import uuid

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction

from .models import (
    BasicInfo,
    FinancialMindset,
    FinancialSatisfaction,
    Message,
)
from .serializers import (
    BasicInfoWriteSerializer,
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
from .field_registry import get_step_field_registry
from .form_actions import normalize_form_action


AI_STATUS_PROCESSING = 'processing'
AI_STATUS_COMPLETED = 'completed'
AI_STATUS_FAILED = 'failed'


ONE_TO_ONE_SECTIONS = {
    'basic_info': (BasicInfo, BasicInfoWriteSerializer),
    'financial_mindset': (FinancialMindset, FinancialMindsetSerializer),
    'financial_satisfaction': (FinancialSatisfaction, FinancialSatisfactionSerializer),
}

FIELD_SECTION_MAP = {}
for section_name, (model_class, _) in ONE_TO_ONE_SECTIONS.items():
    for field in model_class._meta.fields:
        if field.name not in ('id', 'session', 'updated_at'):
            FIELD_SECTION_MAP[field.name] = section_name


def normalize_change_value(section, field, value):
    if section == 'basic_info' and field == 'monthly_income' and value is not None:
        try:
            amount = int(value)
        except (TypeError, ValueError):
            return value
        if 0 < amount < 10000:
            return amount * 10000
        return amount
    return value


# 백엔드가 AI 서버에게 답변 만들어달라는 작업 각각의 고유 번호
def generate_request_id():
    return f'ai_req_{uuid.uuid4().hex}'


def build_thread_id(user, session):
    return f'user-{user.id}-session-{session.id}'


def redis_key(request_id):
    return f'ai:request:{request_id}'


def redis_thread_key(thread_id):
    return f'ai:thread:{thread_id}'


def get_redis_client():
    try:
        import redis
    except ImportError as exc:
        raise RuntimeError('redis 패키지가 설치되어 있지 않습니다.') from exc

    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def save_ai_request_state(request_id, payload):
    client = get_redis_client()
    client.setex(
        redis_key(request_id),
        settings.AI_REQUEST_TTL_SECONDS,
        json.dumps(payload, ensure_ascii=False, cls=DjangoJSONEncoder),
    )
    if payload.get('thread_id'):
        client.setex(
            redis_thread_key(payload['thread_id']),
            settings.AI_REQUEST_TTL_SECONDS,
            request_id,
        )


def get_ai_request_state(request_id):
    client = get_redis_client()
    raw = client.get(redis_key(request_id))
    if not raw:
        return None
    return json.loads(raw)


def get_ai_request_state_by_thread_id(thread_id):
    client = get_redis_client()
    request_id = client.get(redis_thread_key(thread_id))
    if not request_id:
        return None
    return get_ai_request_state(request_id)


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


def build_ai_request_payload(request_id, session, user, message, step_id, form_data=None):
    return {
        'request_id': request_id,
        'session_id': session.id,
        'thread_id': build_thread_id(user, session),
        'step_id': step_id,
        'message': message,
        'customer_info': build_customer_info(session, form_data),
        'field_registry': get_step_field_registry(step_id),
        'callback_url': settings.AI_CALLBACK_URL,
    }


def dispatch_ai_request(payload):
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError('httpx 패키지가 설치되어 있지 않습니다.') from exc

    headers = {}
    if settings.AI_CALLBACK_SECRET:
        headers['X-Internal-Secret'] = settings.AI_CALLBACK_SECRET

    response = httpx.post(
        settings.AI_SERVICE_URL,
        json=payload,
        headers=headers,
        timeout=settings.AI_SERVICE_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def stream_ai_response(payload):
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError('httpx 패키지가 설치되어 있지 않습니다.') from exc

    headers = {'Accept': 'text/event-stream'}
    if settings.AI_CALLBACK_SECRET:
        headers['X-Internal-Secret'] = settings.AI_CALLBACK_SECRET

    stream_payload = {
        key: value
        for key, value in payload.items()
        if key != 'callback_url'
    }
    timeout = httpx.Timeout(
        connect=settings.AI_SERVICE_TIMEOUT_SECONDS,
        read=settings.AI_STREAM_READ_TIMEOUT_SECONDS,
        write=settings.AI_SERVICE_TIMEOUT_SECONDS,
        pool=settings.AI_SERVICE_TIMEOUT_SECONDS,
    )
    with httpx.stream(
        'POST',
        settings.AI_SERVICE_STREAM_URL,
        json=stream_payload,
        headers=headers,
        timeout=timeout,
    ) as response:
        response.raise_for_status()
        yield from response.iter_lines()


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


def mark_message_action(message, action):
    extracted_data = message.extracted_data or {}
    extracted_data['action_status'] = action
    message.extracted_data = extracted_data
    message.save(update_fields=['extracted_data'])


def apply_pending_changes(session, message):
    extracted_data = message.extracted_data or {}
    pending_changes = extracted_data.get('pending_changes') or []
    applied = []

    with transaction.atomic():
        for change in pending_changes:
            section = change.get('section')
            field = change.get('field')
            value = change.get('value')

            if section not in ONE_TO_ONE_SECTIONS:
                raise ValueError(f'지원하지 않는 section입니다: {section}')

            model_class, serializer_class = ONE_TO_ONE_SECTIONS[section]
            allowed_fields = {
                f.name for f in model_class._meta.fields
                if f.name not in ('id', 'session', 'updated_at')
            }
            if field not in allowed_fields:
                raise ValueError(f'{section}.{field} 필드는 수정할 수 없습니다.')

            obj, _ = model_class.objects.get_or_create(session=session)
            value = normalize_change_value(section, field, value)
            serializer = serializer_class(obj, data={field: value}, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            applied.append({**change, 'value': value})

        mark_message_action(message, 'approved')
        session.refresh_completion_status()

    return applied


def create_assistant_message_from_callback(session, payload, step_id=None):
    output = payload.get('output') or {}
    content = payload.get('content') or output.get('response') or ''
    extracted_data = payload.get('extracted_data')
    if step_id is None:
        request_state = get_ai_request_state(payload.get('request_id')) or {}
        step_id = request_state.get('step_id') or output.get('step_id')
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
