# AI 채팅 프록시 계약 안내

이 문서의 과거 비스트리밍 접수·polling·HTTP callback 계약은 ADR-0005에 의해
폐기됐다. 현재 제품 계약은 다음 문서를 따른다.

- `ai-docs/contracts/sse-v2.md`
- `ai-docs/contracts/http-api.md`
- `ai-docs/contracts/ai-api.md`
- `ai-docs/decisions/0005-remove-non-streaming-ai-transport.md`

Browser→Django는 `/api/consulting/v2/consultations/{session_id}/messages/stream/`,
Django→AI는 `/api/ai/v2/chat/stream`만 사용한다. 같은 멱등 키의 완료 결과는 terminal
receipt에서 SSE로 재생하며 graph와 assistant message 저장을 반복하지 않는다.
