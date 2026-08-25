"""뉴스 처리(enrich) 단계.

HDFS 원본(raw)을 읽어 정제/중복제거 후, 각 기사에 대해
카테고리·태그·키워드·임베딩을 생성하고 결과(enriched)를 HDFS 에 저장한다.

흐름:
  HDFS raw 읽기 -> link 중복제거 -> DB 기존 link 스킵(LLM 비용 절감)
    -> ThreadPool 로 LLM 변환(분류+태그 / 키워드 / 임베딩) -> HDFS enriched 저장
      /news/enriched/YYYY/MM/DD/news.jsonl

LLM 호출은 네트워크 I/O 바운드라 ThreadPoolExecutor 로 병렬화한다.

실행:
    python -m news.processor.process
"""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

from common.db import connection
from common.hdfs_client import enriched_path, raw_path, read_jsonl, write_jsonl
from common.llm_client import (
    transform_classify,
    transform_extract_keywords,
    transform_to_embedding,
)
from common.logger import get_logger

log = get_logger(__name__)

# 동시 LLM 호출 스레드 수 (I/O 바운드라 코어 수보다 크게 잡아도 됨)
DEFAULT_MAX_WORKERS = 8


def clean_text(s: str | None) -> str:
    """연속 공백/개행을 단일 공백으로 정리한다 (정제)."""
    return re.sub(r"\s+", " ", s or "").strip()


def existing_links(links: list[str]) -> set[str]:
    """주어진 link 중 news 테이블에 이미 존재하는 것들을 반환 (재처리 스킵용)."""
    if not links:
        return set()
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT link FROM news WHERE link = ANY(%s);", (links,))
            return {row[0] for row in cur.fetchall()}


def enrich_one(article: dict) -> dict | None:
    """기사 1건에 카테고리/태그/키워드/임베딩을 추가한다. 본문 없으면/실패 시 None."""
    text = clean_text(article.get("text"))
    if not text:
        return None

    try:
        title = clean_text(article.get("title"))
        cls = transform_classify(text)  # {"category", "tags"}
        keywords = transform_extract_keywords(text)
        embedding_source = f"{title}\n{text}" if title else text
        embedding = transform_to_embedding(embedding_source)
    except Exception as e:
        log.warning("enrich 실패 %s: %s", article.get("link"), e)
        return None

    return {
        "title": title,
        "link": article.get("link"),
        "press": article.get("press"),
        "pub_date": article.get("pub_date"),
        "text": text,
        "category": cls["category"],
        "tags": cls["tags"],
        "keywords": keywords,
        "embedding": embedding,
    }


def process(
    target_date: date | None = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    save: bool = True,
) -> dict:
    """target_date 의 HDFS 원본을 읽어 enrich 후 HDFS 에 저장한다."""
    target_date = target_date or date.today()
    date_str = target_date.isoformat()

    raw = read_jsonl(raw_path(date_str))

    # link 기준 배치 내 중복 제거
    uniq: dict[str, dict] = {}
    for a in raw:
        link = a.get("link")
        if link:
            uniq[link] = a
    articles = list(uniq.values())

    # 이미 DB 에 있는 link 는 스킵 (LLM 비용 절감)
    have = existing_links([a["link"] for a in articles])
    todo = [a for a in articles if a["link"] not in have]
    log.info(
        "raw=%d, uniq=%d, 기존스킵=%d, 처리대상=%d",
        len(raw),
        len(articles),
        len(have),
        len(todo),
    )

    # 병렬 enrich
    enriched: list[dict] = []
    if todo:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for res in ex.map(enrich_one, todo):
                if res is not None:
                    enriched.append(res)

    log.info("enrich 완료: %d건", len(enriched))

    path = None
    if save and enriched:
        path = enriched_path(date_str)
        write_jsonl(path, enriched)

    return {
        "date": date_str,
        "raw": len(raw),
        "skipped": len(have),
        "enriched": len(enriched),
        "path": path,
    }


if __name__ == "__main__":
    result = process()
    log.info("처리 결과: %s", result)
