# producer/kafka_producer.py

import time
from datetime import date
import json

from dotenv import find_dotenv, load_dotenv
load_dotenv(find_dotenv())

from kafka import KafkaProducer

import logging

from crawl_news import get_urls, setup_driver, crawl_news


# Kafka 브로커 주소
KAFKA_BROKER = "localhost:29092"

# 전송할 Kafka 토픽
TOPIC = "news"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Kafka Producer 생성
producer = KafkaProducer(
    bootstrap_servers=KAFKA_BROKER,
    value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8")
)

def main(queries, interval):

    seen_links = set()

    while True:
        try:
            # --- 네이버 뉴스 검색 API ---
            target_date = date.today()
            logging.info(f"Get API data {target_date}")

            total_news = []
            for query in queries:
                new_news = get_urls(query, target_date)
                total_news.extend(new_news)
            
            temp_total = []
            for news in total_news:
                link = news["link"]
                if link in seen_links:
                    continue
                seen_links.add(link)
                temp_total.append(news)
            total_news = temp_total
            logging.info(f"success API data")


            # --- 뉴스 본문 크롤링 ---
            driver = setup_driver()

            for news in total_news:
                news["text"] = crawl_news(driver, news["link"])
                news["pub_date"] = news["pub_date"].strftime('%Y-%m-%d')

                producer.send(TOPIC, news)
                producer.flush()

                logging.info(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Sent: {news['title']}")

            
            logging.info(f"success save total_news: {len(seen_links)} (added {len(total_news)})")
            time.sleep(interval)
            
        except Exception as e:
            logging.info(f"Error: {e}")
            
        finally:
            if driver:
                driver.quit()
            


if __name__ == "__main__":

    queries = ["국민연금", "공적연금", "주택연금", "퇴직연금"]
    interval = 60*60 # 1시간
    
    main(queries, interval)
