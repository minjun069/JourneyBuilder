-- report_tables.sql
-- 보고서 시스템 신규 테이블
--
-- 의존 테이블 (기존):
--   accounts_users           (id, email, name, birth_year, is_active)
--   consulting_sessions      (user_id, status)
--   consulting_financial_data (session_id, goal_type, assets_*, monthly_*)


-- 1. 사용자별 보고서 관심사 설정
--    consulting_financial_data에서 자동 유도되지 않는 추가 관심사를 수동으로 등록
CREATE TABLE IF NOT EXISTS report_user_preferences (
    id                   SERIAL          PRIMARY KEY,
    user_id              INTEGER         NOT NULL
                             REFERENCES accounts_users(id) ON DELETE CASCADE,
    -- 관심 검색 키워드 (예: {'국민연금', '퇴직연금', '부동산'})
    interest_keywords    TEXT[]          NOT NULL DEFAULT '{}',
    -- 관심 뉴스 카테고리 필터 (예: {'정책', '재테크'})
    -- 빈 배열이면 전체 카테고리 대상으로 검색
    interest_categories  VARCHAR(50)[]   NOT NULL DEFAULT '{}',
    -- 보고서 1건당 참조할 최대 뉴스 수
    max_news_per_report  INTEGER         NOT NULL DEFAULT 10,
    created_at           TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS idx_report_prefs_user
    ON report_user_preferences(user_id);


-- 2. 생성된 보고서 저장
CREATE TABLE IF NOT EXISTS reports (
    id            SERIAL      PRIMARY KEY,
    user_id       INTEGER     NOT NULL
                      REFERENCES accounts_users(id) ON DELETE CASCADE,
    -- 보고서 대상 기간 (보통 7일)
    period_start  DATE        NOT NULL,
    period_end    DATE        NOT NULL,
    -- LLM이 생성한 전체 요약 (한 문단)
    summary       TEXT,
    -- 섹션별 상세 내용 JSON (summary는 위 컬럼에 별도 저장)
    -- 형식: { sections: [{category, headline, detail}], action_items: [] }
    content       JSONB,
    -- 보고서 생성에 사용된 news.id 목록
    news_ids      INTEGER[]   NOT NULL DEFAULT '{}',
    -- 보고서를 실제로 실행한 날짜 (period_start/end와 별개)
    run_date      DATE        NOT NULL DEFAULT CURRENT_DATE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reports_user_id
    ON reports(user_id);
CREATE INDEX IF NOT EXISTS idx_reports_period
    ON reports(period_start, period_end);
CREATE INDEX IF NOT EXISTS idx_reports_run_date
    ON reports(user_id, run_date DESC);
CREATE INDEX IF NOT EXISTS idx_reports_created_at
    ON reports(created_at DESC);


-- 3. 뉴스 클러스터링 결과 저장
--    cluster_news.py Spark 잡이 매일 실행하며 run_date 기준으로 교체됨
CREATE TABLE IF NOT EXISTS news_clusters (
    id                     SERIAL      PRIMARY KEY,
    -- 클러스터 번호 (KMeans 결과, 0부터 시작)
    cluster_id             INTEGER     NOT NULL,
    -- 해당 클러스터의 대표 기사 (centroid에 가장 가까운 기사)
    representative_news_id INTEGER     NOT NULL REFERENCES news(id),
    -- 클러스터에 속하는 전체 news.id 목록
    news_ids               INTEGER[]   NOT NULL DEFAULT '{}',
    run_date               DATE        NOT NULL DEFAULT CURRENT_DATE,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_news_clusters_run_date
    ON news_clusters(run_date DESC);
CREATE INDEX IF NOT EXISTS idx_news_clusters_representative
    ON news_clusters(representative_news_id);


-- 4. updated_at 자동 갱신 트리거 (report_user_preferences)
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_report_prefs_updated_at ON report_user_preferences;
CREATE TRIGGER trg_report_prefs_updated_at
    BEFORE UPDATE ON report_user_preferences
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
