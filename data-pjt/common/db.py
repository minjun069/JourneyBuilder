"""PostgreSQL(news DB) 연결 헬퍼.

배치 파이프라인은 기존 streaming_data(실시간)와 분리된 news DB 를 사용한다.
.env 의 DB_NAME 은 streaming_data 라서 여기서 직접 참조하지 않고,
dbname 기본값을 'news' 로 두고 host/port/user/password 만 env 에서 읽는다.

환경변수:
  DB_HOST     : 호스트 (기본 localhost / 컨테이너 내부에서는 postgres 로 override)
  DB_PORT     : 포트 (기본 5432)
  DB_USER     : 사용자 (기본 ssafy)
  DB_PASSWORD : 비밀번호 (기본 ssafy)
  NEWS_DB_NAME: news DB 이름 override (기본 news)

사용:
    from common.db import get_connection
    with get_connection() as conn:          # 블록 종료 시 commit, 예외 시 rollback
        with conn.cursor() as cur:
            cur.execute(...)
"""

import os
from contextlib import contextmanager

import psycopg2

from common.logger import get_logger

log = get_logger(__name__)


def get_connection(dbname: str | None = None, autocommit: bool = False):
    """psycopg2 연결을 생성해 반환한다. 호출자가 close 를 책임진다.

    contextmanager 가 필요하면 connection() 을 사용할 것.
    """
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=dbname or os.getenv("NEWS_DB_NAME", "news"),
        user=os.getenv("DB_USER", "ssafy"),
        password=os.getenv("DB_PASSWORD", "ssafy"),
    )
    conn.autocommit = autocommit
    return conn


@contextmanager
def connection(dbname: str | None = None):
    """with 블록용 연결 컨텍스트.

    정상 종료 시 commit, 예외 발생 시 rollback, 항상 close.
    """
    conn = get_connection(dbname=dbname)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ping(dbname: str | None = None) -> bool:
    """연결 가능 여부를 확인한다 (헬스체크용)."""
    try:
        conn = get_connection(dbname=dbname)
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            cur.fetchone()
        conn.close()
        return True
    except Exception as e:
        log.error("DB ping 실패: %s", e)
        return False
