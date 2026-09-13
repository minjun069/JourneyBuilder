# 현재 AI 상담 계약 상태

상태: **ADR-0005 SSE 단일 전송 구현 반영**

채택 계약은 [Django HTTP API](http-api.md), [AI 서비스 API](ai-api.md),
[AI 상담 SSE v2](sse-v2.md)가 소유한다.

## 구현 상태

| 경계 | 현재 상태 |
|---|---|
| Browser → Django | 일반 상담·Elicit·Error Review 모두 `POST /api/v2/consultations/{session_id}/messages/stream/` |
| Django → AI | `POST /v2/chat/stream`과 service bearer 인증 |
| terminal | `start -> delta* -> done | error`; terminal 없는 EOF는 실패 |
| 제거된 경로 | 비스트리밍 메시지 POST, AI 결과 polling, AI `/v2/chat`, AI→Django HTTP callback, 구형 `/chat[/stream]` |
| 멱등성 | 사용자·세션·`Idempotency-Key` digest와 `request_id` 결합 |
| 완료 재생 | 같은 key는 graph를 재실행하지 않고 terminal receipt에서 SSE 결과 재생 |
| 저장 | completed/failed receipt와 assistant message를 transaction으로 확정하고 Redis 유실 시 복구 |
| 제안 결정 | 모든 Input Assistance change는 `proposal/pending`; 승인·거절은 FE 로컬에서만 처리하고 결정 API·서버 이력은 없음 |

receipt의 DB 필드명 `callback_digest`는 기존 migration 호환을 위해 유지하지만 현재
의미는 SSE terminal payload digest다. 브라우저 연결과 독립적인 장기 실행이 필요하면
polling을 되살리지 않고 RabbitMQ 영속 작업으로 분리한다.

## 검증 기준

- 공용 `lifecycle.json`과 `sse.json`은 SSE 단일 전송과 제거 route 목록을 고정한다.
- Backend는 SSE 정상·실패·취소·동일 key replay·receipt 복구를 검증한다.
- AI는 `/v2/chat/stream` 인증·terminal을 검증하고 제거된 route의 404를 고정한다.
- Frontend는 polling helper 없이 네 SSE event와 Error Review 비차단 저장을 소비하고, 제안 승인·거절 중 `/ai-actions/` 요청이 0건임을 검증한다.
