"""기존 news 테이블의 분석 컬럼을 Gemini 기반으로 재계산한다.

뉴스 재수집 없이 PostgreSQL news 테이블의 기존 title/text 를 읽어
category, tags, keywords, embedding 값을 업데이트한다.

실행:
    python -m news.processor.reenrich_news --dry-run
    python -m news.processor.reenrich_news --limit 3
    python -m news.processor.reenrich_news
"""

import argparse
import json
import re

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

from common.db import get_connection
from common.llm_client import (
    EMBEDDING_DIM,
    transform_classify,
    transform_extract_keywords,
    transform_to_embedding,
)
from common.logger import get_logger
from common.taxonomy import UNCLASSIFIED

log = get_logger(__name__)


def _clean_text(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


def _fetch_targets(limit: int | None = None) -> list[tuple[int, str, str]]:
    sql = """
        SELECT id, COALESCE(title, ''), text
        FROM news
        WHERE text IS NOT NULL
        ORDER BY id
    """
    params = ()
    if limit is not None:
        sql += " LIMIT %s"
        params = (limit,)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def reenrich(batch_size: int = 10, limit: int | None = None, dry_run: bool = False) -> dict:
    """기존 news row 의 분석 컬럼을 Gemini 기반 결과로 업데이트한다."""
    targets = _fetch_targets(limit=limit)
    if dry_run:
        log.info("재분석 대상: %d건 (dry-run)", len(targets))
        return {"target": len(targets), "updated": 0, "failed": 0, "dry_run": True}

    updated = 0
    failed = 0
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for news_id, title, text in targets:
                title = _clean_text(title)
                text = _clean_text(text)
                embedding_source = f"{title}\n{text}" if title else text
                try:
                    cls = transform_classify(text)
                    if cls["category"] == UNCLASSIFIED:
                        raise ValueError("classification failed")
                    keywords = transform_extract_keywords(text)
                    if not keywords:
                        raise ValueError("keyword extraction failed")
                    embedding = transform_to_embedding(embedding_source)
                    if len(embedding) != EMBEDDING_DIM:
                        raise ValueError(
                            f"embedding dim mismatch: {len(embedding)} != {EMBEDDING_DIM}"
                        )

                    cur.execute(
                        """
                        UPDATE news
                        SET category = %s,
                            tags = %s::jsonb,
                            keywords = %s::jsonb,
                            embedding = %s::vector,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (
                            cls["category"],
                            json.dumps(cls["tags"], ensure_ascii=False),
                            json.dumps(keywords, ensure_ascii=False),
                            _vector_literal(embedding),
                            news_id,
                        ),
                    )
                    updated += 1
                except Exception as e:
                    failed += 1
                    log.warning("재분석 실패 id=%s: %s", news_id, e)

                if updated and updated % batch_size == 0:
                    conn.commit()
                    log.info("재분석 진행: %d/%d건", updated, len(targets))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    result = {"target": len(targets), "updated": updated, "failed": failed, "dry_run": False}
    log.info("재분석 완료: %s", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="news 분석 컬럼을 Gemini 기반으로 재계산")
    parser.add_argument("--batch-size", type=int, default=10, help="commit 단위")
    parser.add_argument("--limit", type=int, default=None, help="테스트용 처리 건수 제한")
    parser.add_argument("--dry-run", action="store_true", help="대상 건수만 확인")
    args = parser.parse_args()

    result = reenrich(batch_size=args.batch_size, limit=args.limit, dry_run=args.dry_run)
    log.info("결과: %s", result)


if __name__ == "__main__":
    main()
