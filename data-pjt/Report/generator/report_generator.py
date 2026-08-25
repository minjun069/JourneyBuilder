"""
LLM 기반 맞춤형 뉴스 보고서 생성 및 DB 저장.

흐름:
  1. _fetch_user_profile()  — 사용자 재무 프로필 + 관심사 조회
  2. _derive_search_params() — 프로필 → 검색 파라미터 유도 (search_and_report에서 hybrid_search 호출 전 사용)
  3. generate_report()       — Top-K 뉴스 + 프로필로 GPT 보고서 생성
  4. save_report()           — reports 테이블 UPSERT
"""
import json
import os
from datetime import date

from openai import OpenAI

from db import get_app_connection

_client = OpenAI(base_url="https://gms.ssafy.io/gmsapi/api.openai.com/v1")

ALL_CATEGORIES = [
    "정책·제도", "투자·재테크", "연금·은퇴", "금융상품",
    "자산운용", "기업·실적", "시장동향", "사회·경제",
]

NEWS_REPORT_TYPE = "news_report"


# ---------------------------------------------------------------------------
# 사용자 프로필 조회
# ---------------------------------------------------------------------------

def _fetch_user_profile(user_id: int) -> dict:
    """
    사용자 재무 프로필과 보고서 관심사를 조합하여 반환.
    상담이 완료된 세션의 가장 최신 재무 데이터를 사용한다.
    """
    sql = """
        WITH latest_session AS (
            SELECT id, user_id
            FROM consulting_sessions
            WHERE user_id = %s AND status = 'completed'
            ORDER BY created_at DESC
            LIMIT 1
        ),
        financial_asset AS (
            SELECT
                session_id,
                COALESCE(SUM(current_value), 0) AS assets_financial
            FROM consulting_financial_assets
            GROUP BY session_id
        ),
        pension_asset AS (
            SELECT
                session_id,
                COALESCE(SUM(current_value), 0) AS assets_pension
            FROM consulting_pension_assets
            GROUP BY session_id
        ),
        real_estate_asset AS (
            SELECT
                session_id,
                COALESCE(SUM(current_value), 0) AS assets_real_estate
            FROM consulting_real_estate_assets
            GROUP BY session_id
        ),
        expense AS (
            SELECT
                session_id,
                COALESCE(SUM(amount), 0) AS monthly_expense
            FROM consulting_expenses
            GROUP BY session_id
        )
        SELECT
            u.name,
            u.birth_year,
            bi.need AS goal_type,
            bi.retirement_age AS target_retirement_age,
            COALESCE(pa.assets_pension, 0) AS assets_pension,
            COALESCE(fa.assets_financial, 0) AS assets_financial,
            COALESCE(ra.assets_real_estate, 0) AS assets_real_estate,
            bi.monthly_income,
            COALESCE(ex.monthly_expense, 0) AS monthly_expense,
            COALESCE(NULLIF(bi.interest, ''), '') AS interest,
            ls.id AS session_id
        FROM accounts_users u
        LEFT JOIN latest_session ls ON ls.user_id = u.id
        LEFT JOIN consulting_basic_info bi ON bi.session_id = ls.id
        LEFT JOIN financial_asset fa ON fa.session_id = ls.id
        LEFT JOIN pension_asset pa ON pa.session_id = ls.id
        LEFT JOIN real_estate_asset ra ON ra.session_id = ls.id
        LEFT JOIN expense ex ON ex.session_id = ls.id
        WHERE u.id = %s;
    """
    conn = get_app_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id, user_id))
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return {}

    return {
        "name": row[0],
        "birth_year": row[1],
        "goal_type": row[2],
        "target_retirement_age": row[3],
        "assets_pension": row[4],
        "assets_financial": row[5],
        "assets_real_estate": row[6],
        "monthly_income": row[7],
        "monthly_expense": row[8],
        "interest_keywords": [row[9]] if row[9] else [],
        "interest_categories": [],
        "session_id": row[10],
    }


# ---------------------------------------------------------------------------
# 검색 파라미터 유도 (hybrid_search 호출 전 단계에서 사용)
# ---------------------------------------------------------------------------

def _derive_search_params(profile: dict) -> tuple[str, list[str], list[str]]:
    """
    사용자 재무 프로필에서 hybrid_search에 전달할 파라미터를 유도한다.

    Returns:
        (query_text, keywords, categories)
        query_text  — 벡터 검색용 자연어 쿼리
        keywords    — ES BM25 검색 키워드 목록
        categories  — 카테고리 필터 (빈 리스트면 전체)
    """
    base_keywords: list[str] = ["연금", "노후", "은퇴"]

    if profile.get("assets_pension"):
        base_keywords += ["국민연금", "퇴직연금", "연금 개혁"]
    if profile.get("assets_financial"):
        base_keywords += ["주식", "펀드", "재테크", "금리"]
    if profile.get("assets_real_estate"):
        base_keywords += ["부동산", "주택연금", "임대"]
    if profile.get("goal_type"):
        base_keywords.append(profile["goal_type"])

    user_keywords = profile.get("interest_keywords", [])
    keywords = list(dict.fromkeys(base_keywords + user_keywords))

    categories = profile.get("interest_categories") or ALL_CATEGORIES
    query_text = " ".join(keywords[:10])

    return query_text, keywords, categories


# ---------------------------------------------------------------------------
# 보고서 생성
# ---------------------------------------------------------------------------

def _build_prompt(profile: dict, news_items: list[dict]) -> str:
    news_block = "\n".join(
        f"[{i + 1}] {item['title']}"
        f" ({item.get('category', '미분류')},"
        f" {str(item.get('pub_date', ''))[:10]})"
        for i, item in enumerate(news_items)
    )

    current_year = date.today().year
    age_str = (
        f"{current_year - profile['birth_year']}세"
        if profile.get("birth_year")
        else "나이 미상"
    )
    income = profile.get("monthly_income") or 0
    expense = profile.get("monthly_expense") or 0

    return f"""
[사용자 정보]
- 이름: {profile.get('name', '사용자')} ({age_str})
- 재무 목표: {profile.get('goal_type', '미설정')}
- 목표 은퇴 나이: {profile.get('target_retirement_age', '미설정')}세
- 월 소득: {income:,}원 / 월 지출: {expense:,}원

[관련 뉴스 목록]
{news_block}

[출력 형식]
{{
  "summary": "이번 주 주요 요약 (2~3문장, 사용자 상황과 연계)",
  "sections": [
    {{
      "category": "카테고리명",
      "headline": "핵심 내용 한 줄",
      "detail": "사용자에게 미치는 영향 및 시사점 (2~3문장)"
    }}
  ],
  "action_items": ["사용자가 취할 수 있는 구체적 행동 1", "행동 2"]
}}
"""


def generate_report(user_id: int, news_items: list[dict]) -> dict:
    """
    Top-K 뉴스 목록을 바탕으로 사용자 맞춤 보고서를 생성한다.

    Args:
        user_id: accounts_users.id
        news_items: hybrid_search 결과 ({id, title, link, pub_date, category})

    Returns:
        보고서 dict {summary, sections, action_items}
    """
    profile = _fetch_user_profile(user_id)
    prompt = _build_prompt(profile, news_items)

    response = _client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "당신은 개인 재무 보고서를 작성하는 전문가입니다. 아래 사용자 정보와 최근 뉴스를 바탕으로 맞춤형 주간 뉴스 보고서를 작성하세요.  — JSON만, 다른 텍스트 금지",
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=1500,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"summary": raw, "sections": [], "action_items": []}


# ---------------------------------------------------------------------------
# 보고서 저장
# ---------------------------------------------------------------------------

def save_report(user_id: int, news_ids: list[int], content: dict) -> int:
    """
    생성된 보고서를 reports 테이블에 INSERT한다.
    기본 키는 SERIAL id이며 중복 제약 없이 매 실행마다 새 행이 생성된다.

    Args:
        user_id: accounts_users.id
        news_ids: 참조한 news.id 목록
        content: generate_report() 반환값

    Returns:
        저장된 reports.id
    """
    profile = _fetch_user_profile(user_id)
    session_id = profile.get("session_id")
    if not session_id:
        raise ValueError(f"completed consulting session not found for user_id={user_id}")

    sql = """
        INSERT INTO reports
            (session_id, report_type, version, is_latest, status, content, generated_at, created_at)
        VALUES
            (%s, %s, %s, %s, %s, %s::jsonb, NOW(), NOW())
        RETURNING id;
    """

    conn = get_app_connection()
    try:
        report_content = {
            **content,
            "news_ids": news_ids,
            "generated_by": "data-pjt/Report",
        }
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE reports
                SET is_latest = FALSE
                WHERE session_id = %s
                  AND report_type = %s
                  AND is_latest = TRUE
                """,
                (session_id, NEWS_REPORT_TYPE),
            )
            cur.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1
                FROM reports
                WHERE session_id = %s
                  AND report_type = %s
                """,
                (session_id, NEWS_REPORT_TYPE),
            )
            version = cur.fetchone()[0]
            cur.execute(
                sql,
                (
                    session_id,
                    NEWS_REPORT_TYPE,
                    version,
                    True,
                    "ready",
                    json.dumps(report_content, ensure_ascii=False),
                ),
            )
            report_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    return report_id
