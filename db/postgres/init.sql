-- init.sql
-- PostgreSQL + pgvector 초기 설정
--
-- 목적:
--   1. pgvector 확장 활성화
--   2. ssafy가 public 스키마에서 작업할 수 있도록 권한 설정
--   3. 배치 파이프라인용 news DB 생성 및 동일 설정 적용
--   4. Django 백엔드용 retirewise_db 생성 및 동일 설정 적용
--
-- DB 구성:
--   - streaming_data : Kafka/Flink 실시간 파이프라인용 (POSTGRES_DB, 기본 DB)
--   - news           : Airflow/Spark 배치 파이프라인용 (신규)
--   - retirewise_db  : Django 백엔드용
--
-- 주의:
--   이 파일은 PostgreSQL 데이터 볼륨이 처음 생성될 때 1회만 실행됨


-- ============================================================
-- 1. 기본 DB (streaming_data) 설정
-- ============================================================

-- pgvector 확장 활성화 (embedding VECTOR 타입 사용을 위해 필요)
CREATE EXTENSION IF NOT EXISTS vector;

-- public 스키마 권한 부여
GRANT USAGE ON SCHEMA public TO ssafy;
GRANT CREATE ON SCHEMA public TO ssafy;

-- 앞으로 ssafy가 생성하는 객체에 대한 기본 권한 설정
ALTER DEFAULT PRIVILEGES FOR ROLE ssafy IN SCHEMA public
GRANT ALL PRIVILEGES ON TABLES TO ssafy;

ALTER DEFAULT PRIVILEGES FOR ROLE ssafy IN SCHEMA public
GRANT ALL PRIVILEGES ON SEQUENCES TO ssafy;

ALTER DEFAULT PRIVILEGES FOR ROLE ssafy IN SCHEMA public
GRANT ALL PRIVILEGES ON FUNCTIONS TO ssafy;

-- 현재 존재하는 객체에 대한 권한 부여
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO ssafy;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO ssafy;
GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO ssafy;


-- ============================================================
-- 2. 배치 파이프라인용 DB (news) 생성
-- ============================================================

-- news DB 생성 (소유자: ssafy)
CREATE DATABASE news OWNER ssafy;

-- 이후 명령은 news DB에 대해 실행
\c news

-- pgvector 확장 활성화
CREATE EXTENSION IF NOT EXISTS vector;

-- public 스키마 권한 부여
GRANT USAGE ON SCHEMA public TO ssafy;
GRANT CREATE ON SCHEMA public TO ssafy;

-- 기본 권한 설정
ALTER DEFAULT PRIVILEGES FOR ROLE ssafy IN SCHEMA public
GRANT ALL PRIVILEGES ON TABLES TO ssafy;

ALTER DEFAULT PRIVILEGES FOR ROLE ssafy IN SCHEMA public
GRANT ALL PRIVILEGES ON SEQUENCES TO ssafy;

ALTER DEFAULT PRIVILEGES FOR ROLE ssafy IN SCHEMA public
GRANT ALL PRIVILEGES ON FUNCTIONS TO ssafy;

-- 현재 존재하는 객체에 대한 권한 부여
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO ssafy;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO ssafy;
GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO ssafy;


-- ============================================================
-- 3. Django 백엔드용 DB (retirewise_db) 생성
-- ============================================================

-- retirewise_db 생성 (소유자: ssafy)
CREATE DATABASE retirewise_db OWNER ssafy;

-- 이후 명령은 retirewise_db에 대해 실행
\c retirewise_db

-- pgvector 확장 활성화
CREATE EXTENSION IF NOT EXISTS vector;

-- public 스키마 권한 부여
GRANT USAGE ON SCHEMA public TO ssafy;
GRANT CREATE ON SCHEMA public TO ssafy;

-- 기본 권한 설정
ALTER DEFAULT PRIVILEGES FOR ROLE ssafy IN SCHEMA public
GRANT ALL PRIVILEGES ON TABLES TO ssafy;

ALTER DEFAULT PRIVILEGES FOR ROLE ssafy IN SCHEMA public
GRANT ALL PRIVILEGES ON SEQUENCES TO ssafy;

ALTER DEFAULT PRIVILEGES FOR ROLE ssafy IN SCHEMA public
GRANT ALL PRIVILEGES ON FUNCTIONS TO ssafy;

-- 현재 존재하는 객체에 대한 권한 부여
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO ssafy;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO ssafy;
GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO ssafy;
