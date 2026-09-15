# AI 서비스 전송 설계 안내

AI 서비스의 대화형 외부 경로는 `POST /api/ai/v2/chat/stream` 하나다. bearer 인증 후
`start -> done | error`를 반환하며 `delta`, 비스트리밍 `/v2/chat`, 구형
`/chat[/stream]`, Backend HTTP callback을 제공하지 않는다.

요청·terminal schema, timeout과 멱등성은 `ai-docs/contracts/ai-api.md`와
`ai-docs/contracts/sse-v2.md`를 따른다. 장기 작업은 대화형 fallback이 아니라 별도
RabbitMQ 영속 작업으로 설계한다.
