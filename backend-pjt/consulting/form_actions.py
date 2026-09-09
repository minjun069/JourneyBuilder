from math import isfinite

from .field_registry import validate_field_change


def normalize_form_action(output, step_id):
    action = output.get('action') if isinstance(output, dict) else None
    if not isinstance(action, dict) or action.get('type') != 'form_change':
        return None

    target = action.get('target') or 'draft_form'
    if target != 'draft_form':
        raise ValueError(f'지원하지 않는 form action target입니다: {target}')
    raw_confidence = action.get('confidence', 0)
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    if not isfinite(confidence):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    user_requested = action.get('user_requested') is True
    changes = []
    for raw in action.get('changes') or []:
        if not isinstance(raw, dict):
            continue
        section = raw.get('section')
        path = raw.get('path')
        operation = raw.get('operation')
        spec = validate_field_change(
            step_id=step_id,
            section=section,
            path=path,
            operation=operation,
        )
        change = {
            'section': section,
            'path': path,
            'operation': operation,
            'value': raw.get('value'),
            'label': raw.get('label') or spec.label,
            'reason': raw.get('reason') or '',
        }
        if raw.get('item_id') is not None:
            change['item_id'] = raw['item_id']
        if raw.get('client_item_id'):
            change['client_item_id'] = raw['client_item_id']
        if raw.get('parent_item_id') is not None:
            change['parent_item_id'] = raw['parent_item_id']
        if raw.get('parent_client_item_id'):
            change['parent_client_item_id'] = raw['parent_client_item_id']
        for metadata_field in (
            'policy_version', 'source', 'retrieved_at', 'transform', 'assumption_level',
            'source_turn_ids',
        ):
            if raw.get(metadata_field) is not None:
                change[metadata_field] = raw[metadata_field]
        changes.append(change)

    if not changes:
        return None
    return {
        'type': 'form_change',
        'step_id': step_id,
        'target': target,
        'mode': 'proposal',
        'user_requested': user_requested,
        'confidence': confidence,
        'changes': changes,
    }
