"""기준 실행 API 의 상태코드·응답 봉투 동결 (S15P21B109-137).

**DB 를 쓰지 않는다.** CI(test:backend)에는 Postgres 서비스가 없어서 DB 를 건드리는
순간 게이트에서 실패한다. reports/tests.py 의 SimpleNamespace/FakeManager 가짜 세션을
get_session 패치로 주입해 전 경로를 돈다.

이게 가능한 이유는 views 가 세션에서 status·id·is_ready_for_reports() 만 읽고 나머지
ORM 접근을 load_consistent_snapshot() 한 곳에 가뒀기 때문이다. 그 제약이 깨지면
이 파일이 먼저 깨진다.
"""

from __future__ import annotations

from contextlib import contextmanager
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
    with patch.object(SimulationRunView, 'get_session', return_value=session), patch(
        'simulations.views.load_consistent_snapshot', return_value=(snapshot, digest)
    ):
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


if __name__ == '__main__':
    unittest.main()
