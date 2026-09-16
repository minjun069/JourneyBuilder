# 몬테카를로 샘플 실행·인수인계 가이드

상태: **브랜치 5번 작업 완료 — 합성 입력의 실제 몬테카를로 CLI·출력 검증·사용 문서.**

[`monte_carlo_samples.py`](../monte_carlo_samples.py)는 기존 합성 고객 3종을
실제 `calculate_monte_carlo`에 전달하고 계산된 결과를 출력한다.
확률이나 잔액을 고정된 예시 값으로 대신하지 않는다. 기존 `retirement_simulation.samples`는
고정 현금흐름 전용으로 유지하며 입력·가정·계산·상세 행은 바꾸지 않았다.
고정 CLI의 몬테카를로 미구현 문구만 새 실행기를 안내하도록 갱신했다.

이 실행기는 **내장 합성 사례 시연용**이다. 고객 파일·DB·폼 입력·외부 API·LLM을
읽지 않으며 실제 고객 보고서, BE 준비율, Agent 도구 또는 What-if 비교를 만들지 않는다.

## 1. Miniforge에서 실행

아래 명령은 `backend-pjt/packages/` 디렉터리에서 실행한다. 저장소 루트라면 먼저 `cd backend-pjt/packages`로
이동한다. 기존 `retirewise-ai` 환경에 추가 패키지를 설치할 필요가 없다.

```bash
# 기본: 3개 합성 사례, 사례마다 1,000개 경로, 시드 42
conda run -n retirewise-ai python -S -B -m retirement_simulation.monte_carlo_samples

# 한 사례의 나이별 분위수·최초 고갈 개수까지 확인
conda run -n retirewise-ai python -S -B -m retirement_simulation.monte_carlo_samples --case working_with_phased_expenses --details

# 경로 수와 시드를 명시한 반복 실행
conda run -n retirewise-ai python -S -B -m retirement_simulation.monte_carlo_samples --num-paths 200 --seed 42

# 도움말
conda run -n retirewise-ai python -S -B -m retirement_simulation.monte_carlo_samples --help
```

환경이 이미 활성화된 셸에서는 `python -m retirement_simulation.monte_carlo_samples`로도
실행할 수 있다. `-S`는 site-packages 없이 실행되는지 확인하는 옵션이며 필수는 아니다.

### CLI 옵션

| 옵션 | 기본값 | 의미 |
|---|---|---|
| `--case NAME` | 세 사례 모두 | 아래 표의 내장 사례 하나만 계산 |
| `--num-paths N` | 1,000 | 사례별 경로 수. 정수 1~100,000 |
| `--seed SEED` | 42 | 각 사례를 시작할 때 사용하는 로컬 RNG 시드. 0 이상 정수 |
| `--details` | 꺼짐 | 모든 나이의 잔액 P10/P50/P90과 최초 고갈 개수를 추가 출력 |
| `--help` | — | 도움말만 출력하고 계산하지 않음 |

금액·기간·평균·변동성·물가를 임의로 입력하는 옵션, 파일 입출력, JSON·HTTP 규격은
추가하지 않았다. 잘못된 옵션·범위·사례명은 계산 전에 거부하며 축약 옵션은 받지 않는다.
경로 수 상한은 메모리·실행 시간 보장이 아니다. 먼저 기본값이나 작은 경로 수로 확인한다.

## 2. 합성 입력과 가정

| 사례 | 기간 | 포함하는 조건 |
|---|---|---|
| `working_with_phased_expenses` | 40~90세, 51년 | 은퇴 전 적립, 60세 은퇴, 생활비 구간 변경, 65세 연금 개시 |
| `already_retired_without_pension` | 68~90세, 23년 | 이미 은퇴, 적립·연금 없음 |
| `pension_start_gap` | 59~90세, 32년 | 60세 은퇴, 65세 연금 개시까지의 공백, 79세 연금 종료 |

금액·연령 구간은 [기존 합성 입력](../samples.py)을 재사용한다.
`build_monte_carlo_cases(num_paths=..., seed=...)`는 새 합성 사례를 만들어
가정만 아래 값으로 교체하고 원본을 수정하지 않는다. 사례와 모델은 불변 구조다.

```text
annual_return_mean       = 0.05
annual_return_volatility = 0.10
annual_inflation_rate    = 0.02
```

이 값들은 단순 연수익률의 산술 평균·표준편차와 연물가율을 소수 비율로 지정한
**시연용 가정**이다. 시장 데이터에서 추정한 값·추천 수익률·실제 서비스 기본값이 아니다.
기존 `SimulationAssumptions`의 세 시장 가정은 여전히 필수 입력이고, 고정 CLI는
여전히 평균·변동성·물가가 모두 0이다.

각 사례는 같은 시드로 독립 실행한다. 세 사례의 계산 기간이 서로 다르므로
**같은 시드라고 공통 시장 경로가 정렬된 What-if 비교는 아니다.** 재현하려면
입력·경로 수·시드뿐 아니라 코드·Python 버전·실행 환경도 유지해야 한다.

## 3. 출력과 읽는 방법

기본 출력에는 사례명, 현재·은퇴·종료 나이, 연도 수와 다음 값이 포함된다.

| 항목 | 의미 |
|---|---|
| `assumptions` | 사용한 평균·변동성·물가. 연간 소수 비율 |
| `execution` | 경로 수, 설정/실사용 시드, 경로 출처, 모델·수익률 분포·RNG·Python 정보 |
| `success_probability`, `success_count` | 전 기간의 연말 잔액이 한 번도 음수가 아닌 경로의 비율과 `개수/N` |
| `depletion_probability`, `depletion_count` | 한 번 이상 음수인 경로의 비율과 `개수/N` |
| `never_depleted_count` | 성공 경로 개수와 동일 |
| `final_balance` | 전체 경로의 최종 명목 잔액 P10/P50/P90 |
| `final_shortfall` | 경로별 최종 미충당 부족액을 먼저 계산한 뒤 집계한 P10/P50/P90 |
| `first_depletion_counts` | `나이:개수`의 최초 고갈 히스토그램. 요약에는 0개인 나이를 생략하고, 고갈이 없으면 `none` |

금액은 명목 원 단위의 소수점 2자리, 확률은 소수 6자리와 퍼센트 3자리로 **표시만**
반올림한다. 개수와 분모도 함께 보여주며, 반올림한 금액으로 고갈 여부를 다시 판정하지 않는다.
미세한 음수가 `-0.00`으로 보여도 고갈 개수는 남는다. 중간에 고갈됐다가 회복해
최종 부족액이 0이어도 성공 경로로 바꾸지 않는다.

`--details`는 아래 열을 추가한다. 전체 세 사례를 선택하면 총 **106개 나이 행**이며,
0개인 최초 고갈 구간도 생략하지 않는다. 개별 경로의 상세 원장이나 수익률은 출력하지 않는다.

```text
age | balance_p10 | balance_p50 | balance_p90 | first_depletion_count
```

P10/P50/P90은 성공·고갈·회복을 포함한 모든 표본의 시점별 요약이다. 특정 생애 경로
하나나 성공확률 신뢰구간이 아니고, 잔액 분위수의 부호만 바꿔 부족액 분위수를 만들 수 없다.
0%·100%도 현실의 확정·보장이 아니며 **기존 BE 준비율과 다른 지표**다.
자세한 판정·집계 정의는 [엔진 가이드](monte-carlo-engine.md)를 참고한다.

### 실제 실행 예시

2026-09-08, CPython 3.13.15에서 기본 가정·`N=1000`·`seed=42`로 실제 실행한
요약은 다음과 같다. 다른 설정이나 환경에서도 이 숫자를 출력하도록 고정한 것은 아니다.

| 합성 사례 | 성공 개수 / N | 성공확률 | 최종 잔액 P50(원) | 최종 부족액 P50(원) |
|---|---:|---:|---:|---:|
| `working_with_phased_expenses` | 279 / 1,000 | 27.900% | -401,057,064.89 | 401,057,064.89 |
| `already_retired_without_pension` | 0 / 1,000 | 0.000% | -463,702,049.99 | 463,702,049.99 |
| `pension_start_gap` | 0 / 1,000 | 0.000% | -764,394,828.99 | 764,394,828.99 |

첫 사례의 출력 일부는 다음과 같다.

```text
success_probability=0.279000 (27.900%), success_count=279/1000
depletion_probability=0.721000 (72.100%), depletion_count=721/1000, never_depleted_count=279
final_balance (nominal KRW): p10=-713,726,097.59, p50=-401,057,064.89, p90=780,840,522.50
final_shortfall (nominal KRW): p10=0.00, p50=401,057,064.89, p90=713,726,097.59
```

이는 내장 합성 조건에서 나온 계산 결과이지 실제 고객 분석이나 시장 전망이 아니다.
`N=200`·동일 시드에서는 첫 사례의 성공 개수가 55/200으로 달라진다. 경로 수를
바꿔도 결과가 같아야 한다고 가정하지 않는다.

## 4. 오류와 종료 코드

| 종료 코드 | 의미 |
|---|---|
| 0 | 선택한 모든 샘플의 계산·출력 완료 또는 도움말 |
| 2 | 잘못된 CLI 옵션·타입·범위·사례명. stderr로 사용법·오류 안내 |
| 1 | 내장 모델 검증 또는 수치 계산 실패. stderr로 단계·필드·가능한 경로/나이 안내 |

선택한 사례들의 계산이 전부 성공한 뒤 stdout에 출력한다. 두 번째 사례가 실패해도
첫 사례의 결과를 먼저 출력하지 않는다. 수치 오류를 고갈 경로로 세거나 경로를
제외해 분모를 줄이지 않는다. 예상된 모델·계산 오류에는 원본 금액이나 traceback을
출력하지 않는다. 이것이 향후 서비스의 요청·로컬 변수 로깅 정책까지 확정한 것은 아니다.

## 5. 검증과 브랜치 완료 범위

2026-09-08, Miniforge `retirewise-ai`의 Python 3.13.15에서 독립 모듈 테스트
**181개 통과: 기존 162개 + 신규 CLI 테스트 19개**를 확인했다.
신규 검사는 [`test_monte_carlo_samples.py`](../tests/test_monte_carlo_samples.py)에 있다.
일반·`-S` 실행뿐 아니라 181개 검사를 역순으로도 실행했다. 기본 실행, 선택 사례의
상세 51행, 200개 경로 실행, 도움말과 변경 문서의 로컬 파일 링크 67개도 확인했다.
추가 실행 점검은 테스트 메서드 개수에 더하지 않는다.

| 그룹 | 개수 | 주요 내용 |
|---|---:|---|
| `MonteCarloSampleInputTests` | 4 | 기존 프로필 재사용·고정 가정 불변, 시연 가정, 새 불변 입력·옵션·모델 검증 |
| `MonteCarloSampleOutputTests` | 9 | 실제 엔진 호출·결과 대조, 가정·메타데이터·한계, 사례 선택, 106행, 재현성, 손계산 회복·미세 음수·미고갈 표시 |
| `MonteCarloSampleCliTests` | 6 | 잘못된 인자·상한 전달, 뒤쪽 사례 및 모델 오류의 무출력, 도움말, site-packages 없는 별도 프로세스의 종료 코드 |

CLI의 최대 경로 수 전달 테스트는 계산을 가로채 상한 옵션이 전달되는지만 확인한다.
100,000개×장기 사례의 실제 부하를 실행한 검사가 아니다. 계산 엔진의 독립 검산·
경계·상한 검증은 [4번 검증 기록](monte-carlo-validation.md)에 별도로 정리한다.

저장소 루트에서 재검증한다.

```bash
conda run -n retirewise-ai python -B -m unittest discover -s backend-pjt/packages/retirement_simulation/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s backend-pjt/packages/retirement_simulation/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s backend-pjt/packages/retirement_simulation/tests -p test_monte_carlo_samples.py -v
```

이 브랜치의 1~5번인 계산 규칙·수익률 생성·집계·경계 검증·샘플 실행은 완료했다.
계산 엔진·공개 모델·BE·FE·Agent·컨텍스트·RAG·공용 의존성·실행 설정은 이번
샘플 작업에서 변경하지 않았다. 당시 후속 기능이었던 What-if의 시나리오 생성·공통 시장
경로 비교·전용 CLI도 이후 별도 브랜치에서 구현했다. [What-if 샘플 가이드](what-if-samples.md)를
참고한다. 기존 CLI의 계산은 유지하며 미구현 안내만 새 명령으로 갱신했다.
실제 입력 adapter·서비스 연결·대량 성능·시장 적합성은 남아 있다.
현재 브랜치의 푸시·MR 생성·병합은 사용자가 별도로 요청할 때만 수행한다.
