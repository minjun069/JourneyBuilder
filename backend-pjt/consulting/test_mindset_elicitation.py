import asyncio
import json
from contextlib import ExitStack
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from .ai_lifecycle import (
    LifecycleError, build_v2_ai_payload, canonical_digest,
    create_v2_assistant_message, validate_browser_message, validate_terminal_payload,
)
from .ai_tokenization import ReferenceTokenVault
from .elicitation_contract import MODE, START_CONTENT, STEP
from .test_v2_lifecycle import completed_terminal
from .test_v2_sse import async_items, collect_async, parse_frame, processing_state
from .v2_sse import proxy_v2_ai_stream
from .v2_views import V2MessageStreamView


def state(**updates):
    return {
        'event': 'answer', 'field': 'values', 'answer_count': 0,
        'completed_fields': [], **updates,
    }


def browser(**updates):
    return {
        'contract_version': '2.0', 'content': '가족의 안정된 생활이 중요해요.',
        'step_id': STEP, 'form_data': {}, 'request_mode': MODE,
        'elicitation_state': state(), **updates,
    }


def output(**updates):
    return {
        'response': '가족의 안정된 생활을 우선하시는군요. 그 기준이 생긴 경험이 있나요?',
        'step_id': STEP, 'action': None,
        'elicitation': {
            'field': 'values', 'summary': '가족의 안정된 생활을 중요하게 생각함.',
            'answer_count': 1, 'status': 'in_progress',
            'completion_reason': None, 'next_field': None, **updates,
        },
    }


class MindsetLifecycleTests(SimpleTestCase):
    def test_mode_state_step_and_start_content_are_bound(self):
        body = browser()
        self.assertEqual(validate_browser_message(body), body)
        for invalid in (
            browser(request_mode='chat'), browser(step_id='financial-goals'),
            browser(elicitation_state=None),
            browser(elicitation_state=state(event='start')),
            browser(elicitation_state=state(event='start', answer_count=1), content=START_CONTENT),
            browser(elicitation_state=state(field='past_experience')),
        ):
            with self.subTest(body=invalid), self.assertRaises(LifecycleError):
                validate_browser_message(invalid)
        start = browser(elicitation_state=state(event='start'), content=START_CONTENT)
        self.assertEqual(validate_browser_message(start), start)

    @patch('consulting.ai_lifecycle.build_customer_info', return_value={'saved_data': {}, 'current_form': {}})
    def test_state_survives_outbound_payload_and_participates_in_digest(self, _customer):
        progress = state()
        payload = build_v2_ai_payload(
            request_id='mindset-test', session=SimpleNamespace(id=12),
            user=SimpleNamespace(id=3), message='안정이 중요해요.', step_id=STEP,
            form_data={}, request_mode=MODE, elicitation_state=progress,
        )
        self.assertEqual(payload['elicitation_state'], progress)
        changed = deepcopy(payload)
        changed['elicitation_state']['answer_count'] = 1
        self.assertNotEqual(canonical_digest(payload), canonical_digest(changed))
        progress['completed_fields'].append('values')
        self.assertEqual(payload['elicitation_state']['completed_fields'], [])

    def test_terminal_is_bound_to_mode_field_and_count(self):
        terminal = completed_terminal(output=output())
        validate_terminal_payload(terminal, request_payload=browser())
        for request, result in (
            (browser(request_mode='chat'), output()),
            (browser(), output(field='past_experience')),
            (browser(), output(answer_count=2)),
            (browser(elicitation_state=state(event='start')), output()),
            (browser(), {**output(), 'action': {'changes': []}}),
        ):
            with self.subTest(request=request, result=result), self.assertRaises(LifecycleError):
                validate_terminal_payload(completed_terminal(output=result), request_payload=request)

    def test_clarification_skip_and_limit_have_distinct_counts(self):
        request = browser(elicitation_state=state(answer_count=2))
        for result in (
            output(answer_count=2),
            output(answer_count=2, status='completed', completion_reason='skipped', next_field='past_experience'),
            output(answer_count=3, status='completed', completion_reason='limit', next_field='past_experience'),
        ):
            validate_terminal_payload(completed_terminal(output=result), request_payload=request)
        with self.assertRaises(LifecycleError):
            validate_terminal_payload(completed_terminal(output=output(answer_count=3)), request_payload=request)

    def test_numeric_reference_summary_is_restored_as_text(self):
        vault = ReferenceTokenVault(user_id=3, session_id=12, request_id='ai_req_test')
        token = vault.tokenize({'total_debt': 73_400_000})['total_debt']
        terminal = completed_terminal(output=output(summary=token))
        restored = vault.restore_terminal(terminal, step_id=STEP)
        self.assertEqual(restored['output']['elicitation']['summary'], '73400000')
        validate_terminal_payload(restored, request_payload=browser())

    @patch('consulting.ai_lifecycle.create_assistant_message_from_terminal')
    def test_result_is_saved_in_history_without_form_action(self, create_message):
        message = SimpleNamespace(extracted_data={}, save=Mock())
        create_message.return_value = message
        result = create_v2_assistant_message(
            SimpleNamespace(id=12), completed_terminal(output=output()), STEP,
        )
        self.assertEqual(result.extracted_data, {'elicitation': output()['elicitation']})
        message.save.assert_called_once_with(update_fields=['extracted_data'])

    def test_start_and_answer_use_same_stream_but_only_start_is_system_event(self):
        for event in ('start', 'answer'):
            with self.subTest(event=event), ExitStack() as stack:
                patches = {}
                for name in (
                    'transaction.atomic', 'AIRequestExecution.objects',
                    'ConsultingSession.objects', 'get_owned_session',
                    'RedisLifecycleStore', 'Message.objects.create',
                    'proxy_v2_ai_stream', 'build_v2_ai_payload',
                ):
                    patches[name] = stack.enter_context(patch(f'consulting.v2_views.{name}'))
                session = SimpleNamespace(id=12, status='in_progress')
                patches['get_owned_session'].return_value = session
                patches['ConsultingSession.objects'].select_for_update.return_value.filter.return_value.first.return_value = session
                patches['AIRequestExecution.objects'].select_for_update.return_value.filter.return_value.first.return_value = None
                patches['RedisLifecycleStore'].return_value.get_idempotency.return_value = None
                patches['Message.objects.create'].return_value = SimpleNamespace(id=41)
                patches['build_v2_ai_payload'].side_effect = lambda **kwargs: {
                    'request_id': kwargs['request_id'], 'session_id': kwargs['session'].id,
                    'thread_id': 'user-3-session-12',
                }
                patches['proxy_v2_ai_stream'].return_value = iter(())
                body = browser(elicitation_state=state(event=event))
                if event == 'start':
                    body['content'] = START_CONTENT
                request = APIRequestFactory().post(
                    '/api/consulting/v2/consultations/12/messages/stream/', body,
                    format='json', HTTP_ACCEPT='text/event-stream',
                    HTTP_IDEMPOTENCY_KEY=f'mindset-{event}',
                )
                force_authenticate(request, user=SimpleNamespace(id=3, is_authenticated=True))
                response = V2MessageStreamView.as_view()(request, session_id=12)
                self.assertEqual(response.status_code, 200)
                response.close()
                self.assertEqual(
                    patches['build_v2_ai_payload'].call_args.kwargs['elicitation_state'],
                    body['elicitation_state'],
                )
                saved = patches['Message.objects.create'].call_args.kwargs
                self.assertEqual(saved['extracted_data'], (
                    {'system_event': 'mindset_elicitation_start', 'step_id': STEP}
                    if event == 'start' else None
                ))

    @patch('consulting.v2_sse.logger')
    @patch('consulting.v2_sse.finalize_terminal_result')
    @patch('consulting.v2_sse.stream_v2_ai_response')
    def test_sse_restores_summary_and_rejects_foreign_field_before_history(
        self, upstream, finalize, _logger,
    ):
        for field in ('values', 'past_experience'):
            with self.subTest(field=field):
                finalize.reset_mock()
                vault = ReferenceTokenVault(user_id=3, session_id=12, request_id='ai_req_test')
                token = vault.tokenize({'total_debt': 73_400_000})['total_debt']
                terminal = completed_terminal(output=output(field=field, summary=token))
                upstream.return_value = async_items([
                    'event: done', f'data: {json.dumps(terminal)}', '',
                ])
                active = {
                    **processing_state(), 'request_id': 'ai_req_test', 'step_id': STEP,
                }
                message = SimpleNamespace(
                    id=99, role='assistant', content=output()['response'],
                    extracted_data={'elicitation': output(summary='73400000')['elicitation']},
                )
                finalize.return_value = SimpleNamespace(
                    state={**active, 'status': 'completed' if field == 'values' else 'failed'},
                    message=message if field == 'values' else None, replay=False,
                )
                request_payload = {
                    **browser(), 'request_id': 'ai_req_test', 'session_id': 12,
                    'thread_id': active['thread_id'],
                }
                store = MagicMock()
                frames = [parse_frame(frame) for frame in asyncio.run(collect_async(
                    proxy_v2_ai_stream(
                        SimpleNamespace(id=12), request_payload, active, store,
                        token_vault=vault,
                    )
                ))]
                finalize.assert_called_once()
                saved_terminal = finalize.call_args.args[1]
                store.save_terminal.assert_called_once()
                if field == 'values':
                    self.assertEqual(saved_terminal['output']['elicitation']['summary'], '73400000')
                    self.assertEqual(frames[-1][0], 'done')
                    self.assertEqual(frames[-1][1]['message']['extracted_data'], message.extracted_data)
                else:
                    self.assertEqual(saved_terminal['status'], 'failed')
                    self.assertNotIn('output', saved_terminal)
                    self.assertEqual(frames[-1][0], 'error')
