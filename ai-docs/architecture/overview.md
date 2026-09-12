# 아키텍처 개요

상태: **AI 서비스 경계와 로컬 애플리케이션 실행 토폴로지 승인 — 배포 환경 미정**

## 1. 현재 구성

| 경로 | 현재 역할 | 주요 기술 |
|---|---|---|
| `front-pjt/` | 사용자 화면 | Vue, Vite |
| `backend-pjt/` | 업무 API와 AI 요청 상태 | Django, Django REST Framework, Redis |
| `ai-pjt/` | AI 상담·검색·오케스트레이션 | FastAPI, LangGraph, PostgreSQL checkpoint |
| `ai-docs/` | AI 요구사항·아키텍처·계약·평가·보안 공유 문서 | Markdown |
| `db/` | 애플리케이션 데이터베이스 초기화 | PostgreSQL, pgvector |
| `data-pjt/` | 폐기된 뉴스·데이터 파이프라인 산출물 | 현재 실행·인수 범위에서 제외 |

기존 뉴스 분석과 Kafka·Flink·Spark·Airflow 기반 데이터 파이프라인은 이번 프로젝트에서 폐기한다. 남아 있는 파일은 F01-T03에서 삭제하지 않지만 현재 제품 실행 경로나 지원 대상에 포함하지 않는다.

## 2. 서비스 토폴로지

```text
Browser
  → Django API: 사용자 인증·세션·업무 데이터·AI 작업 상태·승인
      → Redis: AI 요청 상태와 TTL
      → PostgreSQL: 업무 데이터
      → AI FastAPI: 문맥·6-Agent·RAG·도구 오케스트레이션
          → PostgreSQL: AI checkpoint
          → 승인된 LLM·Embedding provider
          → RAG index: 승인 금융 지식·인터페이스 가이드
          → MCP server: 검색·검증·결정론적 계산
      ← v2 SSE: 일반 채팅·Elicit 답변·인용·변경 제안 또는 실패
      ← AI callback: Error Review·명시적 fallback의 종결 결과
  ← 일반 채팅·Elicit SSE 또는 Error Review·fallback polling
```

Browser는 AI·MCP를 직접 호출하지 않는다. AI는 Django 업무 DB를 직접 수정하지 않으며 사용자 승인 전 변경은 제안 상태로 유지한다.

## 3. 로컬 Compose 실행 결정

루트 `docker-compose.yml`을 Backend·AI·PostgreSQL·Redis의 유일한 애플리케이션 통합 실행 진입점으로 사용한다.

- Compose가 project-scoped bridge network와 named volume을 생성한다.
- 서비스는 Compose DNS로 통신하며 host 포트를 기본 공개하지 않는다.
- PostgreSQL과 Redis가 각각 healthcheck를 통과해야 소비 서비스를 시작한다.
- PostgreSQL 준비 후 Django migration one-shot이 성공해야 Backend를 시작한다.
- AI는 PostgreSQL에 의존하지만 현재 사용하지 않는 Redis에는 readiness 의존성을 두지 않는다.
- 기본 애플리케이션 DB와 사용자는 `retirewise_db`, `retirewise`이며 비밀번호에는 동작 가능한 기본값을 두지 않는다.
- 기존 데이터 파이프라인 Compose와 `ssafy/streaming_data/news` 초기화는 새 실행 진입점에서 참조하지 않는다.

Frontend 개발 서버의 Compose 포함 여부와 개발용 host port override는 후속 실행환경 작업에서 결정한다.

## 4. 서비스 책임과 의존 방향

- Frontend는 Django HTTP 계약만 의존한다.
- Django는 사용자·세션·업무 데이터와 AI 작업 상태의 단일 소유자다.
- AI FastAPI는 Django가 전달한 snapshot과 AI 전용 checkpoint·RAG·MCP에 의존한다.
- MCP 도구는 Agent별 allowlist와 최소 권한을 따르고 Browser에 노출되지 않는다.
- 6-Agent는 공통 상태·라우터·평가·보안 기반을 공유한다. 세부 역할은 [`AI 시스템 명세`](../ai/specification.md)를 따른다.
- callback과 도구 결과를 포함한 모든 경계 입력은 schema·인증·권한을 다시 검증한다.

## 5. 데이터 소유권

상담·메시지·확정 재무 데이터는 Django가 소유한다. AI는 요청 시점 snapshot만 받고 업무 데이터에 직접 쓰지 않는다. AI 대화 checkpoint와 RAG 인덱스는 AI 서비스가 소유한다. 상세 소유권과 변경 제안 계약은 [`데이터 스키마`](../data/schema.md)를 따른다.

로컬 F01-T03 Compose는 F01 범위를 위해 Django와 AI checkpoint가 한 PostgreSQL service를 사용한다. 운영 환경에서 DB instance·role·backup을 물리적으로 분리할지는 별도 배포 설계에서 결정한다.

## 6. 비기능 설계

- 보안·개인정보·Zero Trust 핵심 통제는 [`AI 보안 정책 v0.1`](../security/ai-security-policy.md)을 따른다.
- Django·AI·MCP·LLM 장애를 분리하고 실패 callback·timeout·제한 재시도로 복구한다.
- request·thread·trace ID를 가명화해 경계 간 요청을 연결하고 토큰·비용·지연·도구·검색·승인을 관측한다.
- 모델·프롬프트·인덱스·도구 버전을 독립적으로 rollback할 수 있어야 한다.
- 목표 SLO는 1차 baseline 뒤 문서와 검증 게이트에 확정한다.

## 7. 열린 결정

- 배포 환경과 외부 네트워크 경계
- Frontend와 선택 서비스의 Compose 포함 여부
- AI·MCP 서비스 인증의 운영 방식과 credential rotation
- checkpoint·RAG·업무 DB의 물리 분리와 보존기간
