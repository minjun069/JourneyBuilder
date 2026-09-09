# AI 라우팅 테스트

이 디렉터리는 과거 P06에서 준비하고 `M2-L02`에서 production router에 결합하는 라우팅 평가 자산을 소유한다. 외부 AI provider나 LangChain 설치 여부 때문에 정책 검사가 막히지 않도록 실행 단계를 분리한다.

## P06 — 지금 실행하는 검사

다음 명령은 Python 표준 라이브러리만 사용한다.

```bash
python3 -m unittest discover -s ai-pjt/tests -p 'test_*.py'
```

`test_router_evaluation.py`는 다음을 확인한다.

- 계약에 동결된 23개 `step_id`와 Agent allowlist가 평가 fixture에 모두 존재한다.
- 여섯 역할의 대표 성공 경로와 모호·미지원·정책 거부·분류기 오류 fallback이 포함된다.
- `elicit`은 `financial-mindset`에서 명시적으로 정성정보 수집을 요청한 경우에만 성공 라벨이 된다.
- 모호한 요청은 단계와 관계없이 `fallback`으로 라벨링된다.
- 라우팅 정확도 기준은 `0.95`이며 60개 평가 입력 중 최소 57개를 맞혀야 한다.

P06에서는 production router를 import하거나 실제 모델을 호출하지 않는다.

## W1-L02 구현 후 M2-L02에서 production pure router 검사 활성화

`test_router_m2_l02.py`는 W1-L02가 production pure router를 구현할 때까지 명시적으로
skip된다. W1-L02 완료 결과를 검증하는 M2-L02 담당자는 다음 순서로 활성화한다.

1. `graph.router.select_route`를 task 하나만 처리하는 순수 함수로 구현한다.
2. allowlist와 classifier를 keyword 인자로 주입받게 한다.
3. 반환값에 `task_id`, 실행 대상과 `reason_code`를 포함한다.
4. `AI_M2_L02_ROUTER_TESTS=1`을 설정해 skip을 해제한다.
5. AI 의존성을 설치한 환경에서 아래 명령을 실행하고 M2-L02 증거로 남긴다.

```bash
AI_M2_L02_ROUTER_TESTS=1 PYTHONPATH=ai-pjt \
  python3 -m unittest discover -s ai-pjt/tests -p 'test_*.py'
```

production 함수의 예정된 호출 형태는 다음과 같다.

```python
select_route(
    task={
        "id": "task-1",
        "type": "qualitative_collection",
        "description": "은퇴 후 삶의 우선순위를 하나씩 물어봐 줘",
        "executor_kind": "agent",
    },
    step_id="financial-mindset",
    step_candidates=step_candidates,
    classifier=fake_classifier,
)
```

classifier는 `task`와 현재 단계의 `candidates`를 keyword 인자로 받고 `status`와 `agent`를 반환한다. `status`는 `matched`, `ambiguous`, `unsupported` 중 하나다. `M2-L02`의 LLM adapter는 모델 출력을 이 작은 내부 계약으로 정규화해야 한다. 도구 실행 task는 구체적인 handler 이름 대신 capability를 사용하며 미등록 capability는 `capability_unavailable`로 닫는다.

## P06 현재 선택 근거 코드

`reason_code`는 사용자에게 보여주는 오류 문구가 아니라 라우팅 안전 경계와 운영 지표를 설명하는 내부 코드다.

| 코드 | 의미 | 최종 Agent |
|---|---|---|
| `intent_match` | 분류된 Agent가 현재 단계 allowlist에 있음 | 분류된 Agent |
| `ambiguous_intent` | 의도를 하나로 결정할 수 없음 | `fallback` |
| `unsupported_intent` | 서비스가 수행하지 않는 의도임 | `fallback` |
| `plan_invalid` | 미지원 step, allowlist 불일치, 분류기 오류로 안전한 계획을 시작할 수 없음 | `fallback` |

`elicit`은 모호성을 처리하는 Agent가 아니다. `financial-mindset` 단계에서 목표·가치관·걱정 같은 정성정보를 한 항목씩 수집해 달라는 명시적 의도가 `matched`로 분류됐을 때만 선택한다.

F08-T03에서 다음 계약으로 코드와 fixture를 전환했다.

- `ambiguous_intent`와 `unsupported_intent`만 라우터에서 직접 `fallback`으로 보낸다.
- `unsupported_step`, `agent_not_allowed`, `classifier_error`는 실행할 수 없는 계획을 뜻하는
  외부 사유 `plan_invalid`로 정규화한 뒤 신뢰된 Fallback 경로로 보낸다.
- `capability_unavailable`은 Fallback 선택 사유가 아니다. Agent 실행 중 발생해 해당 Agent가
  설명할 수 있으면 Agent가 한계와 다음 행동을 안내하고, 실행 전 점검에서 판정되면
  Aggregator가 결정론적 문구로 안내한다.
- `dependency_failed`, `artifact_invalid`, `binding_resolution_failed`도 Aggregator가
  결정론적으로 안내한다. 일부 task가 성공했다면 검증된 결과만 집계하고 Fallback을 호출하지
  않는다.
- 모든 task가 실패해 전달할 검증된 결과가 없을 때만 `all_tasks_failed`로 Fallback을 한 번
  호출한다.

라우터 평가는 세 Fallback 분기와 `plan_invalid` 정규화를 확인하고, Executor 평가는 부분 성공과
`all_tasks_failed` 호출 횟수를 별도로 확인한다.

## F03 의존 작업 계획·실행 검사

- `test_planner.py`: 최대 3개 task, 앞선 task만 참조하는 DAG, 명시적 binding과 runtime 라우팅을 검사한다.
- `test_task_contracts.py`: `completed`·`failed`·`blocked` 결과와 typed artifact·binding 계약을 검사한다.
- `test_executor.py`: 선행 결과 주입, 선행 실패 시 후속 차단, 독립 task 지속, 변경안 값 일치와 충돌 집계를 검사한다.
- `test_graph_execution.py`: 실제 LangGraph에서 planner→순차 executor→공통 validator 연결을 검사한다.
- `test_agent_bindings.py`: `qa`와 `input_assist`가 검증된 binding을 재조회·재생성하지 않는 경계를 검사한다.

마지막 두 파일은 LangGraph·LangChain이 없는 호스트에서는 skip된다. AI runtime 이미지에서는 다음처럼 실행한다.

```bash
python -m unittest discover -s tests -p 'test_graph_execution.py' -v
python -m unittest discover -s tests -p 'test_agent_bindings.py' -v
```

## 모델·의존성 평가

실제 모델, prompt, LangChain adapter를 사용하는 정확도 평가는 deterministic M2-L02
계약 검사가 통과한 뒤 F03 후속 평가에서 실행한다. 같은
`fixtures/router-evaluation-v1.json`을 사용하되 모델·prompt ID와 버전, 실행 시각,
예측 결과를 함께 남긴다. 기본 unit test에서는 네트워크나 credential을 요구하지 않는다.
