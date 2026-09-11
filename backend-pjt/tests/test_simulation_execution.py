"""동시 실행 보호 (S15P21B109-139).

가짜 Redis 클라이언트로 뮤텍스를, 실제 세마포어로 워커 슬롯을 검증한다.
CI 에 Redis 가 없으므로 붙지 않는다.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django  # noqa: E402

django.setup()

from simulations import execution  # noqa: E402
from simulations.errors import SimulationError  # noqa: E402


class FakeRedis:
    """set(nx=True) 의 원자성만 흉내낸다."""

    def __init__(self, *, fail=False):
        self.store: dict[str, str] = {}
        self.fail = fail
        self.deleted: list[str] = []

    def set(self, key, value, nx=False, ex=None):
        if self.fail:
            raise ConnectionError('redis down')
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.deleted.append(key)
        self.store.pop(key, None)


class SessionLockTests(unittest.TestCase):
    def test_lock_is_released_after_a_successful_run(self) -> None:
        client = FakeRedis()
        with execution.session_lock(7, 42, client=client):
            self.assertEqual(len(client.store), 1)
        self.assertEqual(client.store, {})

    def test_lock_is_released_even_when_the_body_raises(self) -> None:
        client = FakeRedis()
        with self.assertRaises(ValueError):
            with execution.session_lock(7, 42, client=client):
                raise ValueError('boom')
        self.assertEqual(client.store, {})

    def test_second_run_on_the_same_session_is_409(self) -> None:
        client = FakeRedis()
        with execution.session_lock(7, 42, client=client):
            with self.assertRaises(SimulationError) as caught:
                with execution.session_lock(7, 42, client=client):
                    pass
        error = caught.exception
        self.assertEqual(error.status_code, 409)
        self.assertEqual(error.code, 'SIMULATION_IN_PROGRESS')
        self.assertTrue(error.retryable)

    def test_different_sessions_do_not_block_each_other(self) -> None:
        client = FakeRedis()
        with execution.session_lock(7, 42, client=client):
            with execution.session_lock(7, 43, client=client):
                pass

    def test_different_users_do_not_block_each_other(self) -> None:
        client = FakeRedis()
        with execution.session_lock(7, 42, client=client):
            with execution.session_lock(8, 42, client=client):
                pass

    def test_redis_failure_is_503_not_a_silent_bypass(self) -> None:
        """보호 장치가 죽었는데 통과시키면 보호가 없는 것과 같다."""
        with self.assertRaises(SimulationError) as caught:
            with execution.session_lock(7, 42, client=FakeRedis(fail=True)):
                pass
        error = caught.exception
        self.assertEqual(error.status_code, 503)
        self.assertEqual(error.code, 'EXECUTION_GUARD_UNAVAILABLE')
        self.assertTrue(error.retryable)

    def test_expired_lock_taken_by_another_request_is_not_deleted(self) -> None:
        """TTL 이 지나 다른 요청이 잡은 락을 지우면 안 된다."""
        client = FakeRedis()
        key = execution._lock_key(7, 42)
        with execution.session_lock(7, 42, client=client):
            client.store[key] = 'someone-elses-token'
        self.assertEqual(client.store[key], 'someone-elses-token')
        self.assertNotIn(key, client.deleted)


class WorkerSlotTests(unittest.TestCase):
    def setUp(self) -> None:
        # 다른 테스트가 남긴 상태와 섞이지 않게 새 세마포어로 갈아끼운다.
        self._original = execution._semaphore
        execution._semaphore = threading.BoundedSemaphore(
            execution.MAX_CONCURRENT_PER_WORKER
        )
        self.addCleanup(setattr, execution, '_semaphore', self._original)

    def test_slots_up_to_the_limit_are_granted(self) -> None:
        with execution.worker_slot(), execution.worker_slot():
            pass

    def test_exceeding_the_limit_returns_409_immediately(self) -> None:
        """대기시키지 않는다. 대기시키면 응답시간이 상한 없이 늘어난다."""
        with execution.worker_slot(), execution.worker_slot():
            with self.assertRaises(SimulationError) as caught:
                with execution.worker_slot():
                    pass
        error = caught.exception
        self.assertEqual(error.status_code, 409)
        self.assertEqual(error.code, 'SIMULATION_BUSY')
        self.assertTrue(error.retryable)

    def test_slot_is_returned_after_use(self) -> None:
        for _ in range(execution.MAX_CONCURRENT_PER_WORKER + 2):
            with execution.worker_slot():
                pass

    def test_slot_is_returned_even_when_the_body_raises(self) -> None:
        with self.assertRaises(ValueError):
            with execution.worker_slot():
                raise ValueError('boom')
        with execution.worker_slot():
            pass

    def test_limit_matches_the_deployed_worker_count(self) -> None:
        """uvicorn --workers 2 기준. 워커 수를 바꾸면 전역 상한도 바뀐다."""
        dockerfile = (BACKEND_ROOT / 'Dockerfile').read_text(encoding='utf-8')
        self.assertIn('"--workers", "2"', dockerfile)
        self.assertEqual(execution.MAX_CONCURRENT_PER_WORKER, 2)


if __name__ == '__main__':
    unittest.main()
