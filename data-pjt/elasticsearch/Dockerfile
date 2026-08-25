# Elasticsearch + 한국어 형태소 분석기(nori)
# 뉴스 제목/본문 한국어 전문검색을 위해 analysis-nori 플러그인을 미리 설치한 커스텀 이미지.
# python 클라이언트(elasticsearch==8.17.1)와 버전을 맞추기 위해 8.17.1 사용.

FROM docker.elastic.co/elasticsearch/elasticsearch:8.17.1

# 한국어 형태소 분석 플러그인 설치 (--batch: 권한 프롬프트 자동 동의)
RUN bin/elasticsearch-plugin install --batch analysis-nori
