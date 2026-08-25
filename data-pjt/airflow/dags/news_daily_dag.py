"""뉴스 일일 배치 파이프라인 DAG.

흐름:
    collect (수집 -> HDFS raw)
      -> process (정제/중복제거/LLM enrich -> HDFS enriched)
        -> load (PostgreSQL + Elasticsearch 이중 적재)

스케줄: 매일 02:00 (Asia/Seoul). 02:00 에 실행되는 run 의 논리날짜(ds)는 전날이며,
        그 전날(=수집 대상일)의 경제 뉴스를 수집/처리/적재한다.

수동 테스트:
    'Trigger DAG w/ config' 로 아래 conf 를 주면 날짜/규모를 바꿀 수 있다.
      {"date": "2026-06-23", "max_pages": 2}
"""

from datetime import date, timedelta

import pendulum
from airflow import DAG
from airflow.operators.python import PythonOperator

from news.collector.collect import collect
from news.config import DEFAULT_MAX_PAGES
from news.load.load import load
from news.processor.process import process

KST = pendulum.timezone("Asia/Seoul")


def _conf(dag_run) -> dict:
    return (dag_run.conf if dag_run and dag_run.conf else {}) or {}


def _target_date(ds: str, dag_run) -> date:
    return date.fromisoformat(_conf(dag_run).get("date", ds))


def task_collect(ds=None, dag_run=None, **_):
    max_pages = int(_conf(dag_run).get("max_pages", DEFAULT_MAX_PAGES))
    return collect(target_date=_target_date(ds, dag_run), max_pages=max_pages)


def task_process(ds=None, dag_run=None, **_):
    return process(target_date=_target_date(ds, dag_run))


def task_load(ds=None, dag_run=None, **_):
    return load(target_date=_target_date(ds, dag_run))


default_args = {
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="news_daily",
    description="경제 뉴스 수집 -> 처리 -> PostgreSQL/Elasticsearch 적재",
    schedule="0 2 * * *",
    start_date=pendulum.datetime(2026, 6, 1, tz=KST),
    catchup=False,
    default_args=default_args,
    tags=["news", "batch"],
) as dag:
    collect_op = PythonOperator(task_id="collect", python_callable=task_collect)
    process_op = PythonOperator(task_id="process", python_callable=task_process)
    load_op = PythonOperator(task_id="load", python_callable=task_load)

    collect_op >> process_op >> load_op
