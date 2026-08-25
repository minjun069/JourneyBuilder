import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

DATA_PJT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = DATA_PJT_DIR.parent

load_dotenv(REPO_ROOT / ".env")
load_dotenv(DATA_PJT_DIR / ".env", override=True)


def _connect(dbname: str):
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=dbname,
        user=os.getenv("DB_USER", "ssafy"),
        password=os.getenv("DB_PASSWORD", "ssafy"),
    )


def get_news_connection():
    """뉴스/클러스터링 전용 DB 연결."""
    return _connect(os.getenv("NEWS_DB_NAME", "news"))


def get_app_connection():
    """백엔드 사용자/상담/보고서 전용 DB 연결."""
    return _connect(os.getenv("APP_DB_NAME", "retirewise_db"))


def get_connection():
    """하위 호환용: 기존 검색 코드는 news DB 를 기본으로 사용한다."""
    return get_news_connection()
