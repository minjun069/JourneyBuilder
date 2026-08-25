import time
import os
from datetime import date

from dotenv import find_dotenv, load_dotenv
load_dotenv(find_dotenv())

import psycopg2

import logging

from crawl_news import get_urls, setup_driver, crawl_news



# PostgreSQL 연결 
def get_connection():

    db_user = os.getenv("DB_USER")
    db_password = os.getenv("DB_PASSWORD")
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME")

    return psycopg2.connect(
        host=db_host,
        port=db_port,
        database=db_name,
        user=db_user,
        password=db_password
    )


# DB INSERT
def save_article_to_db(article):

    query = """
    INSERT INTO news (title, link, pub_date, text)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (link) DO NOTHING
    """

    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(query, (
            article.get("title"),
            article.get("link"),
            article.get("pub_date"),
            article.get("text")
        ))
        logging.info(f"success execute: {article['title']}")
        conn.commit()
        logging.info(f"success commit: {article['title']}")
        cur.close()
        conn.close()

    except Exception as e:
        logging.info(f"failed save_article_to_db: {article['title']} - {e}")


def main(queries, interval):

    seen_links = set()

    while True:
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
            link = news["link"]
            content = crawl_news(driver, link)
            news["text"] = content

        driver.quit()
        logging.info(f"success crawling")

        # --- DB 적재 및 로그 저장 ---
        if total_news:
            for news in total_news:
                save_article_to_db(news)
        else:
            logging.info("No news data to save.")

        logging.info(f"total_news: {len(seen_links)} (added {len(total_news)})")
        time.sleep(interval)

        
if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    queries = ["국민연금", "공적연금", "주택연금", "퇴직연금"]
    interval = 60*60 # 1시간

    main(queries, interval)
