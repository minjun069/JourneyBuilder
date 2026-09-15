# AI SSE 계약 안내

현재 SSE 단일 전송 계약의 원문은 `ai-docs/contracts/sse-v2.md`다.

- Browser→Django: `POST /api/consulting/v2/consultations/{session_id}/messages/stream/`
- Django→AI: `POST /api/ai/v2/chat/stream`
- 상태 머신: `start -> done | error`; `delta`는 protocol 위반
- 완료 재접속: terminal receipt 기반 재생, graph 재실행 없음
- 제공하지 않음: 비스트리밍 접수, 결과 polling, HTTP callback, 자동 transport 전환
