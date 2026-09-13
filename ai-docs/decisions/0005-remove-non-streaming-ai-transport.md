# ADR-0005: 비스트리밍 AI 전송 경로 제거

- 상태: Accepted
- 결정일: 2026-09-13
- 관련 작업: F04-T04 / W2-L03
- 대체 대상: ADR-0004의 callback + polling 호환 유지 결정

## 배경

일반 상담, Elicit, Error Review가 모두 v2 SSE를 사용한 뒤 비스트리밍 접수,
HTTP callback, polling API를 호출하는 제품 기능과 확인된 외부 소비자는 없다. Error
Review도 비차단 자문이라 브라우저 연결과 독립적인 완료 보장이 필요하지 않다. 호환
경로를 유지하면 같은 요청에 두 수명주기, 두 오류 처리, 불필요한 callback credential과
주기적 조회 계약이 남는다.

## 결정

1. Browser→Django와 Django→AI의 대화형 AI 전송은 각각 v2 SSE endpoint 하나만
   사용한다.
2. Django 비스트리밍 메시지 POST, AI 결과 polling, AI 비스트리밍 `/v2/chat`,
   AI→Django HTTP callback endpoint를 제거한다. 구형 `/chat[/stream]`과 callback
   endpoint도 더 이상 호환 응답을 제공하지 않는다.
3. `Idempotency-Key`와 `request_id` 결합은 그대로 유지한다. 같은 요청의 완료 결과는
   graph를 다시 실행하지 않고 terminal receipt에서 SSE `start` 뒤 `done` 또는
   `error`로 재생한다.
4. PostgreSQL terminal receipt는 HTTP callback 수신 기록이 아니라 SSE terminal의
   소유권 결합, exact-once assistant message 생성, Redis 유실 복구를 위한 내부 저장
   경계로 유지한다.
5. 브라우저 연결과 독립적인 완료·재시도·DLQ가 필요한 장기 작업은 대화형 호환 경로가
   아니라 RabbitMQ 영속 작업 계약으로 분리한다.

## 결과

- 대화형 요청의 상태 머신은 `start -> delta* -> done | error` 하나다.
- 연결 종료 전 terminal이 확정되면 실패 receipt로 종결하고, 재접속은 같은 멱등 결과를
  재생한다.
- polling용 `processing/expired` 외부 상태와 callback timeout/backoff/secret 설정은
  제거된다.
- receipt의 기존 DB 필드명 `callback_digest`는 migration 호환을 위해 유지하지만 의미는
  terminal payload digest다.

## 완료 기준

- 제거 대상 route가 모두 404/미해결이고 production 코드에서 호출되지 않는다.
- SSE 정상·실패·취소·동일 키 replay와 Redis 유실 뒤 receipt 복구가 통과한다.
- 공용 lifecycle/SSE fixture가 SSE 단일 전송 정책을 명시한다.

