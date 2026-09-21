"""What-if 비교의 기준을 식별하는 불투명 키 (S15P21B109-137).

## 왜 해시가 아니라 HMAC 인가

모듈 인수인계 문서가 "재무정보 원문이나 **단순 재무값 해시를 공개 식별자로 사용
금지**" 를 명시한다. `calculation_input_digest` 는 저장 스냅샷의 sha256 이라 그대로
내보내면 값을 역산·열거할 여지가 생긴다. SECRET_KEY 를 키로 쓰는 HMAC 으로 한 번 더
덮고, digest 자체는 재료로만 쓰고 응답에 넣지 않는다.

## 왜 저장소가 필요 없나

키가 재료 전체의 결정론적 함수다. 상담 데이터·정책 버전·모듈 버전 중 무엇이 바뀌어도
다시 만든 키가 달라지므로 불일치가 자동으로 드러난다. Redis TTL 도 DB 테이블도 없다.
SECRET_KEY 로테이션은 전체 무효화가 되는데, 이는 "재배포 시 기준 무효화" 요구와 같다.

## 만료를 넣지 않은 이유

기준이 실제로 바뀌지 않았는데 시간이 지났다는 이유로 1,000경로 재계산을 강제할 근거가
없다. 바뀌면 어차피 키가 달라진다.

키는 소유권을 대신하지 않는다. 매 요청에서 세션 소유권을 별도로 확인한다.
"""

from __future__ import annotations

import base64
import re

from django.utils.crypto import salted_hmac

BASELINE_KEY_VERSION = 'bk1'
BASELINE_KEY_PATTERN = re.compile(r'bk1\.[A-Za-z0-9_-]{43}')
_SALT = 'simulations.baseline_key'


def _material(
    *,
    user_id: int,
    session_id: int,
    snapshot_digest: str,
    policy_version: str,
    contract_version: str,
    mapping_version: str,
    package_version: str,
) -> str:
    parts = (
        BASELINE_KEY_VERSION,
        str(user_id),
        str(session_id),
        snapshot_digest,
        policy_version,
        contract_version,
        mapping_version,
        package_version,
    )
    # 구분자가 값 안에 나타나면 서로 다른 재료가 같은 문자열이 될 수 있다.
    # 정책/계약 버전에 '|' 가 들어갈 일은 없지만 값에 의존하지 않도록 이스케이프한다.
    return '|'.join(part.replace('\\', r'\\').replace('|', r'\|') for part in parts)


def issue_baseline_key(**material) -> str:
    # salted_hmac 의 기본 algorithm 은 sha1 이다. 명시하지 않으면 20바이트가 나온다.
    digest = salted_hmac(_SALT, _material(**material), algorithm='sha256').digest()
    body = base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')
    return f'{BASELINE_KEY_VERSION}.{body}'


def has_baseline_key_shape(key: object) -> bool:
    """형식만 본다. 형식이 틀리면 400, 형식은 맞고 값이 다르면 409 로 갈린다."""
    return isinstance(key, str) and BASELINE_KEY_PATTERN.fullmatch(key) is not None
