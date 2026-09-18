# AI orchestration 테스트

이 디렉터리는 Planner → 결정론적 route enrichment →
`evaluate_route_plan` → Executor → Aggregator 경계를 검증한다. Router는 더
이상 모델을 호출하지 않고 검증 권한도 가지지 않는다.

## 핵심 계약

- Planner가 `executable`, `ambiguous`, `unsupported`를 판단한다.
- `graph.router` 는 task type으로 Agent·tool 실행 정보만 제안한다.
- `evaluate_route_plan`만 구조 검증과 route 검증을 순서대로 수행한다.
- Planner 결과와 enrichment는 모두 신뢰하지 않으며, Executor는 검증된
  canonical plan만 받는다.
- 최대 3개 task, 앞선 task만 참조하는 DAG, 명시적 binding 계약을
  강제한다.
- 계획 검증 실패는 검증 이유를 제공해 한 번 재생성한 후 상세한
  `plan_invalid` 응답으로 종료한다.
- 모든 task가 실패해도 가상 Fallback task를 만들지 않고 Aggregator의
  결정론적 종료 정책을 사용한다.
- 복수의 검증된 성공 응답만 LLM composer로 자연스럽게 합치며, 수치·
  출처 변조나 모델 실패 시 결정론적 join으로 복귀한다.
- Error Review의 외부 계약은 `request_mode=error_review`만 받고 저장
  동작으로만 시작한다.

## 주요 검사

- `test_routing_policy.py`: task type→실행자 결정론적 enrichment와 단계별
  task menu
- `guardrails/tests/test_plan_policy.py`: 단일 검증 진입점, 구조 우선 검증,
  enrichment 변조 차단
- `test_planner.py`: bounded context, DAG·binding, 1회 계획 수복, 설명 질문
  회복
- `test_executor.py`: 의존성 차단, artifact 검증, 부분 성공, `all_tasks_failed`
  Aggregator 종료 정책
- `test_graph_execution.py`: 실제 LangGraph에서 계획·검증·실행·집계 연결
- `test_agent_bindings.py`: QA와 Input Assist가 검증된 binding을 재조회·
  재생성하지 않는 경계

AI runtime 이미지에서 다음을 실행한다.

```bash
PYTHONPATH=. python -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=. python -m unittest discover -s guardrails/tests -p 'test_*.py' -v
PYTHONPATH=. python -m unittest discover -s tools/tests -p 'test_*.py' -v
```

F07-T04·F08-T04 평가는 `test_agent_features.py`와
`evals/tests/test_agent_baseline.py`에서 QA binding, Error Review confidence, chart
payload, Elicit context, Aggregator 실패 응답 계약을 결정론적으로 검증한다.
