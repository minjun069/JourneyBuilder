"""동시 실행 보호 (S15P21B109-139).

## 실측이 설계를 정했다 (2026-09-11, 컨테이너 1 CPU)

| 조건 | 소요 |
|---|---|
| 1,000경로 31년 비교안 1개 (MVP 실제) | 433ms |
| 1,000경로 81년 (모듈 최대 기간) | 1,048ms |
| 동시 1 / 2 / 4 / 8건 | 424 / 818 / 1,446 / 2,802ms |
| 계산 중 SSE 이벤트 루프 지연 | 중앙 4~16ms, 최악 88ms |

**게이트웨이 타임아웃 60초 대비 최악도 5% 수준이라 동기 요청-응답을 유지한다.**
비동기 잡 큐(202+폴링)를 만들 근거가 없다 — 기존 AI 상담이 그 구조인 이유는 LLM 이
수십 초 걸리기 때문이고, 계산은 1초 미만이다.

그래서 nginx proxy_read_timeout 조정과 계획 기간 상한은 **철회했다.**

## 그럼에도 두 가지가 필요한 이유

CPU 를 2배로 줘도 동시 실행이 빨라지지 않았다 — 순수 Python 이라 프로세스 안에서
GIL 이 완전히 직렬화한다. DRF 동기 뷰는 ASGI 스레드풀에서 도는데, 스레드가 쌓이면
**상한 없이 선형으로 늘고 같은 워커의 다른 API 요청(보고서 조회·상담 저장)이 전부
뒤에 줄을 선다.**

- **세션 뮤텍스(Redis)** — 같은 사용자의 중복 클릭을 막는다. 워커가 달라도 걸린다
- **프로세스 세마포어** — 워커 안에서 몇 건까지 동시에 돌릴지 제한한다.
  Redis 가 아니라 로컬인 이유는 5줄이고 Redis 장애와 운명을 같이하지 않기 때문이다

SSE 채팅은 영향받지 않는 것을 확인했다(최악 88ms). 그건 막지 않는다.
"""

from __future__ import annotations

from contextlib import contextmanager
import secrets
import threading

from django.conf import settings

from .errors import SimulationError

# 워커당 동시 실행 수. uvicorn --workers 2 이므로 전역 최대 4건이고,
# 그 경우 최악 응답시간이 약 0.9초로 고정된다.
MAX_CONCURRENT_PER_WORKER = 2
# 뮤텍스 TTL. 최악 실행(1.3초)보다 충분히 크되, 프로세스가 죽어도 오래 잠기지 않게.
LOCK_TTL_SECONDS = 60

_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_PER_WORKER)


def _client():
    import redis  # noqa: PLC0415

    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def _lock_key(user_id: int, session_id: int) -> str:
    return f'{settings.AI_REDIS_NAMESPACE}sim:run:{user_id}:{session_id}'


@contextmanager
def session_lock(user_id: int, session_id: int, client=None):
    """같은 상담에 대한 동시 실행을 하나로 제한한다.

    토큰을 확인하고 지운다 — TTL 이 지나 다른 요청이 잡은 락을 지우면 안 된다.
    """
    try:
        client = _client() if client is None else client
        token = secrets.token_hex(16)
        acquired = client.set(
            _lock_key(user_id, session_id), token, nx=True, ex=LOCK_TTL_SECONDS
        )
    except SimulationError:
        raise
    except Exception as error:  # redis 연결·응답 실패
        # 보호 장치가 죽었는데 그냥 통과시키면 보호가 없는 것과 같다.
        raise SimulationError(
            503,
            'EXECUTION_GUARD_UNAVAILABLE',
            '지금은 시뮬레이션을 실행할 수 없습니다. 잠시 후 다시 시도해 주세요.',
            retryable=True,
        ) from error

    if not acquired:
        raise SimulationError(
            409,
            'SIMULATION_IN_PROGRESS',
            '이 상담의 시뮬레이션이 이미 실행 중입니다. 잠시 후 다시 시도해 주세요.',
            retryable=True,
        )
    try:
        yield
    finally:
        try:
            key = _lock_key(user_id, session_id)
            if client.get(key) == token:
                client.delete(key)
        except Exception:  # 해제 실패는 TTL 이 정리한다. 요청을 실패시키지 않는다.
            pass


@contextmanager
def worker_slot():
    """워커 안에서 동시에 도는 계산 수를 제한한다.

    기다리지 않고 즉시 409 를 낸다. 대기시키면 응답시간이 상한 없이 늘어나
    사용자는 "느리다" 만 겪는다. 재시도 안내가 낫다.
    """
    if not _semaphore.acquire(blocking=False):
        raise SimulationError(
            409,
            'SIMULATION_BUSY',
            '동시 실행이 많아 잠시 뒤에 처리할 수 있습니다. 잠시 후 다시 시도해 주세요.',
            retryable=True,
        )
    try:
        yield
    finally:
        _semaphore.release()
