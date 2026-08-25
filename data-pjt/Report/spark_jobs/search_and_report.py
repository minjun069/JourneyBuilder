"""
배치 잡: 활성 사용자별 하이브리드 검색 + 보고서 생성.

실행 예시:
  python search_and_report.py       # 최근 7일 뉴스 대상
  python search_and_report.py 30    # 최근 30일 뉴스 대상
"""
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import get_app_connection
from generator.report_generator import (
    _derive_search_params,
    _fetch_user_profile,
    generate_report,
    save_report,
)
from search.hybrid_search import hybrid_search

logger = logging.getLogger(__name__)


def _process_user(user_id: int, search_days: int) -> dict:
    """단일 사용자에 대해 검색 → 보고서 생성 → 저장을 수행한다."""
    try:
        profile = _fetch_user_profile(user_id)
        if not profile:
            return {"user_id": user_id, "status": "skipped", "reason": "no_profile"}

        query_text, keywords, categories = _derive_search_params(profile)

        news_items = hybrid_search(
            query_text=query_text,
            keywords=keywords,
            categories=categories,
            top_k=10,
            days=search_days,
        )

        if not news_items:
            return {"user_id": user_id, "status": "skipped", "reason": "no_news"}

        content = generate_report(user_id, news_items)
        news_ids = [item["id"] for item in news_items if item.get("id")]
        report_id = save_report(user_id, news_ids, content)

        logger.info("user:%d 보고서 생성 완료 (report_id=%d)", user_id, report_id)
        return {"user_id": user_id, "status": "success", "report_id": report_id}

    except Exception as e:
        logger.error("user:%d 오류: %s", user_id, e, exc_info=True)
        return {"user_id": user_id, "status": "error", "reason": str(e)}


def main():
    search_days = int(sys.argv[1]) if len(sys.argv) > 1 else 7

    conn = get_app_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM accounts_users WHERE is_active = TRUE")
            user_ids = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()

    logger.info("처리 대상 사용자: %d명 / 검색 범위: 최근 %d일", len(user_ids), search_days)

    results = [_process_user(uid, search_days) for uid in user_ids]

    success = sum(1 for r in results if r["status"] == "success")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    failed  = sum(1 for r in results if r["status"] == "error")

    logger.info("완료 — 성공:%d / 건너뜀:%d / 실패:%d", success, skipped, failed)


if __name__ == "__main__":
    main()
