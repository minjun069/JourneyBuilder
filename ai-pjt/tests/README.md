# AI 라우팅 테스트

이 디렉터리는 P06에서 준비하고 I03에서 production router에 결합하는 라우팅 평가 자산을 소유한다. 외부 AI provider나 LangChain 설치 여부 때문에 정책 검사가 막히지 않도록 실행 단계를 분리한다.

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

## I03 — production pure router 계약 활성화

`test_router_i03.py`는 I03 전환 전까지 명시적으로 skip된다. I03 담당자는 다음 순서로 활성화한다.

1. `graph.router.select_route`를 외부 상태를 사용하지 않는 함수로 구현한다.
2. allowlist와 classifier를 keyword 인자로 주입받게 한다.
3. 반환값에 `agent`와 `reason_code`를 포함한다.
4. `AI_I03_ROUTER_TESTS=1`을 설정해 skip을 해제한다.
5. AI 의존성을 설치한 환경에서 아래 명령을 실행한다.

```bash
AI_I03_ROUTER_TESTS=1 PYTHONPATH=ai-pjt \
  python3 -m unittest discover -s ai-pjt/tests -p 'test_*.py'
```

production 함수의 예정된 호출 형태는 다음과 같다.

```python
select_route(
    step_id="financial-mindset",
    message="은퇴 후 삶의 우선순위를 하나씩 물어봐 줘",
    step_candidates=step_candidates,
    classifier=fake_classifier,
)
```

classifier는 `message`와 현재 단계의 `candidates`를 keyword 인자로 받고 `status`와 `agent`를 반환한다. `status`는 `matched`, `ambiguous`, `unsupported` 중 하나다. I03의 LLM adapter는 모델 출력을 이 작은 내부 계약으로 정규화해야 한다.

## 선택 근거 코드

`reason_code`는 사용자에게 보여주는 오류 문구가 아니라 라우팅 안전 경계와 운영 지표를 설명하는 내부 코드다.

| 코드 | 의미 | 최종 Agent |
|---|---|---|
| `intent_match` | 분류된 Agent가 현재 단계 allowlist에 있음 | 분류된 Agent |
| `ambiguous_intent` | 의도를 하나로 결정할 수 없음 | `fallback` |
| `unsupported_intent` | 서비스가 수행하지 않는 의도임 | `fallback` |
| `unsupported_step` | `step_id`가 registry에 없음 | `fallback` |
| `agent_not_allowed` | 분류 결과가 현재 단계 allowlist 밖임 | `fallback` |
| `classifier_error` | 분류기가 예외 또는 잘못된 결과를 반환함 | `fallback` |

`elicit`은 모호성을 처리하는 Agent가 아니다. `financial-mindset` 단계에서 목표·가치관·걱정 같은 정성정보를 한 항목씩 수집해 달라는 명시적 의도가 `matched`로 분류됐을 때만 선택한다.

## 모델·의존성 평가

실제 모델, prompt, LangChain adapter를 사용하는 정확도 평가는 deterministic I03 계약 검사가 통과한 뒤 별도 실행한다. 같은 `fixtures/router-evaluation-v1.json`을 사용하되 모델·prompt ID와 버전, 실행 시각, 예측 결과를 함께 남긴다. 기본 unit test에서는 네트워크나 credential을 요구하지 않는다.
