"""뉴스 수집 오케스트레이터 (requests 기반, 결정적 날짜 수집).

흐름:
  list.naver date 페이지네이션으로 target_date 기사 목록 수집
    -> 각 기사 본문 병렬(ThreadPool) 추출
      -> HDFS 원본(JSONL) 저장: /news/raw/YYYY/MM/DD/news.jsonl

list.naver 의 date 파라미터가 발행일을 보장하므로 날짜 휴리스틱이 필요 없다.

실행:
    python -m news.collector.collect
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from zoneinfo import ZoneInfo

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

from common.hdfs_client import raw_path, write_jsonl
from common.logger import get_logger
from news.config import DEFAULT_MAX_PAGES, ECONOMY_SID
from news.collector.naver_section import get_article_list
from news.collector.web_crawler import fetch_article

log = get_logger(__name__)

# 본문 fetch 동시 스레드 수 (requests I/O 바운드)
DEFAULT_MAX_WORKERS = 8
KST = ZoneInfo("Asia/Seoul")


def collect(
    target_date: date | None = None,
    sid: int = ECONOMY_SID,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_workers: int = DEFAULT_MAX_WORKERS,
    save: bool = True,
) -> dict:
    """경제 섹션의 target_date 기사를 수집해 HDFS 에 저장한다."""
    target_date = target_date or datetime.now(KST).date()
    date_str = target_date.isoformat()

    listed = get_article_list(date_str, sid=sid, max_pages=max_pages)

    def _one(it: dict) -> dict | None:
        art = fetch_article(it["link"])
        if art is None:
            return None
        pub = art.get("pub_date")
        # date 파라미터로 이미 보장되지만, 혹시 다른 날짜면 제외 (안전장치)
        if pub != date_str:
            log.warning(
                "기사 날짜 불일치/누락으로 제외 pub_date=%s target=%s link=%s",
                pub,
                date_str,
                it["link"],
            )
            return None
        return {
            "title": art.get("title") or "",
            "link": it["link"],
            "press": it.get("press"),
            "pub_date": pub,
            "text": art.get("text", ""),
        }

    records: list[dict] = []
    if listed:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for res in ex.map(_one, listed):
                if res is not None:
                    records.append(res)

    log.info("%s 최종 기사 수: %d건", date_str, len(records))

    if save and records:
        path = raw_path(date_str)
        write_jsonl(path, records)
        return {"date": date_str, "count": len(records), "path": path}

    return {"date": date_str, "count": len(records), "records": records}


if __name__ == "__main__":
    result = collect()
    log.info("수집 결과: %s", {k: v for k, v in result.items() if k != "records"})
