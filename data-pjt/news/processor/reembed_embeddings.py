"""기존 news 테이블의 embedding 컬럼만 Gemini 임베딩으로 재계산한다.

뉴스 재수집 없이 PostgreSQL news 테이블의 기존 text 를 읽어
embedding VECTOR(1536) 값만 업데이트한다.

실행:
    python -m news.processor.reembed_embeddings --dry-run
    python -m news.processor.reembed_embeddings
"""

import argparse
import re
import time

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

from common.db import get_connection
from common.llm_client import EMBEDDING_DIM, transform_to_embedding
from common.logger import get_logger

log = get_logger(__name__)


def _clean_text(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


def _fetch_targets(
    limit: int | None = None,
    min_id: int | None = None,
    max_id: int | None = None,
) -> list[tuple[int, str, str]]:
    sql = """
        SELECT id, COALESCE(title, ''), text
        FROM news
        WHERE text IS NOT NULL
    """
    params = []
    if min_id is not None:
        sql += " AND id >= %s"
        params.append(min_id)
    if max_id is not None:
        sql += " AND id <= %s"
        params.append(max_id)

    sql += " ORDER BY id"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return cur.fetchall()
    finally:
        conn.close()


def _is_quota_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "429" in message or "quota" in message or "rate limit" in message


def reembed(
    batch_size: int = 20,
    limit: int | None = None,
    min_id: int | None = None,
    max_id: int | None = None,
    sleep_seconds: float = 1.0,
    stop_on_quota: bool = True,
    dry_run: bool = False,
) -> dict:
    """기존 news row 의 embedding 만 재계산해 업데이트한다."""
    targets = _fetch_targets(limit=limit, min_id=min_id, max_id=max_id)
    if dry_run:
        log.info("재임베딩 대상: %d건 (dry-run)", len(targets))
        return {
            "target": len(targets),
            "updated": 0,
            "failed": 0,
            "stopped_on_quota": False,
            "dry_run": True,
        }

    updated = 0
    failed = 0
    stopped_on_quota = False
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for idx, (news_id, title, text) in enumerate(targets, start=1):
                content = _clean_text(f"{title}\n{text}")
                try:
                    embedding = transform_to_embedding(content)
                    if len(embedding) != EMBEDDING_DIM:
                        raise ValueError(
                            f"embedding dim mismatch: {len(embedding)} != {EMBEDDING_DIM}"
                        )
                    cur.execute(
                        """
                        UPDATE news
                        SET embedding = %s::vector,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (_vector_literal(embedding), news_id),
                    )
                    updated += 1
                except Exception as e:
                    failed += 1
                    log.warning("재임베딩 실패 id=%s: %s", news_id, e)
                    if stop_on_quota and _is_quota_error(e):
                        stopped_on_quota = True
                        conn.commit()
                        log.warning(
                            "Gemini quota/rate limit 감지 -> 중단. 나중에 --min-id %s 로 재개하세요.",
                            news_id,
                        )
                        break

                if updated and updated % batch_size == 0:
                    conn.commit()
                    log.info("재임베딩 진행: %d/%d건", updated, len(targets))
                if sleep_seconds > 0 and idx < len(targets):
                    time.sleep(sleep_seconds)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    result = {
        "target": len(targets),
        "updated": updated,
        "failed": failed,
        "stopped_on_quota": stopped_on_quota,
        "dry_run": False,
    }
    log.info("재임베딩 완료: %s", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="news.embedding 컬럼만 Gemini로 재임베딩")
    parser.add_argument("--batch-size", type=int, default=20, help="commit 단위")
    parser.add_argument("--limit", type=int, default=None, help="테스트용 처리 건수 제한")
    parser.add_argument("--min-id", type=int, default=None, help="이 id 이상만 처리")
    parser.add_argument("--max-id", type=int, default=None, help="이 id 이하만 처리")
    parser.add_argument("--sleep", type=float, default=1.0, help="요청 사이 대기 초")
    parser.add_argument(
        "--no-stop-on-quota",
        action="store_true",
        help="429/quota 에러가 나도 끝까지 계속 시도",
    )
    parser.add_argument("--dry-run", action="store_true", help="대상 건수만 확인")
    args = parser.parse_args()

    result = reembed(
        batch_size=args.batch_size,
        limit=args.limit,
        min_id=args.min_id,
        max_id=args.max_id,
        sleep_seconds=args.sleep,
        stop_on_quota=not args.no_stop_on_quota,
        dry_run=args.dry_run,
    )
    log.info("결과: %s", result)


if __name__ == "__main__":
    main()
