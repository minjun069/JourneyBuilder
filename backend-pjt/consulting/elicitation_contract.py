"""Typed financial-mindset dialogue metadata, separate from form actions."""

FIELDS = (
    'values', 'past_experience', 'current_situation', 'current_challenge',
    'invest_philosophy', 'future_goals',
)
MODE = 'mindset_elicitation'
STEP = 'financial-mindset'
START_CONTENT = '경제관념 항목 상담을 시작합니다.'


def validate_request(state, *, request_mode, step_id, content):
    if request_mode != MODE:
        if state is not None:
            raise ValueError('elicitation_state는 경제관념 상담에서만 사용할 수 있습니다.')
        return None
    if step_id != STEP:
        raise ValueError('경제관념 상담 단계가 올바르지 않습니다.')
    if not isinstance(state, dict) or set(state) != {
        'event', 'field', 'answer_count', 'completed_fields',
    }:
        raise ValueError('elicitation_state 구조가 올바르지 않습니다.')
    field = state['field']
    if field not in FIELDS or state['event'] not in ('start', 'answer'):
        raise ValueError('경제관념 상담 항목 또는 이벤트가 올바르지 않습니다.')
    count = state['answer_count']
    if type(count) is not int or not 0 <= count <= 2:
        raise ValueError('answer_count는 0~2 정수여야 합니다.')
    if state['completed_fields'] != list(FIELDS[:FIELDS.index(field)]):
        raise ValueError('completed_fields는 현재 항목 이전의 순서와 일치해야 합니다.')
    if state['event'] == 'start' and (count != 0 or content != START_CONTENT):
        raise ValueError('경제관념 시작 이벤트가 올바르지 않습니다.')
    return {**state, 'completed_fields': list(state['completed_fields'])}


def validate_output(output, *, request_payload=None):
    if request_payload and request_payload.get('request_mode') == MODE:
        if any(output.get(key) is not None for key in ('action', 'review', 'presentation_entry_status')):
            raise ValueError('경제관념 상담은 별도 elicitation 결과만 반환할 수 있습니다.')
    result = output.get('elicitation')
    if result is None:
        if 'elicitation' in output:
            raise ValueError('elicitation은 객체여야 합니다.')
        return
    if not isinstance(result, dict) or set(result) != {
        'field', 'summary', 'answer_count', 'status', 'completion_reason', 'next_field',
    }:
        raise ValueError('elicitation 구조가 올바르지 않습니다.')
    field = result['field']
    count = result['answer_count']
    status = result['status']
    reason = result['completion_reason']
    if field not in FIELDS or type(count) is not int or not 0 <= count <= 3:
        raise ValueError('elicitation 항목 또는 답변 횟수가 올바르지 않습니다.')
    if not isinstance(result['summary'], str):
        raise ValueError('elicitation.summary는 문자열이어야 합니다.')
    if output.get('step_id') not in (None, STEP):
        raise ValueError('elicitation은 경제관념 단계 결과여야 합니다.')
    if any(output.get(key) is not None for key in ('action', 'review', 'presentation_entry_status')):
        raise ValueError('elicitation과 다른 변경·검토 결과를 함께 반환할 수 없습니다.')
    if status == 'in_progress':
        if count > 2 or reason is not None or result['next_field'] is not None:
            raise ValueError('진행 중인 경제관념 결과가 올바르지 않습니다.')
    elif status == 'completed':
        index = FIELDS.index(field)
        next_field = FIELDS[index + 1] if index + 1 < len(FIELDS) else None
        if result['next_field'] != next_field:
            raise ValueError('next_field가 다음 경제관념 항목과 다릅니다.')
        if reason not in ('sufficient', 'limit', 'skipped'):
            raise ValueError('경제관념 완료 사유가 올바르지 않습니다.')
        if reason == 'skipped':
            if count > 2:
                raise ValueError('건너뛰기는 답변 횟수를 소모하지 않습니다.')
        elif count == 0 or not result['summary'].strip():
            raise ValueError('완료된 경제관념 답변에는 요약이 필요합니다.')
        if reason == 'limit' and count != 3:
            raise ValueError('횟수 제한 완료는 세 번째 답변이어야 합니다.')
    else:
        raise ValueError('elicitation.status가 올바르지 않습니다.')
    if request_payload is None:
        return
    if request_payload.get('request_mode') != MODE or request_payload.get('step_id') != STEP:
        raise ValueError('경제관념 요청이 아닌 결과에 elicitation이 있습니다.')
    state = request_payload.get('elicitation_state')
    if not isinstance(state, dict) or field != state.get('field'):
        raise ValueError('elicitation 항목이 원래 요청과 다릅니다.')
    expected_count = state.get('answer_count')
    if state.get('event') == 'start':
        if status != 'in_progress' or count != 0:
            raise ValueError('시작 요청은 답변 수집을 완료할 수 없습니다.')
    elif status == 'in_progress' and count == expected_count:
        # A term clarification does not consume one of the three answers.
        return
    elif reason != 'skipped':
        expected_count += 1
    if count != expected_count:
        raise ValueError('elicitation 답변 횟수가 원래 요청과 다릅니다.')
