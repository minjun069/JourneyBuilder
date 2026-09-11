"""기준 실행 API 의 상태코드·응답 봉투 동결 (S15P21B109-137).

**DB 를 쓰지 않는다.** CI(test:backend)에는 Postgres 서비스가 없어서 DB 를 건드리는
순간 게이트에서 실패한다. reports/tests.py 의 SimpleNamespace/FakeManager 가짜 세션을
get_session 패치로 주입해 전 경로를 돈다.

이게 가능한 이유는 views 가 세션에서 status·id·is_ready_for_reports() 만 읽고 나머지
ORM 접근을 load_consistent_snapshot() 한 곳에 가뒀기 때문이다. 그 제약이 깨지면
이 파일이 먼저 깨진다.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django  # noqa: E402

django.setup()

from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from consulting.models import ConsultingSession  # noqa: E402
from simulations.errors import SimulationError  # noqa: E402
from simulations.views import SimulationRunView  # noqa: E402

sys.path.insert(0, str(BACKEND_ROOT / 'tests'))
from test_simulation_adapter import snapshot as valid_snapshot  # noqa: E402

SESSION_ID = 42
USER_ID = 7


def fake_session(status=ConsultingSession.Status.COMPLETED, ready=True):
    return SimpleNamespace(
        id=SESSION_ID,
        status=status,
        is_ready_for_reports=lambda: ready,
    )


_DEFAULT = object()  # session=None 은 "조회 실패" 라는 뜻이라 기본값과 구분해야 한다


@contextmanager
def wired(session=_DEFAULT, snapshot=None, digest='a' * 64):
    """세션 조회와 스냅샷 조회만 가짜로 바꾼다. 정책·어댑터·모듈은 진짜를 쓴다."""
    session = fake_session() if session is _DEFAULT else session
    snapshot = valid_snapshot() if snapshot is None else snapshot
    # 세션 뮤텍스만 무력화한다(CI 에 Redis 가 없다). worker_slot 은 프로세스 로컬이라
    # 진짜를 그대로 쓴다 — 실제 세마포어가 요청마다 반납되는지도 함께 검증된다.
    with patch.object(SimulationRunView, 'get_session', return_value=session), patch(
        'simulations.views.load_consistent_snapshot', return_value=(snapshot, digest)
    ), patch('simulations.views.session_lock', lambda *_a, **_k: nullcontext()):
        yield


def call(body=None, *, user_id=USER_ID):
    factory = APIRequestFactory()
    request = factory.post(
        f'/api/v1/consultations/{SESSION_ID}/simulations/',
        {'mode': 'baseline'} if body is None else body,
        format='json',
    )
    force_authenticate(request, user=SimpleNamespace(id=user_id, is_authenticated=True))
    return SimulationRunView.as_view()(request, session_id=SESSION_ID)


class SuccessContractTests(unittest.TestCase):
    def test_baseline_run_returns_the_agreed_envelope(self) -> None:
        with wired():
            response = call()
        self.assertEqual(response.status_code, 200)
        body = response.data
        self.assertTrue(body['success'])
        self.assertEqual(set(body), {'success', 'data', 'meta'})
        self.assertEqual(set(body['data']), {'baseline_key', 'analysis'})
        self.assertEqual(
            set(body['meta']),
            {'simulation_policy_version', 'package_version', 'executed_at', 'elapsed_ms'},
        )

    def test_module_output_is_passed_through_unmodified(self) -> None:
        """notices·units·contract_version 이 보존돼야 한다. 가공하면 안내가 사라진다."""
        with wired():
            analysis = call().data['data']['analysis']
        self.assertEqual(analysis['contract_version'], 'retirement-analysis-v1')
        self.assertEqual(analysis['mode'], 'baseline')
        self.assertIn('units', analysis)
        self.assertIn('notices', analysis)
        self.assertTrue(analysis['notices'])
        self.assertEqual(analysis['scenarios'], [])

    def test_response_is_strict_json_encodable(self) -> None:
        """BE 는 allow_nan=False 로 인코딩해 응답한다. NaN/Infinity 가 새면 안 된다."""
        with wired():
            body = call().data
        json.dumps(body, allow_nan=False, ensure_ascii=False)

    def test_snapshot_digest_is_not_exposed(self) -> None:
        digest = 'd' * 64
        with wired(digest=digest):
            body = call().data
        self.assertNotIn(digest, json.dumps(body, ensure_ascii=False))

    def test_baseline_key_is_stable_for_the_same_inputs(self) -> None:
        with wired():
            first = call().data['data']['baseline_key']
        with wired():
            second = call().data['data']['baseline_key']
        self.assertEqual(first, second)

    def test_baseline_key_changes_when_the_snapshot_changes(self) -> None:
        with wired(digest='a' * 64):
            first = call().data['data']['baseline_key']
        with wired(digest='b' * 64):
            second = call().data['data']['baseline_key']
        self.assertNotEqual(first, second)


class RequestValidationTests(unittest.TestCase):
    def _assert_rejected(self, body, code='REQUEST_INVALID', status=400):
        with wired():
            response = call(body)
        self.assertEqual(response.status_code, status)
        self.assertFalse(response.data['success'])
        self.assertEqual(response.data['error']['code'], code)

    def test_missing_mode_is_rejected(self) -> None:
        self._assert_rejected({})

    def test_unsupported_mode_is_rejected(self) -> None:
        # what_if 는 다음 단계다. 조용히 기준으로 처리하면 비교가 된 줄 안다.
        self._assert_rejected({'mode': 'what_if'})

    def test_unknown_fields_are_rejected(self) -> None:
        self._assert_rejected({'mode': 'baseline', 'num_paths': 5000})

    def test_assumptions_cannot_be_supplied_by_the_browser(self) -> None:
        self._assert_rejected({'mode': 'baseline', 'seed': 1})

    def test_error_envelope_shape(self) -> None:
        with wired():
            response = call({})
        self.assertEqual(set(response.data), {'success', 'error'})
        self.assertEqual(
            set(response.data['error']) - {'details'},
            {'code', 'message', 'retryable'},
        )


class SessionGateTests(unittest.TestCase):
    def _assert_error(self, session, code, status):
        with wired(session=session):
            response = call()
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.data['error']['code'], code)

    def test_missing_or_foreign_session_is_404(self) -> None:
        # 타인 세션도 404 다. 존재 여부가 새지 않는다.
        self._assert_error(None, 'SESSION_NOT_FOUND', 404)

    def test_abandoned_session_is_409(self) -> None:
        self._assert_error(
            fake_session(status=ConsultingSession.Status.ABANDONED),
            'SESSION_ABANDONED',
            409,
        )

    def test_incomplete_session_is_409(self) -> None:
        self._assert_error(fake_session(ready=False), 'SESSION_NOT_READY', 409)


class InputAndPolicyFailureTests(unittest.TestCase):
    def test_bad_input_returns_422_with_collected_issues(self) -> None:
        broken = valid_snapshot(
            financial_assets=[
                {'asset_type': '전세보증금', 'current_value': None, 'monthly_savings': 1}
            ]
        )
        with wired(snapshot=broken):
            response = call()
        self.assertEqual(response.status_code, 422)
        error = response.data['error']
        self.assertEqual(error['code'], 'INPUT_NOT_READY')
        issues = error['details']['issues']
        # 첫 오류에서 멈추지 않는다 — 이 어댑터의 존재 이유다.
        self.assertGreater(len(issues), 1)
        for issue in issues:
            self.assertEqual(set(issue) - {'hint'}, {'code', 'field', 'message'})

    def test_policy_failure_is_503_and_never_falls_back(self) -> None:
        from simulations.policy import SimulationPolicyError  # noqa: PLC0415

        with wired(), patch(
            'simulations.services.simulation_assumption_values',
            side_effect=SimulationPolicyError('boom'),
        ):
            response = call()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['error']['code'], 'POLICY_UNAVAILABLE')
        self.assertTrue(response.data['error']['retryable'])

    def test_module_rejection_is_reported_as_a_safety_net(self) -> None:
        """어댑터가 통과시켰는데 모듈이 거절하면 ANALYSIS_REJECTED 다.

        정상 흐름에서는 나오지 않아야 한다. 나오면 어댑터 사전 점검에 구멍이 있다.
        """
        from retirement_simulation import RetirementAnalysisError  # noqa: PLC0415

        with wired(), patch(
            'simulations.services.prepare_input'
        ) as prepared, patch(
            'retirement_simulation.analyze_retirement',
            # input_mapping_failed 는 reason_code 가 필수다 (analysis_errors._ERROR_SPECS).
            side_effect=RetirementAnalysisError(
                'input_mapping_failed', 'basic_info', reason_code='missing_field'
            ),
        ):
            prepared.return_value = SimpleNamespace(
                saved_data={}, confirmed_empty_sections=()
            )
            response = call()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data['error']['code'], 'ANALYSIS_REJECTED')
        self.assertIn('analysis_error', response.data['error']['details'])


class RoutingContractTests(unittest.TestCase):
    def test_endpoint_is_registered_under_api_v1(self) -> None:
        from django.urls import resolve, reverse  # noqa: PLC0415

        url = reverse('simulation-run', kwargs={'session_id': SESSION_ID})
        self.assertEqual(url, f'/api/v1/consultations/{SESSION_ID}/simulations/')
        self.assertIs(resolve(url).func.view_class, SimulationRunView)

    def test_reports_catch_all_does_not_swallow_the_route(self) -> None:
        """reports/urls.py 의 <slug:report_type> 이 먼저 잡으면 404 가 난다."""
        from django.urls import resolve  # noqa: PLC0415

        match = resolve(f'/api/v1/consultations/{SESSION_ID}/simulations/')
        self.assertEqual(match.url_name, 'simulation-run')


def baseline_key_for(digest='a' * 64):
    with wired(digest=digest):
        return call().data['data']['baseline_key']


def what_if(changes, key=None, digest='a' * 64, **kwargs):
    body = {
        'mode': 'what_if',
        'baseline_key': baseline_key_for(digest) if key is None else key,
        'changes': changes,
    }
    with wired(digest=digest, **kwargs):
        return call(body)


class WhatIfContractTests(unittest.TestCase):
    """S15P21B109-138. 기준안과 변경안 1개를 같은 시장 경로로 비교한다."""

    def test_comparison_returns_baseline_and_one_scenario(self) -> None:
        response = what_if({'monthly_savings': 1_500_000})
        self.assertEqual(response.status_code, 200)
        analysis = response.data['data']['analysis']
        self.assertEqual(analysis['mode'], 'what_if')
        self.assertEqual(len(analysis['scenarios']), 1)
        scenario = analysis['scenarios'][0]
        self.assertEqual(scenario['scenario_id'], 'comparison')
        self.assertIn('success_probability_delta_pp', scenario['delta'])
        # 기준 결과가 같은 응답에 함께 온다 — 같은 수익률 경로로 계산됐다는 뜻이다.
        self.assertIn('baseline', analysis)

    def test_saving_more_does_not_lower_success_probability(self) -> None:
        """같은 시장 경로를 재사용하므로 차이는 조건 변경 때문이어야 한다."""
        analysis = what_if({'monthly_savings': 5_000_000}).data['data']['analysis']
        self.assertGreaterEqual(
            analysis['scenarios'][0]['result']['success_probability'],
            analysis['baseline']['success_probability'],
        )

    def test_zero_is_a_real_change_not_an_omission(self) -> None:
        response = what_if({'monthly_savings': 0})
        self.assertEqual(response.status_code, 200)
        changes = response.data['data']['analysis']['scenarios'][0]['changes']
        self.assertEqual(changes['monthly_savings'], 0)

    def test_baseline_key_is_unchanged_when_the_baseline_is_unchanged(self) -> None:
        key = baseline_key_for()
        self.assertEqual(
            what_if({'monthly_savings': 1}, key=key).data['data']['baseline_key'], key
        )

    def test_stale_baseline_key_is_409_without_a_replacement(self) -> None:
        stale = baseline_key_for(digest='a' * 64)
        response = what_if({'monthly_savings': 1}, key=stale, digest='b' * 64)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['error']['code'], 'BASELINE_STALE')
        # 새 키를 함께 주면 화면이 조용히 다른 기준으로 비교할 수 있다.
        self.assertNotIn('baseline_key', json.dumps(response.data))

    def test_malformed_baseline_key_is_400(self) -> None:
        response = what_if({'monthly_savings': 1}, key='not-a-key')
        self.assertEqual(response.status_code, 400)


class WhatIfRequestValidationTests(unittest.TestCase):
    def test_missing_changes_or_key_is_400(self) -> None:
        with wired():
            self.assertEqual(call({'mode': 'what_if'}).status_code, 400)
        with wired():
            body = {'mode': 'what_if', 'changes': {'monthly_savings': 1}}
            self.assertEqual(call(body).status_code, 400)

    def test_baseline_mode_rejects_change_fields(self) -> None:
        """조용히 무시하면 사용자는 비교가 된 줄 안다."""
        with wired():
            response = call({'mode': 'baseline', 'changes': {'monthly_savings': 1}})
        self.assertEqual(response.status_code, 400)

    def test_unsupported_change_field_is_400(self) -> None:
        response = what_if({'annual_return_mean': 0.09})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['error']['details']['unsupported_fields'], ['annual_return_mean']
        )

    def test_empty_changes_is_400(self) -> None:
        self.assertEqual(what_if({}).status_code, 400)

    def test_empty_expense_list_is_rejected(self) -> None:
        # 빈 목록은 "생활비 없음" 이 아니다. 유지하려면 생략해야 한다.
        self.assertEqual(what_if({'retirement_expenses': []}).status_code, 400)

    def test_expense_row_field_and_range_validation(self) -> None:
        bad_rows = (
            [{'from_age': 60, 'to_age': 85}],
            [{'from_age': 60, 'to_age': 85, 'monthly_amount': 1, 'note': 'x'}],
            [{'from_age': 85, 'to_age': 60, 'monthly_amount': 1}],
            [{'from_age': 0, 'to_age': 85, 'monthly_amount': 1}],
            [{'from_age': 60, 'to_age': 85, 'monthly_amount': -1}],
            [{'from_age': 60, 'to_age': 85, 'monthly_amount': True}],
        )
        for rows in bad_rows:
            with self.subTest(rows=rows):
                self.assertEqual(what_if({'retirement_expenses': rows}).status_code, 400)

    def test_retirement_age_range_is_enforced(self) -> None:
        self.assertEqual(what_if({'retirement_age': 17}).status_code, 400)
        self.assertEqual(what_if({'retirement_age': 101}).status_code, 400)


class WhatIfApplicabilityTests(unittest.TestCase):
    """변경을 적용했을 때 생기는 문제는 422 로, 어느 구간인지까지 알려준다."""

    def test_earlier_retirement_without_new_expense_rows_is_422(self) -> None:
        # 기준 생활비는 60~85세다. 은퇴를 55세로 당기면 55~59세가 빈다.
        response = what_if({'retirement_age': 55})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data['error']['code'], 'CHANGES_NOT_APPLICABLE')
        issue = response.data['error']['details']['issues'][0]
        self.assertEqual(issue['code'], 'expense_periods_gap')
        self.assertEqual(issue['hint'], {'missing': [{'from_age': 55, 'to_age': 59}]})

    def test_earlier_retirement_with_matching_expenses_succeeds(self) -> None:
        response = what_if({
            'retirement_age': 55,
            'retirement_expenses': [
                {'from_age': 55, 'to_age': 85, 'monthly_amount': 1_800_000}
            ],
        })
        self.assertEqual(response.status_code, 200)

    def test_replacement_schedule_with_a_gap_is_422(self) -> None:
        response = what_if({
            'retirement_expenses': [
                {'from_age': 60, 'to_age': 74, 'monthly_amount': 2_000_000},
                {'from_age': 80, 'to_age': 85, 'monthly_amount': 1_500_000},
            ]
        })
        self.assertEqual(response.status_code, 422)
        issue = response.data['error']['details']['issues'][0]
        self.assertEqual(issue['hint'], {'missing': [{'from_age': 75, 'to_age': 79}]})

    def test_overlapping_replacement_schedule_is_422(self) -> None:
        response = what_if({
            'retirement_expenses': [
                {'from_age': 60, 'to_age': 75, 'monthly_amount': 2_000_000},
                {'from_age': 75, 'to_age': 85, 'monthly_amount': 1_500_000},
            ]
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.data['error']['details']['issues'][0]['code'], 'expense_periods_overlap'
        )

    def test_retirement_age_before_current_age_is_422(self) -> None:
        response = what_if({
            'retirement_age': 30,
            'retirement_expenses': [
                {'from_age': 40, 'to_age': 85, 'monthly_amount': 1_000_000}
            ],
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.data['error']['details']['issues'][0]['code'],
            'retirement_age_before_current_age',
        )

    def test_applicability_check_is_not_wider_than_the_module(self) -> None:
        """BE 가 통과시킨 변경은 모듈도 받아야 한다. 넓으면 정상 사용자가 막힌다."""
        accepted = (
            {'monthly_savings': 0},
            {'monthly_savings': 9_000_000},
            {'retirement_age': 65},
            {'retirement_age': 85},
            {'retirement_expenses': [{'from_age': 60, 'to_age': 85, 'monthly_amount': 0}]},
        )
        for changes in accepted:
            with self.subTest(changes=changes):
                self.assertEqual(what_if(changes).status_code, 200)


class ModuleContractVersionTests(unittest.TestCase):
    def test_view_constants_match_the_module_output(self) -> None:
        """상수가 모듈과 어긋나면 baseline_key 발급·검증이 갈려 항상 409 가 된다."""
        from simulations.views import CONTRACT_VERSION, MAPPING_VERSION  # noqa: PLC0415

        with wired():
            analysis = call().data['data']['analysis']
        self.assertEqual(analysis['contract_version'], CONTRACT_VERSION)
        self.assertEqual(analysis['mapping_version'], MAPPING_VERSION)


if __name__ == '__main__':
    unittest.main()
