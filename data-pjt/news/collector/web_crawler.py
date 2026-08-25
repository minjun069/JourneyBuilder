"""뉴스 본문 추출 (requests + BeautifulSoup).

네이버 기사 페이지는 본문이 raw HTML 에 포함되어 있어 Selenium 없이 받을 수 있다.

셀렉터:
  - 본문    : #dic_area  (내부 .media_end_summary / .img_desc 는 제거)
  - 발행시각 : span.media_end_head_info_datestamp_time 의 data-date-time
  - 제목    : h2#title_area
"""

import re

import requests
from bs4 import BeautifulSoup

from common.logger import get_logger

log = get_logger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(HEADERS)
    return _session


def fetch_article(url: str) -> dict | None:
    """기사 URL 에서 제목/본문/발행일을 추출. 실패 시 None.

    반환: {"title": str, "text": str, "pub_date": str|None}
    """
    try:
        resp = _get_session().get(url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        log.warning("기사 요청 실패 %s: %s", url, e)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    dic = soup.select_one("#dic_area")
    if dic is None:
        log.warning("본문(#dic_area) 없음: %s", url)
        return None

    # 본문 내 요약/이미지 설명 제거
    for sub in dic.select(".media_end_summary, .img_desc"):
        sub.extract()
    text = re.sub(r"\s+", " ", dic.get_text(" ", strip=True))

    title_el = soup.select_one("h2#title_area")
    dt_el = soup.select_one("span.media_end_head_info_datestamp_time")
    pub_date = None
    if dt_el and dt_el.get("data-date-time"):
        pub_date = dt_el["data-date-time"].split(" ")[0]  # "YYYY-MM-DD"

    return {
        "title": title_el.get_text(strip=True) if title_el else "",
        "text": text,
        "pub_date": pub_date,
    }
