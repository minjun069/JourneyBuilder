# Input Assist 오류와 검증 경계

현재 파이프라인은 `extract → 코드 검증 → 필요한 경우 review → respond`다.
기본 모델 호출은 추출과 안내의 2회이며, 의미 확인이 필요한 항목이 있을 때만 일괄 검토 1회를 추가한다.
과거 classify·identify·resolve·merge와 항목별 재시도 구조는 사용하지 않는다.

## 현재 오류 처리

| 단계 | 오류 | 처리 |
|---|---|---|
| extract | JSON 파싱 실패·출력 schema 불일치 | provider JSON mode의 응답을 Pydantic으로 검사하고 실패를 성공 제안으로 처리하지 않음 |
| extract | 알 수 없는 필드 참조키·행 ID·operation | 현재 요청의 필드·행 매핑과 canonical 계약으로 거부 |
| 코드 검증 | 잘못된 자료형·범위·선택지·단위·중첩 대상 | 코드가 검증하며 모델의 긍정 판단으로 우회하지 않음 |
| review | 근거 없는 값·모호한 의미·출처와 다른 값 | 필요한 항목만 한 번 검토하고 해결되지 않은 항목은 변경안에서 제외 |
| respond | 처리 결과와 다른 안내·자동 적용 또는 저장 주장 | 검증 결과와 제안 상태를 기준으로 안내하며 최종 출력 경계도 적용 |

추출 결과의 `value`는 숫자·문자열·객체·배열 등 직접 JSON 값이다. JSON 문자열 안에 값을 다시 인코딩하지 않는다.
모델 출력은 폼을 직접 바꾸지 않는다. 검증된 제안도 Frontend에서 사용자가 개별 승인한 뒤 draft에 적용되고,
DB 저장은 별도의 폼 저장 검증을 거친다. confidence 점수는 출력·표시·승인 기준에 없다.

## 확인 방법

`ai-pjt` 디렉터리에서 다음 mock 회귀를 실행한다.

```bash
python -m unittest tests.test_input_assist_refactor tests.test_agent_features -v
```

이 검사는 코드 계약과 호출 예산의 회귀를 확인한다. 실제 provider 품질이나 운영 오류 빈도를 측정한 결과는 아니다.

- 현재 흐름과 모델 계약: [AI 명세](../../ai-docs/ai/specification.md)
- 실제 실행과 검증: [Input Assist 서비스](../agents/input_assist/service.py)
- 신규 요구 회귀: [Input Assist 테스트](../tests/test_input_assist_refactor.py)
