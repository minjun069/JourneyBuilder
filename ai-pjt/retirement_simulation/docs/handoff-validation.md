# MVP 확정 가정 검증과 전달용 샘플

2026-09-10. Miniforge `retirewise-ai`, Linux/WSL CPython 3.13.15.
계산 엔진은 MR !52 병합의 `d39677b`와 동일하다. [확정 정책](simulation-policy.md)을
명시 입력한 합성 검증이며 실제 고객·시장 보정·BE 이미지·HTTP/동시 부하 시험이 아니다.

## 1. 제공 자료

- [정책 JSON](simulation-policy.json): 다섯 함수 입력값과 별도 정책 버전/범위/근거.
- [전체 입력·결과 JSON](handoff-examples.json): 기본 MC·세 조건 조합 What-if·연금 없음 미확인 오류.
  `python_arguments`는 Python 함수 인자이고 `module_output`은 기존 전체 JSON 계약 그대로다.
  `cli_exit_code`는 로컬 도구 종료 코드이지 HTTP 상태가 아니다. 최상위 묶음도 HTTP 계약이 아니다.
- [원시 진단 결과](handoff-validation-results.json): 30개 시드/경로 수 실행 + 7개 가정 민감도 실행.
- [샘플 도구](../tests/handoff_samples.py): 위 정책과 기존 합성 입력만 읽는 소스/sdist 전용 실행기.
- [자동 검사](../tests/test_handoff_policy.py): 조합·단위·재현·불변·오류·경계·문서 샘플 정합성.

합성 JSON에는 가짜 고객의 금액/나이가 들어 있다. 실제 고객 데이터로 바꾸어 커밋하거나
일반 로그에 남기지 않는다. 원본 DB/보고서/대화 상태를 읽거나 수정하는 동작은 없다.
설치용 wheel에는 docs/tests가 없으므로 이 도구를 운영 BE에서 import하지 않는다.

## 2. 재실행

저장소의 `ai-pjt/`에서 Miniforge 환경을 사용한다. stdout JSON을 확인하며 자동 파일 저장은 없다.

```bash
conda run --no-capture-output -n retirewise-ai python -S -B -m retirement_simulation.tests.handoff_samples --case baseline --pretty
conda run --no-capture-output -n retirewise-ai python -S -B -m retirement_simulation.tests.handoff_samples --case combined --request --pretty
conda run --no-capture-output -n retirewise-ai python -S -B -m retirement_simulation.tests.handoff_samples --case combined --pretty
conda run --no-capture-output -n retirewise-ai python -S -B -m retirement_simulation.tests.handoff_samples --case unconfirmed_pension --pretty
```

오류 사례의 종료 코드 1은 의도한 실패다. 성공 0, 잘못된 CLI 인자 2와 구분한다.
`--request`는 계산하지 않고 입력만 보여준다. 자유 파일 입력·임의 경로 수·시장 가정 옵션은 없다.
기존 `analysis_samples`의 5% 시연 기본값을 바꾸지 않았다. 이번 4% 정책 샘플은 위 전용 도구다.

기준은 기존 재직 fixture(40세·은퇴 60세·종료 90세)이며 변경은 다음과 같다.

| 이름 | 변경 후 월 총 적립 | 은퇴 나이 | 명시적 전체 생활비 일정(현재 가치 원/월) |
|---|---|---|---|
| `save_more` | 1,000,000 | 유지 | 유지 |
| `retire_earlier` | 유지 | 58 | 58~74세 2,000,000 / 75~90세 1,500,000 |
| `lower_expenses` | 유지 | 유지 | 60~74세 1,800,000 / 75~90세 1,300,000 |
| `save_and_retire` | 1,000,000 | 58 | 조기 은퇴 일정 |
| `save_and_spend` | 1,000,000 | 유지 | 생활비 감소 일정 |
| `retire_and_spend` | 유지 | 58 | 58~74세 1,800,000 / 75~90세 1,300,000 |
| `combined` | 1,000,000 | 58 | 58~74세 1,800,000 / 75~90세 1,300,000 |

매번 기준+변경 **1개**만 실행한다. 7개 변경을 한 서비스 요청에 보내는 예시가 아니다.
조기 은퇴의 추가 연도 생활비는 합성 사례에서 의도적으로 지정한 값이다. 엔진이나 실제 UI가
사용자 확인 없이 자동 보충하라는 뜻이 아니다. 연금 수령 시작은 65세 그대로다.
`unchanged`, `retired`, `pension_gap`, `missing_expenses`도 선택 가능하다.

## 3. 자동 검증 범위

저장소 루트에서 실행한다.

```bash
conda run --no-capture-output -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -p test_handoff_policy.py -v
conda run --no-capture-output -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
```

- 가정 다섯 필드만 전달하고 누락/정책 metadata를 암묵 보정하지 않는다.
- 세 조건의 7조합 모두 단일 비교안, 완전한 생활비 일정, 원본/전역 RNG 불변.
- 기준 MC와 비교 배치의 기준 요약 일치(실행 출처만 generated/provided로 다름).
- 한 번의 공통 경로 생성과 두 번의 기준/변경 계산, 무변경 차이 0, 확률×100/차이 pp 검증.
- 미반영 범위/연금 없음/기간 밖 연금 안내 보존, 누락·0·null·빈 생활비·조기 은퇴 coverage 구분.
- 18~120세 103년 전체 기간 검증. 이 경계 검사는 5경로이며 BE 장기간 성능 시험이 아니다.
- 변동성 0·수익률 4%·물가 2%의 별도 3년 손계산 잔액 92,000 / 83,440 / 74,292.8원 확인.
- CLI 및 전체 입력/출력 artifact가 공개 함수 결과와 일치. Python 표시 metadata만 환경에 맞게 비교.
- 예상 밖 버그/자원 부족을 성공확률 0이나 알려진 입력 오류로 위장하지 않는다.

## 4. 여러 시드와 가정 민감도

`ai-pjt/`에서 다음 고정 행렬을 재실행한다. 37개 호출을 순차 수행하며 각 호출은 기준+변경 1개다.

```bash
conda run --no-capture-output -n retirewise-ai python -S -B -m retirement_simulation.tests.handoff_samples --study --pretty
```

경로 수 1,000/5,000 × 시드 7/21/42/73/109 × 재직 조합/은퇴 후/연금 공백 3개 = 30회.
재직 조합은 51년, 은퇴 후는 6년, 연금 공백은 18년이다. 아래 범위는 **관측한 다섯 시드의 최솟값~최댓값**이며
신뢰구간·보장 범위가 아니다. 경로 수를 늘려도 정책/모형 오차가 사라지지는 않는다.

| 합성 사례 | 경로 수 | 기준 성공확률 범위 | 변경 성공확률 범위 | 변경 차이 범위 |
|---|---|---|---|---|
| 재직·세 조건 조합 | 1,000 | 32.9~36.6% | 44.8~48.6% | +11.5~14.4%p |
| 재직·세 조건 조합 | 5,000 | 32.46~33.50% | 45.58~46.84% | +12.66~14.28%p |
| 은퇴 후 생활비 감소 | 1,000 | 46.1~49.6% | 92.0~93.0% | +42.5~46.6%p |
| 은퇴 후 생활비 감소 | 5,000 | 47.10~49.18% | 91.64~92.60% | +43.42~44.62%p |
| 연금 공백·은퇴 연기 | 1,000 | 0% | 0~0.2% | 0~+0.2%p |
| 연금 공백·은퇴 연기 | 5,000 | 0% | 0.02~0.08% | +0.02~0.08%p |

표본 0%는 모든 현실에서 불가능하다는 뜻이 아니다. 미세한 차이를 유의미한 개선으로
단정하거나 성공확률의 소수 자릿수를 정확도의 보장으로 해석하지 않는다.
1,000경로 MVP 정책을 임의로 바꾸지 않았고, 최종 서비스의 정밀도 요구/실제 BE 성능에 따라
추가 경로가 필요하면 정책 버전을 올려 검증한다. 시드를 결과에 맞춰 선택하지 않는다.

추가 7회는 재직 조합·1,000경로·시드 42에서 **한 가정씩만** 변경했다.

| 진단 가정 | 기준 확률 | 변경 확률 | 차이 |
|---|---|---|---|
| 정책 4% / 10% / 2% | 34.2% | 48.6% | +14.4%p |
| 평균만 2% | 3.5% | 9.0% | +5.5%p |
| 평균만 6% | 81.4% | 88.8% | +7.4%p |
| 변동성만 5% | 35.3% | 62.0% | +26.7%p |
| 변동성만 15% | 29.7% | 38.4% | +8.7%p |
| 물가만 1% | 74.7% | 85.4% | +10.7%p |
| 물가만 3% | 8.3% | 15.9% | +7.6%p |

모든 입력에서 같은 방향/크기의 효과를 보장하지 않는다. 민감도 값은 추천 상품·추가 UI 프리셋이 아니다.
수익률·물가 선택에 결과가 크게 좌우된다는 한계를 확인했으며 **시장 예측력이 검증됐다는 결론은 내리지 않는다.**
원시 `elapsed_seconds`는 로컬 계산/JSON 검사 시간이며 정식 벤치마크·HTTP SLA가 아니다.

## 5. 남은 검증

실제 BE 이미지 설치, 같은 시점 snapshot/없음 확인, HTTP 인증/소유권·기준 변경,
동시성/timeout/취소·긴 기간·FE 단위·안내·원본 보존 E2E는 연결 담당과 별도로 검증한다.
기존 [설치 검증](installation-validation.md)과 이 소스 검증은 구분한다.
