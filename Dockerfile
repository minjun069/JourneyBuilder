FROM flink:1.19-scala_2.12-java17

# 2. Python & pip 설치 + PyFlink 설치
RUN apt-get update && \
    apt-get install -y python3 python3-pip && \
    pip install --no-cache-dir apache-flink==1.19.3 && \
    pip install --no-cache-dir pandas psycopg2-binary python-dotenv openai tiktoken pydantic

# PyFlink operators require this jar on the cluster runtime classpath.
COPY data-pjt/consumer/flink-sql-connector-kafka-3.3.0-1.19.jar /opt/flink/lib/

RUN cp /opt/flink/opt/flink-python-*.jar /opt/flink/lib/

# consumer/ 디렉터리는 docker-compose-flink.yml에서 /opt/flink/consumer로 마운트됩니다.
# TaskManager의 Python worker가 preprocessing.py 같은 로컬 모듈을 import할 수 있게 경로를 고정합니다.
ENV PYTHONPATH=/opt/flink/consumer

# 3. 기본 Python3 링크 설정 - python 명령어로 실행될 수 있게
RUN ln -s /usr/bin/python3 /usr/bin/python

# 4. 작업 디렉토리 설정
WORKDIR /opt/flink
