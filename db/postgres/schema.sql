-- schema.sql
-- 배치 파이프라인용 news 테이블 정의
--
-- 적용 대상 DB: news  (init.sql 에서 생성됨)
-- 적용 방법:
--   docker exec -i postgres psql -U ssafy -d news < db/postgres/schema.sql
--
-- 파이프라인 흐름:
--   Airflow -> 수집 -> HDFS -> Spark(정제/키워드/카테고리/임베딩) -> 이 테이블 적재
--
-- 멱등성:
--   link 를 UNIQUE 로 두고 적재 시 ON CONFLICT (link) DO UPDATE 로 중복/재처리 대응


-- pgvector 확장 (init.sql 에서 이미 활성화되지만 단독 적용 대비 보강)
CREATE EXTENSION IF NOT EXISTS vector;


CREATE TABLE IF NOT EXISTS news (
    id          BIGSERIAL PRIMARY KEY,

    -- 수집 원본 필드
    title       VARCHAR(200),                 -- 기사 제목
    link        TEXT NOT NULL UNIQUE,         -- 기사 URL (중복 판단 기준)
    pub_date    DATE,                         -- 기사 발행일
    text        TEXT,                         -- 본문
    press       VARCHAR(100),                 -- 언론사

    -- Spark 처리 결과 필드
    category    VARCHAR(50),                  -- 분류된 카테고리 (단일, taxonomy)
    tags        JSONB,                        -- 통제 태그 배열 (taxonomy 고정 어휘, 필터용)
    keywords    JSONB,                        -- 자유 추출 키워드 배열 (검색/롱테일)
    embedding   VECTOR(1536),                 -- Gemini 임베딩 (gemini-embedding-001, 1536차원)

    -- 배치 메타데이터
    batch_date  DATE,                         -- 배치 실행(수집) 날짜 - 멱등 재처리 단위
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- 벡터 유사도 검색용 인덱스 (코사인 거리 기준, HNSW)
-- 리포트 시스템이 사용자 관심사 임베딩과 뉴스 임베딩을 비교할 때 사용
CREATE INDEX IF NOT EXISTS idx_news_embedding
    ON news USING hnsw (embedding vector_cosine_ops);

-- 조회 패턴별 보조 인덱스
CREATE INDEX IF NOT EXISTS idx_news_category   ON news (category);
CREATE INDEX IF NOT EXISTS idx_news_pub_date   ON news (pub_date);
CREATE INDEX IF NOT EXISTS idx_news_batch_date ON news (batch_date);

-- 태그/키워드 배열 검색용 GIN 인덱스 (예: tags @> '["IRP"]', keywords @> '["연금"]')
CREATE INDEX IF NOT EXISTS idx_news_tags       ON news USING gin (tags);
CREATE INDEX IF NOT EXISTS idx_news_keywords   ON news USING gin (keywords);
