import os
import requests
from datetime import datetime

from dotenv import find_dotenv, load_dotenv
load_dotenv(find_dotenv())

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import logging

# 네이버 뉴스 검색 API 
def get_urls(query, target_date):
    id = os.getenv("X-Naver-Client-Id")
    secret = os.getenv("X-Naver-Client-Secret")

    start = 1
    items = []
    while start < 1000:
        response = requests.get(
            url="https://openapi.naver.com/v1/search/news.json",
            headers={
                "X-Naver-Client-Id": id,
                "X-Naver-Client-Secret": secret
            },
            params={
                "query": query,
                "display": 100,
                "start": start,
                "sort": "date"
            }
        )
        data = response.json()
        temp_items = data['items']
        items.extend(temp_items)

        last_date = datetime.strptime(temp_items[-1]["pubDate"], '%a, %d %b %Y %H:%M:%S %z').replace(tzinfo=None).date()
        if last_date < target_date:
            break
        start += 100
    
    news_list = []
    for item in items:
        pub_date = datetime.strptime(item['pubDate'], '%a, %d %b %Y %H:%M:%S %z').replace(tzinfo=None).date()
        if pub_date < target_date:
            break
        link = item["link"]
        if not "n.news.naver" in link:
            continue
        news = {
            "title": item["title"],
            "link": link,
            "pub_date": pub_date,
            "query": query
        }
        news_list.append(news)
    
    return news_list


# Chrome WebDriver 설정 및 초기화
def setup_driver():
    logging.info("try setup_driver")
    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")  # 샌드박스 모드 비활성화 (리눅스 환경에서 필요할 수 있음)
        options.add_argument("--disable-dev-shm-usage")  # 공유 메모리 사용 제한 (리소스 문제 방지)
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

        driver = webdriver.Chrome(options=options) # Chrome 드라이버 실행
        logging.info("success setup_driver")
        return driver
    
    except Exception as e:
        logging.info(f"failed setup_driver {e}")


# 뉴스 크롤링
def crawl_news(driver, url):
    """웹사이트로 이동 및 로딩 대기"""
    logging.info("try navigate_to_page")
    try:
        driver.get(url) # 웹사이트 접속
        WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.ID, "ct"))) # 페이지 요소 로딩 대기
        logging.info("success navigate_to_page")
        
    except Exception as e:
        logging.info(f"failed navigate_to_page {e}")
    
    """크롤링"""
    logging.info("try scrape_news")
    try:
        content = driver.find_element(By.ID, "ct")
        dic_area =  content.find_element(By.ID, "dic_area")

        full_text = dic_area.get_attribute('innerText')
        try:
            media_end_summary = dic_area.find_element(By.CLASS_NAME, "media_end_summary").text
            img_desc = dic_area.find_element(By.CLASS_NAME, "img_desc").text
            text = full_text.replace(media_end_summary, "").strip()
            text = text.replace(img_desc, "").strip()
        except:
            text = full_text.strip()

        text = text.replace('\n', ' ')
        logging.info("success scrape_news")
        
        return text
    
    except Exception as e:
        logging.info(f"failed scrape_news: {e}")
        return ""