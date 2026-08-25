"""
Airflow DAG: 사용자별 맞춤 뉴스 보고서 자동 생성 파이프라인.

스케줄: 매일 오전 02:00

전제:
  PostgreSQL(pgvector)과 ElasticSearch에 최신 뉴스가 이미 저장되어 있다고 가정한다.
  뉴스 수집 및 ES 동기화는 별도 파이프라인(Flink 등) 담당.

태스크 순서:
  1. cluster_news    — 뉴스 임베딩 KMeans 클러스터링 + 대표 기사 선정 (Spark)
  2. generate_reports — 사용자별 하이브리드 검색(대표 기사 대상) + 보고서 생성 (Python)

사전 설정 (최초 1회):
  1. Airflow 커넥션 등록 (cluster_news 태스크용):
       Admin > Connections > [+]
         Conn Id   : spark_default
         Conn Type : Spark
         Host      : spark://spark-master
         Port      : 7077

  2. airflow/docker-compose.yml 의 _PIP_ADDITIONAL_REQUIREMENTS 에 추가:
       apache-airflow-providers-apache-spark

  3. Airflow Variables 또는 docker-compose.yml environment 에 DB/ES/API 변수 등록
     (3-3-1 항목 참조)

경로 기준:
  - Spark 컨테이너는 data-pjt/ 를 /opt/app 으로 마운트
  - Report 코드 경로: /opt/app/Report
"""
import os
from datetime import timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from airflow.utils.dates import days_ago

# Spark 컨테이너 기준 경로 (spark/docker-compose.yml: ../ → /opt/app)
REPORT_DIR = os.getenv("REPORT_DIR", "/opt/app/Report")
SPARK_CONN_ID = "spark_default"  # Admin > Connections 에서 spark://spark-master:7077 로 등록

# Spark 작업에 전달할 환경변수
_SPARK_ENV = {
    "DB_HOST":        os.getenv("DB_HOST", "postgres"),
    "DB_PORT":        os.getenv("DB_PORT", "5432"),
    "DB_NAME":        os.getenv("DB_NAME", "streaming_data"),
    "DB_USER":        os.getenv("DB_USER", "ssafy"),
    "DB_PASSWORD":    os.getenv("DB_PASSWORD", ""),
    "ES_HOST":        os.getenv("ES_HOST", "http://elasticsearch:9200"),
    "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
    "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", ""),
}

DEFAULT_ARGS = {
    "owner": "data-team",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
}

with DAG(
    dag_id="report_pipeline",
    default_args=DEFAULT_ARGS,
    description="사용자별 맞춤 뉴스 보고서 자동 생성",
    schedule_interval="0 2 * * *",
    start_date=days_ago(1),
    catchup=False,
    tags=["report", "spark", "news"],
) as dag:

    # ------------------------------------------------------------------
    # Task 1: 뉴스 클러스터링 + 대표 기사 선정 (Spark MLlib KMeans)
    # ------------------------------------------------------------------
    cluster_news = SparkSubmitOperator(
        task_id="cluster_news",
        application=f"{REPORT_DIR}/spark_jobs/cluster_news.py",
        application_args=["7"],
        conn_id=SPARK_CONN_ID,
        packages="org.postgresql:postgresql:42.7.3",
        name="cluster_news",
        env_vars=_SPARK_ENV,
        verbose=False,
    )

    # ------------------------------------------------------------------
    # Task 2: 사용자별 하이브리드 검색 + 보고서 생성 (순수 Python)
    # ------------------------------------------------------------------
    generate_reports = BashOperator(
        task_id="generate_reports",
        bash_command=f"python {REPORT_DIR}/spark_jobs/search_and_report.py 7",
        env=_SPARK_ENV,
        append_env=True,
    )

    cluster_news >> generate_reports
