# PyFlink에서 PostgreSQL의 JSON, pgvector 타입을 저장하기 위해 최종 DB 저장 단계에서는 Python 코드에서 INSERT SQL을 직접 실행합니다.
# 일반적인 Flink Sink Connector를 사용할 수도 있지만, PostgreSQL의 JSON, VECTOR 타입은 명시적인 캐스팅이 필요한 경우가 있습니다.
# 해당 예시 코드에서는 구조를 단순하게 유지하기 위해 MapFunction 내부에서 PostgreSQL 저장 로직을 수행합니다.
# 운영 환경이나 확장 구조에서는 Flink JDBC Sink, staging table, 별도 적재/병합 로직 등을 활용할 수 있습니다.

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pyflink.datastream import StreamExecutionEnvironment
from pyflink.common.serialization import SimpleStringSchema
from pyflink.datastream.connectors import FlinkKafkaConsumer
from pyflink.datastream.functions import MapFunction
from pyflink.common import Configuration

import psycopg2
from dotenv import load_dotenv

from preprocessing import (
    transform_classify_category,
    transform_extract_keywords,
    transform_to_embedding,
)


load_dotenv()


def to_none(value):
    """
    값이 None, 빈 문자열, 공백 문자열이면 'none'으로 변환합니다.
    """
    if value is None:
        return "none"

    value = str(value).strip()

    if value == "":
        return "none"

    return value


def process_message(json_str: str) -> dict:
    """
    Kafka에서 받은 JSON 문자열을 파싱하여 dict 형태의 뉴스 데이터로 변환합니다.
    """
    try:
        data = json.loads(json_str)

        return {
            "title": to_none(data.get("title")),
            "link": to_none(data.get("link")),
            "pub_date": to_none(data.get("pub_date")),
            "text": to_none(data.get("text")),
            "query": to_none(data.get("query")),
        }

    except Exception as e:
        print(f"메시지 파싱 오류: {e}")

        return {
            "title": "오류 발생",
            "link": "none",
            "pub_date": "none",
            "text": "none",
            "query": "none",
        }


class DBInsertionMapFunction(MapFunction):
    """
    DB에 뉴스 기사를 저장하는 함수
    """

    def __init__(self):
        self._initialized = False
        self._db_conn = None

    def _initialize(self):
        """
        DB 연결을 초기화합니다.
        """
        self._db_conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 5432)),
            dbname=os.getenv("DB_NAME", "streaming_data"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
        )
        self._db_conn.autocommit = True
        self._initialized = True

    def map(self, article: dict) -> dict:
        if not self._initialized:
            self._initialize()

        # [:200] -> 200자만 저장됨
        title = to_none(article.get("title"))[:200]
        link = to_none(article.get("link"))
        pub_date = to_none(article.get("pub_date"))
        text = to_none(article.get("text"))

        if link == "none":
            print("Skipped article because link is none")
            return article
        
        try:
            if pub_date == "none":
                pub_date = datetime.now()
            else:
                pub_date = datetime.fromisoformat(pub_date).replace(tzinfo=None)
        except Exception:
            pub_date = datetime.now()

        try:
            category = transform_classify_category(text)
            category = to_none(category)[:50]

            if category == "none":
                category = "미분류"

        except Exception as e:
            print("Category transformation error:", e)
            category = "미분류"

        try:
            keywords = transform_extract_keywords(text)

            if keywords is None:
                keywords = []

            keywords_json = json.dumps(keywords, ensure_ascii=False)

        except Exception as e:
            print("Keywords transformation error:", e)
            keywords_json = json.dumps([], ensure_ascii=False)

        try:
            embedding = transform_to_embedding(text)

            if embedding and len(embedding) == 1536:
                embedding_vector = "[" + ",".join(str(value) for value in embedding) + "]"
            else:
                embedding_vector = None

        except Exception as e:
            print("Embedding transformation error:", e)
            embedding_vector = None

        cursor = self._db_conn.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO news (
                    title,
                    link,
                    pub_date,
                    text,
                    category,
                    keywords,
                    embedding
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s::json,
                    %s::vector
                )
                ON CONFLICT (link) DO UPDATE SET
                    title = EXCLUDED.title,
                    pub_date = EXCLUDED.pub_date,
                    text = EXCLUDED.text,
                    category = EXCLUDED.category,
                    keywords = EXCLUDED.keywords,
                    embedding = EXCLUDED.embedding;
                """,
                (
                    title,
                    link,
                    pub_date,
                    text,
                    category,
                    keywords_json,
                    embedding_vector,
                ),
            )

            print(f"Successfully saved article to PostgreSQL: {link}")

        except Exception as e:
            print("DB insertion error:", e)

        finally:
            cursor.close()

        return article


def main():
    consumer_dir = os.path.dirname(os.path.abspath(__file__))
    kafka_jar_path = os.path.join(
        consumer_dir,
        "flink-sql-connector-kafka-3.3.0-1.19.jar",
    )

    config = Configuration()
    config.set_string("pipeline.jars", f"file://{kafka_jar_path}")

    env = StreamExecutionEnvironment.get_execution_environment(configuration=config)
    env.set_parallelism(1)

    kafka_props = {
        "bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092"),
        "group.id": "flink_consumer_group",
    }

    kafka_consumer = FlinkKafkaConsumer(
        topics=os.getenv("KAFKA_TOPIC", "news"),
        deserialization_schema=SimpleStringSchema(),
        properties=kafka_props,
    )

    stream = env.add_source(kafka_consumer)

    processed_stream = stream.map(process_message)
    processed_stream = processed_stream.map(DBInsertionMapFunction())

    print("consumer is running...")

    env.execute("Flink Kafka Consumer Job")


if __name__ == "__main__":
    main()
