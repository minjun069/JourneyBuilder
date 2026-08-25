# Airflow + 뉴스 파이프라인 실행 환경
#
# 수집기를 requests 기반으로 전환하여 Chrome/Selenium 의존성을 제거했다.
# DAG 는 PythonOperator 로 news.* / common.* 모듈을 직접 호출한다 (PYTHONPATH=/opt/app).

FROM apache/airflow:2.10.5

# 파이프라인 Python 의존성 (버전 핀)
RUN pip install --no-cache-dir \
    requests==2.32.3 \
    beautifulsoup4==4.13.3 \
    openai==2.8.1 \
    tiktoken==0.8.0 \
    hdfs==2.7.3 \
    psycopg2-binary==2.9.10 \
    "elasticsearch==8.17.1" \
    python-dotenv==1.0.1
