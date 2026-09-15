# AI 프로젝트 공유 문서

이 디렉터리는 AI 기능을 구현하거나 서비스 간 계약을 변경할 때 함께 읽어야 하는
팀 공유 문서 묶음이다. 최초 공유 대상 6개와 그 문서가 직접·간접으로 참조하는
필수 문서를 함께 담아, 링크를 따라가다가 맥락이 끊기지 않도록 구성했다.

## 읽기 시작점

| 상황 | 먼저 읽을 문서 |
|---|---|
| 제품 목표와 AI 범위 확인 | [프로젝트 요구사항](REQUIREMENTS.md), [AI Agent 요구사항](requirements/ai-agent.md) |
| 서비스 구조와 책임 확인 | [아키텍처 개요](architecture/overview.md), [AI 시스템 명세](ai/specification.md) |
| Frontend·Django·AI 계약 변경 | [Django HTTP API](contracts/http-api.md), [AI 서비스 API 계약](contracts/ai-api.md), [데이터 스키마](data/schema.md) |
| AI 전송 경로 결정 확인 | [SSE 단일 전송 ADR](decisions/0005-remove-non-streaming-ai-transport.md), [부분 응답 제거 ADR](decisions/0006-remove-sse-delta-events.md), [AI 상담 SSE v2](contracts/sse-v2.md) |
| AI 품질과 회귀 기준 확인 | [AI 평가와 운영 관측](ai/evaluation.md), [프로젝트 실행 안내](../README.md) |
| 개인정보·도구·RAG 경계 확인 | [AI 보안 정책](security/ai-security-policy.md), [검색·지식 거버넌스](ai/retrieval.md), [AI 도구 계약](ai/tools.md) |
| 현재 구현과 목표 계약 차이 확인 | [현재 계약 불일치 조사](contracts/current-state.md) |

## 문서 사용 원칙

- 확정된 요구사항·계약·정책은 각 링크의 소유 문서를 기준으로 판단한다.
- 이 README는 여러 원천을 찾기 위한 읽기 관점이며 새로운 계약을 정의하지 않는다.
- 코드나 계약을 변경할 때는 관련 문서를 같은 변경에서 갱신한다.
- 문서와 실제 구현이 다르면 차이를 숨기지 않고 `contracts/current-state.md`에 기록한다.
- 개인 Codex 설정, 로컬 절대경로, 인증 정보와 실행 기록은 이 디렉터리에 넣지 않는다.

## 포함 범위

- 제품 및 AI 요구사항
- 아키텍처와 채택된 AI 구조 결정
- Browser↔Django↔AI 계약과 데이터 의미
- AI orchestration·RAG·도구·평가 명세
- 보안 정책
- 위 문서를 이해하는 데 필요한 구조 결정과 현재 계약 상태

개인 작업 환경을 위한 에이전트 지침, 컨텍스트 관측 설정, Git 인증 절차와 개인
실행 로드맵은 공유 대상에서 제외했다.
