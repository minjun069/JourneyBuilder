"""네이버 뉴스 경제 섹션 - 날짜별 기사 목록 수집 (requests 기반).

구 list.naver 인터페이스를 사용한다. date 파라미터로 발행일이 보장되므로,
별도 날짜 휴리스틱 없이 결정적으로 그 날짜의 기사만 모은다.

    https://news.naver.com/main/list.naver?mode=LS2D&mid=shm&sid1=101&date=YYYYMMDD&page=N

페이지를 1부터 늘리며, 신규 기사가 없는 페이지가 나오면 종료한다.
목록 HTML 에서 기사 link 와 언론사(.writing)를 추출한다 (사이드바 '많이 본 뉴스'는
ul.type06_headline / ul.type06 셀렉터로 자연히 제외됨).
"""

from datetime import date

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from common.logger import get_logger

log = get_logger(__name__)

LIST_URL = "https://news.naver.com/main/list.naver"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
RETRY_STATUS_CODES = (429, 500, 502, 503, 504)


def _compact_date(date_str: str) -> str:
    """YYYY-MM-DD 문자열을 list.naver date 파라미터 형식으로 변환."""
    return date.fromisoformat(date_str).strftime("%Y%m%d")


def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=RETRY_STATUS_CODES,
        allowed_methods={"GET"},
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _parse_list_page(html: str) -> list[dict]:
    """목록 페이지 HTML 에서 메인 기사들의 link/press 를 추출."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for li in soup.select("ul.type06_headline li, ul.type06 li"):
        a = li.select_one("a[href*='/article/']")
        if not a or not a.get("href"):
            continue
        link = a["href"].split("?")[0]
        press = li.select_one(".writing")
        items.append(
            {"link": link, "press": press.get_text(strip=True) if press else None}
        )
    return items


def get_article_list(date_str: str, sid: int = 101, max_pages: int = 100) -> list[dict]:
    """date_str(YYYY-MM-DD) 경제 기사 목록을 페이지네이션으로 수집한다.

    신규 기사가 없는 페이지가 나오면 종료. 반환: [{"link", "press"}] (link 중복 제거)
    """
    date_compact = _compact_date(date_str)
    if max_pages < 1:
        raise ValueError("max_pages must be greater than 0")

    session = _build_session()

    seen: set[str] = set()
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        try:
            resp = session.get(
                LIST_URL,
                params={
                    "mode": "LS2D",
                    "mid": "shm",
                    "sid1": sid,
                    "date": date_compact,
                    "page": page,
                },
                timeout=10,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning("목록 요청 실패 date=%s page=%d: %s", date_str, page, e)
            raise

        new = [it for it in _parse_list_page(resp.text) if it["link"] not in seen]
        if not new:
            log.info("page %d 신규 기사 없음 -> 종료", page)
            break

        for it in new:
            seen.add(it["link"])
            out.append(it)

    log.info("date=%s 기사 목록 수집: %d건", date_str, len(out))
    return out
