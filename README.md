# SSAFY 15기 관통프로젝트

## 팀 프로젝트 진행 방법

1. 각자의 PC에서 `git clone` 명령어로 내려 받습니다.
2. 각자의 `branch`를 생성하여 맡은 업무를 진행합니다.
  - 작업 중 주기적으로 `Merge Request`를 활용하여 서로의 작업물을 동기화 합니다.
3. 각 작업이 완료되기 전, `README.md`에 프로젝트에 대한 설명과 이슈 등을 작성합니다.
4. 당일 18시 이전에 오늘까지 수행한 내용을 최종 commit을 작성하여 제출합니다.
  - 제출은 생성된 원격 저장소의 `master branch`에 merge 하여야만 인정됩니다.
---

# 프로젝트명
## 은퇴 설계에 특화된 AI 기반 재무설계 서비스
사용자의 자산 현황, 현금 흐름, 재무 목표 등을 분석하여  
맞춤형 재무 계획 및 금융 건강 진단을 제공하는 비대면 AI 재무설계 플랫폼

## 프로젝트 구조
backend-pjt/ &ensp;# Django 서버  
front-pjt/ &emsp;&emsp;# Vue 프론트엔드  
data-pjt/ &nbsp;&emsp;&emsp;# 데이터 파이프라인  

## 작업 완료 요약 설명 & 이슈  
1. 네이버 뉴스 API 검색

당일의, 주어진 키워드(국민연금 공적연금 주택연금 퇴직연금)로 검색한 뉴스 목록 조회 (제목, URL, 게시일)

2. 뉴스 본문 크롤링

검색된 url에 대한 본문 크롤링 (네이버 뉴스에 업로드 된 경우로 제한)

3. DB 적재

streaming_data DB의 news 테이블에 적재

4. Docker 초기 환경 설정
- 'docker compose up -d' : 기존 테이블, 데이터 확인 가능
- 'docker compose down' : 작업 완료 후 docker 종료 시 사용, 데이터 유지
5. PostgresSQL 연동 및 설정

6. 뉴스 검색 API 이후, 기사 하나씩 크롤링 하며 producer에서 send

7. Kafka, Flink 컨테이너 구성

8. PostgreSQL pgvector 설정 및 컬럼 추가

9. Flink Consumer 구현
- Kafka 메시지 수신, JSON 파싱
- PostgreSQL 적재

10. AI 기반 데이터 전처리 파이프라인 적용
- 뉴스 카테고리 분류, 키워드 추출, 본문 임베딩 생성

## 브랜치 전략
### 1.브랜치 (Branch) 전략

- **브랜치명 형식:** `태그/기능명`
    - 예시: `feat/stock-chart`, `fix/jwt-auth`

### 2. 커밋 메시지 규칙

- **형식:** `{이모지} {태그}: [{기능명}] 커밋 메세지`
- **예시:** `feat: [stock-chart] 차트 시각화 기능 구현`
- **이모지 및 태그:**
    - `feat`: 새로운 기능 추가
    - `fix`: 버그 수정
    - `refactor`: 코드 리팩토링
    - `comment`: 주석 추가 또는 오타 수정
    - `docs`: README 등 문서 수정
    - `merge`: 브랜치 병합
    - `rename`: 파일/폴더명 수정 및 이동
    - `chore`: 패키지 매니저, 환경 설정 등 기타 변경

### 3. PR (Pull Request) 및 코드 리뷰 규칙

- **PR 제목:** `merge: [{날짜}] {이름} {한 일} {종료 여부}`
    - 예시: `merge: [2026-04-14] 홍길동 차트 시각화(완)`
- **PR 내용:** 전체 작업 내용 요약, 주요 로직, 리뷰어가 집중해서 봐야 할 부분 명시

## 기술 스택

## 환경 변수

## 실행 방법