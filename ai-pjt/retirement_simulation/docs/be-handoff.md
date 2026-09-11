# 은퇴 시뮬레이션 BE 직접 호출 인수인계

작성일: 2026-09-09. 통합 분석 브랜치 6번의 **모듈 인수인계**다.
계산·매핑·통합 실행·결과/오류 JSON·합성 성능 측정·[통합 CLI](analysis-samples.md)를 구현했다.
BE/FE·DB·Agent·공통 의존성·배포 설정은 수정하지 않았다. 실제 API/화면 연결은 아직 미구현이다.
아래 담당 범위는 연결 작업을 나누기 위한 제안이며 담당자의 수락이나 변경 권한을 대신하지 않는다.

2026-09-10 인수인계 갱신: MR !52 병합 후 `master=d39677b` 기준.
**wheel/sdist 빌드와 격리 설치 검증은 완료**, 실제 BE 이미지 설치와 BE/FE 연결은 미구현이다.
[MVP 정책](simulation-policy.md)·[전체 합성 입력/출력](handoff-examples.json)·
[가정 검증](handoff-validation.md)을 준비했다. 이 자료는 아직 담당자에게 전달하거나 합의하지 않았다.
FE 담당은 [화면 연결 안내](fe-handoff.md), 기술 확인 항목은 [설치·호출 확인서](be-integration-checklist.md)를 읽는다.

## 먼저 전달할 요약

- 패키지: `retirewise-retirement-simulation==0.1.1`, import `retirement_simulation`.
- Python `>=3.13,<3.14`, 런타임 외부 의존성 없음. 로컬 검증 3.13.15, BE 이미지 3.13.14는 별도 검증 필요.
- `analyze_retirement()`에 저장 snapshot·가정 5개·변경안 최대 1개·실제 없음 확인 근거를 연결한다.
- 정책: 평균 4% / 변동성 10% / 물가 2% / 1,000경로 / seed 42. 서버가 명시적으로 전달한다.
- 변경 후 준비율 제외, 안내를 유지한 부분 분석 허용. 기존 준비율 원천은 기존 BE/FE 담당 확인 대상.
- Agent/채팅/LLM 경유·원본 저장·외부 금융 API는 필요 없다. 인증/기준 일관성/자원 보호는 BE가 구현한다.

패키지는 [설치 가이드](package-installation.md)대로 소스 커밋과 빌드 보고서·SHA-256을 함께 고정한다.
버전 이름이 같아도 README 메타데이터/빌드 시각 때문에 산출물 해시가 달라질 수 있다.
2026-09-10 전달 준비에서는 계산 Python 18개와 공개 인터페이스를 변경하지 않아 런타임 버전을 유지했다.
아래 2026-09-11 매핑 보완은 그 이후의 **소스 변경**이며 기존 `0.1.0` 설치 파일에 포함됐다고 보지 않는다.
정책 JSON과 docs/tests는 **sdist/저장소 자료**이며 wheel 설치 경로에서 읽을 수 없다.
BE는 [정책 JSON](simulation-policy.json)을 검토한 서버 설정으로 별도 배치하고 버전을 응답에 기록한다.
공통 재무 정책·Dockerfile·requirements를 모듈 담당이 임의 변경하지 않는다.

### 2026-09-11: 비모델링 금융자산 매핑 보완

`fix/ai-simulation-other-assets`, 시작 기준 `master=4ed89b5`. 사용자에게 전달된 BE 수정 요청에
따라 `asset_type="기타"`를 인식하되 모델링하지 않는 범주로 처리했다(`84a67b5`). 후속 3번에서
패키지를 **`0.1.1`로 갱신**하고 소스 682개·격리 설치 17개를 검증했다.
소스 검증과 [실제 설치 검증](installation-validation.md)은 별도 기록한다.
BE 설치·API 적용은 모듈 로컬 검증과 별개이며 완료로 표시하지 않는다.
일반/`-S` 전체 소스 회귀는 각각 680개 통과했다. 상세 경계와 재실행 명령은
[기타 금융자산 검증 기록](input-adapter-validation.md)을 참고한다.
이는 0.1.1 당시 기록이며 아래는 **후속 0.1.2의 입력 규칙**이다.
변경 이유·오류 구분·BE 테스트 조정은 [0.1.2 변경 안내](unmodeled-financial-assets.md)를 따른다.

- 모델링 5종 밖의 비어 있지 않은 분류 문자열 행은 `current_value`와 `monthly_savings`를 모두 제외한다.
  `기타`·`전세보증금`·`예금적금` 같은 오타도 분류 추정/치환 없이 제외한다. 금액을 읽거나
  누락/null을 0으로 보충하지 않으며, 원본 행·저장 금액은 변경하지 않는다. 행 구조·ID 검증은 유지한다.
- 제외 행마다 `other_financial_asset_not_modeled`를 생성한다. `field_name="financial_assets[N]"`의
  `N`은 0부터 시작하는 원본 배열 위치이며 DB ID가 아니다. 최종 `analysis_to_dict()`의 `notices`에 보존된다.
- 모든 금융자산이 제외 대상이면 모델링 자산·적립 합계는 0이다. 실제 자산 없음으로 확정하지 않으며 원본 목록이
  비어 있지 않으므로 `confirmed_empty_sections`에 `financial_assets`를 넣지 않는다.
- 분류 키 누락은 `missing_field`, null/공백은 `missing_value`, 비문자열은 `invalid_type`이다.
  문자열 `asset_type`에는 더 이상 `unsupported_value`를 쓰지 않지만 연금 등의 기존 미지원 오류는 유지한다.
- 기존 5종 계산·원본 보존·MC/What-if 엔진·가정·준비율 처리 범위는 유지한다.
  What-if 월 적립 명시값은 기존처럼 변경 후 총 월액이며 제외한 행의 적립액을 다시 합산하지 않는다.

합성 예: `[예금/적금(평가액 10,000,000원, 월 적립 200,000원),
전세보증금(평가액 5,000,000원, 월 적립 100,000원)]`이면 모델링 합계는 `10,000,000원 / 월 200,000원`이다.
아래 안내가 기존 범위 안내들과 함께 반환된다.

```json
{"code": "other_financial_asset_not_modeled", "field_name": "financial_assets[1]"}
```

적용 순서는 **BE 테스트의 임시 차단/모듈 오류 기대값 분리 → 수정 패키지 버전·소스 커밋·해시 확인 →
BE 설치 → 실제 API의 두 합계와 notices 확인 → 화면의 제외 안내 확인 → BE 임시 분류 차단 제거**다.
화면 안내가 가능하기 전까지 BE가 안내한 사전 감지/422는 유지한다. 계산용 금액 사전 검사가 제외 행의
미사용 금액을 다시 거절하지 않는지도 BE에서 확인하되 저장 serializer 제약과는 구분한다.
기존 notice 발생 대상을 넓히는 변경이지 HTTP 오류를 모듈에서 처리하거나 준비율을 바꾸는 변경은 아니다.

확인한 master의 [금융자산 폼](../../../front-pjt/src/components/forms/FinancialAssetsForm.vue)이 사용하는
[AddableTable.vue](../../../front-pjt/src/components/ui/AddableTable.vue)에는 `기타` 선택 시 직접 입력 경로가 있다.
이 경로에서는 선택 직후 빈 값, 입력 후 자유 문자열이 저장될 수 있다. 이 브랜치에서 FE나 BE를
수정하지 않으며 원본 문자열을 `기타`로 자동 치환하지 않는다.

## 1. 호출 경계

선택한 방향은 **BE 프로세스에서 단일 소스 Python 패키지를 import해 실행**하는 것이다.
AI 서버·Agent 라우팅·컨텍스트·재무상담 매뉴얼·LLM 호출은 계산의 전제 조건이 아니다.
보고서 버튼은 인증된 BE API를 호출해야 하며 브라우저에서 Python 모듈을 직접 실행하지 않는다.

```text
BE: 인증·상담 소유권 → 일관된 저장 snapshot·서버 가정·없음 확인·변경안
  → 모듈: analyze_retirement()
  → 모듈: analysis_to_dict() 또는 analysis_error_to_dict()
  → BE: 전체 JSON 인코딩·HTTP 응답 → FE: 확률/비교·가정·범위 안내 표시
```

`analysis_samples`는 내장 합성 사례만 실행한다. BE에서 CLI를 subprocess로 띄우거나 stdout을
파싱하는 연결을 만들지 않는다. 계산식/매퍼를 BE로 복사하거나 같은 기준 MC를 다시 계산할 필요도 없다.
What-if 한 번 호출에 기준과 변경안의 결과가 같은 시장 경로로 포함된다.

## 2. BE가 준비할 입력

정확한 값·단위·누락/추가 필드·기간 규칙은 [통합 호출 규격](analysis-contract.md)과
[저장 입력 매핑 규격](input-mapping-rules.md), [변경 입력 가이드](what-if-adapter.md)를 따른다.
새 고객 원천 필드나 HTTP 요청 모델이 이미 존재한다고 가정하지 않는다.

| Python 인자 | BE 연결부의 책임 |
|---|---|
| `saved_data` | 동일 시점의 `basic_info`와 `financial_assets`, `pension_assets`, `goal_periods`, `real_estate_assets`, `liabilities`, `goal_milestones`를 준비. DB 객체/채팅 전체/`current_form`이 아니라 저장형 mapping을 전달 |
| `assumption_values` | `annual_return_mean`, `annual_return_volatility`, `annual_inflation_rate`, `num_paths`, `seed` 5개를 명시. 비율은 5%=0.05, 금액/나이/횟수와 다른 단위. 출처·버전·갱신 및 자원 정책은 서버에서 관리 |
| `scenarios` | dict 행의 list/tuple. 빈 목록은 기준 MC, MVP는 What-if 1개. 엔진의 10개 허용과 구분. `scenario_id`와 `changes`를 전달하며 사용자 자유 제목/고객 ID를 계산 식별자로 쓰지 않음 |
| `confirmed_empty_sections` | 실제 없음이 확인된 `financial_assets`/`pension_assets`만 명시. 기본은 빈 tuple. 비어 있는 목록·폼 완료 플래그만 보고 없음으로 자동 확정하지 않음 |

스냅샷 구성과 호출 중 원본이 다른 요청에서 수정되지 않도록 하는 것은 BE 책임이다.
모듈이 원본을 수정하지 않는 것과 DB 트랜잭션/동시 요청의 snapshot 일관성은 다른 보장이다.
브라우저가 보낸 사용자 ID·상담 ID·없음 확인·가정·경로 수를 신뢰하여 바로 전달하지 않는다.
`customer_info` 구성이나 완료 상담의 접근 정책을 이 모듈이 대신 구현하지 않는다.

What-if의 월 적립은 증액분이 아닌 **변경 후 총 월액**이다. `None`/생략은 유지, `0`은 유효한
변경이고 생활비 `[]`는 오류다. 생활비 변경은 전체 기간 목록을 교체하므로 은퇴를 앞당기면 새로
생긴 은퇴 기간도 채워야 한다. 은퇴 나이 변경이 연금 개시 나이나 현재/종료 나이를 함께 바꾸지 않는다.
기존 기준에 각 변경안을 독립 적용하며 앞 변경안을 다음 변경안의 기준으로 사용하지 않는다.

부동산·부채·일회성 목표는 현재 계산에 포함하지 않으며 `notices`로 알린다. 목록을 지우거나
부동산을 현금에 더해 전체 재무상태를 반영한 것처럼 만들어서는 안 된다. 안내를 유지한 부분
분석은 허용하지만 필수 누락의 오류 처리는 유지한다. 없음 확인을 샘플에서 복사하지 않는다.

[현재 snapshot helper](../../../backend-pjt/reports/snapshots.py)는 재사용 후보지만 동일 시점 조회를
스스로 보장하지 않는다. `calculation_input_digest(session)`도 다시 조회하므로 snapshot과 digest를
각각 읽어 서로 다른 시점을 섞지 않는다. 확보한 **동일 snapshot**으로 계산과 기준 검증을 수행한다.
원천 숫자 타입·null·연금 기간·생활비를 점검하며 실제 없음의 근거가 없으면 입력 부족으로 반환한다.
오래된 보고서의 준비율과 최신 MC를 같은 기준이라고 결합하지 않는다. 보고서 갱신 UX/저장 권한은
기존 보고서 담당과 확인한다.

## 3. Python 호출 예시

다음은 **문서 내 연결 예시**이며 실제 BE 함수/API를 추가한 것이 아니다. 앞 단계의 인증·소유권·
일관된 조회·운영 제한을 통과한 입력을 받는다는 전제다. 패키지가 import 가능하도록 배포하는 작업은 별도다.

```python
from retirement_simulation import (
    RetirementAnalysisError,
    analyze_retirement,
    analysis_error_to_dict,
    analysis_to_dict,
)


def build_simulation_payload(saved_snapshot, server_assumptions, *,
                             scenarios=(), confirmed_empty_sections=()):
    try:
        result = analyze_retirement(
            saved_snapshot,
            server_assumptions,
            scenarios=scenarios,
            confirmed_empty_sections=confirmed_empty_sections,
        )
        return analysis_to_dict(result)
    except RetirementAnalysisError as error:
        return analysis_error_to_dict(error)
```

반환은 JSON 호환 dict이며 HTTP response/status가 아니다. 두 출력 함수는 엔진을 재실행하지 않는다.
BE는 dict를 엄격한 JSON(`allow_nan=False`)으로 완전히 인코딩한 후 응답하고, 알려진 오류의
HTTP 상태/envelope는 [서비스 연결 설계 D2](service-integration-design.md)에서 정한다.
위 예시를 그대로 항상 HTTP 200으로 감싸라는 뜻이 아니다.

예상 밖 버그·`MemoryError`·중단을 `except Exception`으로 정상 분석 오류에 섞거나 성공확률 0으로
대체하지 않는다. 서버의 비식별 내부 오류 응답·중단·시간 제한·로그 정책을 별도로 구현해야 한다.
원천 재무정보·프로필·지역 변수·traceback 전체를 일반 로그나 클라이언트 응답에 덤프하지 않는다.
현재 함수는 timeout·캐시·재시도·고객 정보/결과 저장을 수행하지 않는다.

## 4. 결과를 화면에 전달할 때

| 결과 | 해석·주의 |
|---|---|
| `contract_version`, `mapping_version`, `metadata` | 계약/계산 모델/매핑 버전·실행 가정·기간·경로 수·난수 출처를 보존. 서버의 가정 출처/설정 버전과 snapshot 식별은 별도 책임 |
| `baseline_profile`, 변경 `profile` | 실제 계산에 사용한 입력. 원천 전체 개인정보는 아니지만 재무정보이므로 인증 없이 공개하거나 일반 로그에 남기지 않음 |
| `baseline.success_probability` | 0~1 비율. FE 퍼센트 표시는 ×100. 기존 준비율과 별도이며 준비율로 이름만 바꾸지 않음 |
| 변경 `delta.success_probability_delta_pp` | 이미 퍼센트포인트(pp). 다시 ×100 하지 않음. 다른 금액 차이도 변경−기준 |
| 잔액·부족액·연도별 분위수 | 계산 금액은 명목 원 단위. 음수 잔액은 누적 부족액 계산이며 실제 차입/순자산이 아님. p10/p50/p90은 표본 분포 요약이고 보장/신뢰구간/한 경로가 아님 |
| `notices` | 미지원 재무 범위·없음 확인·연금 기간 안내 등을 모두 보존하고 화면에서 설명. 결과가 나왔다고 안내를 숨기지 않음 |
| `error` | 알려진 실패는 성공 결과가 없는 오류 객체. 코드·stage·허용 field 경로·고정 설명만 소비. 입력값/알 수 없는 키를 오류 원문으로 추가하지 않음 |

수치 범위·오류 코드 7개·전체 JSON 예시는 [출력 변환 가이드](analysis-output.md)와
[통합 규격](analysis-contract.md)에 있다. 큰 숫자를 반올림/문자열화/상한 치환해 검증을 우회하지 않는다.
What-if의 하위 기준/변경 결과는 제공받은 공통 경로를 사용하므로 하위 `effective_seed=null`이
정상이다. 배치 `metadata`의 실제 시드/생성 출처와 구분한다.

원본 DB·보고서 버전·AI 대화 컨텍스트는 What-if 실행으로 수정하지 않는다.
별도 기준 조회 후 What-if를 실행한다면 snapshot·가정·시드를 동일하게 유지해야 한다.
사용자가 조건을 바꾸는 동안 늦게 도착한 응답이 새 화면을 덮어쓰지 않게 하는 것도 FE/BE 연결 책임이다.
변경 후 준비율은 이번 MVP에서 제외했다. 기존 준비율의 정의·원천·평가 시점은 BE/FE 확인 사항이다.
모듈 `metadata`의 실제 가정/환경과 별도로, 서비스 envelope에 정책 버전·보고서 기준·서버 기준 식별자를
기록한다. `assumption_values` 안에 정책 metadata를 섞지 않는다.
`baseline_key`의 형식/유효기간/저장 방식은 미합의이며 재무값이나 재무값의 단순 해시를 공개하지 않는다.
키는 사용자·상담/보고서·동일 snapshot·정책·엔진/환경에 결합하고 매번 소유권을 별도로 확인한다.
FE는 비교 응답에 포함된 기준/변경 결과를 함께 교체한다.

## 5. 연결 전에 정할 사항

[서비스 연결 설계](service-integration-design.md)의 D1~D7을 실행 가능한 점검 항목으로 정리했다.
별도 회의 형식은 필수가 아니지만 다른 영역의 승인/기존 계약 확인 없이 완료로 표시하지 않는다.

| 항목 | 확인 내용·연결 작업 경계 |
|---|---|
| 패키지·배포 (D1) | 단일 소스 패키지/이미지 포함 방식·버전 고정·호출 위치. 최신 BE build context는 저장소 루트지만 이미지의 모듈 설치 단계는 없어 import를 보장하지 않음 |
| 인증·조회·요청 계약 (D2) | 사용자/상담 소유권·완료 상담 실행 정책·동일 snapshot·기준 ID/만료·HTTP URL/status/envelope. 채팅 API 재사용이나 인증 우회 금지 |
| 기존 준비율 (D3) | 변경 후 준비율 제외. 기존 지표의 분자/분모·원천·시점·미준비 처리와 실데이터 연결 확인 |
| 입력/부분 분석 (D4) | 부분 분석 허용은 확정. 실제 타입/null·기간·연금 없음 근거·모든 notices 전달 확인. 모듈 나이 허용이 기존 BE 쓰기 검증을 변경하는 것은 아님 |
| 가정 연결 (D5) | [정책 JSON](simulation-policy.json)의 설정 배치·로드 실패·응답 버전 기록 확인. 시연값 5%를 사용하지 않으며 외부 금융 API는 제외 |
| 실행 보호 (D6) | 배포 환경 재측정·서버 측 사전 제한·동시성·시간 초과·중복/재시도·취소·자원 회수. 경로·기간·변경안을 조용히 잘라 실행하지 않음 |
| 화면 (D7) | 버튼·조건 3종/생활비 전체 일정·확률/pp/금액·가정/안내·오류/로딩·오래된 응답 방지 |

패키징 구현·단일 소스 wheel/sdist 빌드·저장소 밖 격리 설치 검증은 완료했다.
BE 설치는 소스 복제·임시 `sys.path`나 tests의 소스 실행기를 사용하지 않는다.
실제 BE 이미지 포함 방식과 담당자는 별도 확인한다. 로컬 검증 환경은 Miniforge `retirewise-ai` /
CPython 3.13.15, 현재 BE Dockerfile은 3.13.14이며 실제 이미지의 설치·호환성·성능은 미검증이다.

[성능 기록](analysis-performance.md)의 **1,000경로·51년·3개 비교 이하**는 당시 시험 후보 범위다.
후속 MVP 입력은 1,000경로·변경안 1개로 정했지만 BE 성능/SLA는 아직 검증하지 않았다.
벤치마크 worker timeout도 API timeout이 아니다.
51년보다 긴 실제 분석을 51년으로 자르지 말고 지원 범위/대기 방식을 명시적으로 정한다.
API 동시 부하·전체 요청 시간·배포 메모리를 실제 환경에서 검증해야 한다.
현재 BE는 uvicorn worker 2개이므로 프로세스 내부 락 하나가 서비스 전체 동시 제한을 뜻하지 않는다.
단일 계산부터 실제 환경을 측정하고 제한 범위·중복 요청·취소/timeout 시 계산 회수를 정한다.
HTTP 연결 종료가 동기 CPU 계산을 중단한다고 가정하지 않는다. 이 문서로 새 큐/서버를 도입하지 않는다.

## 6. 인수 체크와 남은 순서

모듈 측 인수 증거는 [통합 샘플 검증 기록](analysis-samples.md)에 모은다.
저장형 합성 입력→실제 MC/What-if→성공/오류 JSON, 기존 계산과 일치, 공통 경로/원본 불변,
확대 경계 회귀 및 실행 예시는 이 브랜치의 검증 대상이다.

다음 **서비스 인수 항목은 아직 미완료**다.

- [ ] 실제 BE 이미지에서 단일 소스 패키지를 import하고 모듈 검증을 재실행한다.
- [ ] 인증/다른 상담 접근/완료 상담/동일 snapshot·기준 변경을 API로 검증한다.
- [ ] 실제 저장 타입·누락·없음 확인·이미 은퇴·부분 분석 정책 및 가정 출처를 검증한다.
- [ ] 원본 재무정보·보고서·Agent 컨텍스트를 변경하지 않고 저장→보고서→계산→비교를 실행한다.
- [ ] FE의 실제 준비율·성공확률·변경 pp·기간별 결과·안내를 원천과 대조하고 mock/고정값을 제거한다.
- [ ] 동시/중복 요청·취소/timeout·실패 재시도·늦은 응답·배포 성능·비식별 로그를 검증한다.

통합 분석은 MR !48, 패키징은 MR !52로 병합됐다. 현재 브랜치는 정책·검증·전달 자료를 보완한다.
푸시/MR은 명시 요청 시에만 하며 이지석 계정과 기존 MR 양식을 확인한다.
다음 서비스 작업은 승인된 별도 연결 범위에서
**담당/기술 계약 확인 → BE 이미지 설치/인증 API + FE 병행 → 실제 데이터/E2E → 배포 검증** 순으로 진행한다.
전달 묶음과 구체적 다음 작업은 [후속 작업 순서](handoff-next-steps.md)를 따른다.
이 문서를 추가하는 것만으로 BE/FE 담당 작업을 수행하거나 서비스 인수를 완료한 것은 아니다.
