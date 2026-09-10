# 은퇴 시뮬레이션 BE 직접 호출 인수인계

작성일: 2026-09-09. 통합 분석 브랜치 6번의 **모듈 인수인계**다.
계산·매핑·통합 실행·결과/오류 JSON·합성 성능 측정·[통합 CLI](analysis-samples.md)를 구현했다.
BE/FE·DB·Agent·공통 의존성·배포 설정은 수정하지 않았다. 실제 API/화면 연결은 아직 미구현이다.
아래 담당 범위는 연결 작업을 나누기 위한 제안이며 담당자의 수락이나 변경 권한을 대신하지 않는다.

2026-09-10 갱신: MR !48 병합 후 `master=d0194e7` 기준의 [패키징 규격](packaging-contract.md)과
[BE 전달용 설치·호출 확인서](be-integration-checklist.md)를 추가했다. 패키징은 0·1번 준비/문서화만
완료했으며 빌드 설정·wheel·실제 설치·BE/FE 연결은 미구현이다. 확인서는 아직 전달/합의되지 않았다.

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
| `scenarios` | dict 행의 list/tuple. 빈 목록은 기준 MC, 1~10개는 What-if. `scenario_id`와 `changes`를 전달하며 사용자 자유 제목/고객 ID를 계산 식별자로 쓰지 않음 |
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
부동산을 현금에 더해 전체 재무상태를 반영한 것처럼 만들어서는 안 된다. 부분 분석을 허용할지
보류할지는 실제 서비스 정책으로 정해야 한다. 없음 확인도 샘플 값을 고객에게 복사하지 않는다.

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
                             changes=(), confirmed_empty_sections=()):
    try:
        result = analyze_retirement(
            saved_snapshot,
            server_assumptions,
            scenarios=changes,
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
실제 준비율의 정의·원천·평가 시점 및 변경 후 준비율 제공 여부는 이 모듈이 결정하지 않았다.

## 5. 연결 전에 정할 사항

[서비스 연결 설계](service-integration-design.md)의 D1~D7을 실행 가능한 점검 항목으로 정리했다.
별도 회의 형식은 필수가 아니지만 다른 영역의 승인/기존 계약 확인 없이 완료로 표시하지 않는다.

| 항목 | 확인 내용·연결 작업 경계 |
|---|---|
| 패키지·배포 (D1) | 단일 소스 패키지/이미지 포함 방식·버전 고정·호출 위치. 최신 BE build context는 저장소 루트지만 이미지의 모듈 설치 단계는 없어 import를 보장하지 않음 |
| 인증·조회·요청 계약 (D2) | 사용자/상담 소유권·완료 상담 실행 정책·동일 snapshot·기준 ID/만료·HTTP URL/status/envelope. 채팅 API 재사용이나 인증 우회 금지 |
| 실제 준비율 (D3) | 분자/분모·원천·시점·0/미준비 처리. 시뮬레이션 성공확률과 구분하고 변경 후 준비율 제공 여부 확정 |
| 입력/부분 분석 (D4) | 저장된 타입/null·기간·연금 없음 근거·부동산/부채/목표 제외 허용. 모듈의 이미 은퇴/단일 나이 지원이 기존 BE 쓰기 검증의 허용을 의미하지 않음 |
| 실제 가정 (D5) | 평균·변동성·물가 출처/버전/갱신. 샘플 0.05/0.10/0.02를 운영값으로 자동 사용하지 않음. 외부 금융 API 도입 자체는 필수도 확정도 아님 |
| 실행 보호 (D6) | 배포 환경 재측정·서버 측 사전 제한·동시성·시간 초과·중복/재시도·취소·자원 회수. 경로·기간·변경안을 조용히 잘라 실행하지 않음 |
| 화면 (D7) | 버튼·조건 3종/생활비 전체 일정·확률/pp/금액·가정/안내·오류/로딩·오래된 응답 방지 |

패키징은 규격만 정리했으며 아직 구현하지 않았다. 모듈 내부 단일 소스에서 wheel/sdist를 빌드하고
저장소 밖에서 설치를 검증하는 방향이다. 소스 복제나 임시 `sys.path`는 사용하지 않는다.
실제 BE 이미지 포함 방식과 담당자는 별도 확인한다. 로컬 검증 환경은 Miniforge `retirewise-ai` /
CPython 3.13.15, 현재 BE Dockerfile은 3.13.14이며 실제 이미지의 설치·호환성·성능은 미검증이다.

[성능 기록](analysis-performance.md)의 **1,000경로·51년·3개 비교 이하**는 초기 연동 시험 후보일 뿐
운영 상한/기본값/SLA가 아니다. 벤치마크 worker timeout도 API timeout이 아니다.
51년보다 긴 실제 분석을 51년으로 자르지 말고 지원 범위/대기 방식을 명시적으로 정한다.
API 동시 부하·전체 요청 시간·배포 메모리를 실제 환경에서 검증해야 한다.

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

통합 분석은 MR !48로 병합됐다. 현재 패키징 브랜치는 준비/규격 문서화 다음에 빌드·격리 설치·검증을 진행한다.
푸시/MR은 명시 요청 시에만 하며 이지석 계정과 기존 MR 양식을 확인한다.
다음 서비스 작업은 승인된 별도 연결 범위에서
**BE 패키징/인증 API → 기본 MC·준비율 화면 → What-if 화면 → 운영/E2E 검증** 순으로 진행한다.
이 문서를 추가하는 것만으로 BE/FE 담당 작업을 수행하거나 서비스 인수를 완료한 것은 아니다.
