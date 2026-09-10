# 은퇴 시뮬레이션 패키징 규격

작성일: 2026-09-10. 기준: MR !48 병합(`14eb23c`) 이후 `master=d0194e7`.
브랜치: `feat/ai-retirement-packaging`. **0번 준비·1번 규격 문서화만 완료**한 문서다.
패키지 빌드 설정·배포 파일·설치 검증·BE 이미지/API 연결은 아직 구현하지 않았다.
아래 선택은 모듈 내부 구현 기준이며 BE·배포 담당자가 설치 방식을 승인했다는 뜻이 아니다.

## 1. 목표와 변경 경계

기존 `retirement_simulation`을 **단일 소스에서 빌드해 설치 가능한 독립 Python 패키지**로 준비한다.
BE가 설치된 패키지의 공개 함수를 import하는 방향은 유지한다. AI 서버 호출이나 CLI subprocess를
서비스 진입점으로 사용하지 않는다. 공개 함수와 계산 규칙은 [통합 호출 규격](analysis-contract.md)을 따른다.

- 변경 위치는 `ai-pjt/retirement_simulation/` 내부로 제한한다. 소스 이동·복제·import 이름 변경 없음.
- 이번 0·1번은 Markdown만 변경한다. `pyproject.toml`, `MANIFEST.in`, 빌드 스크립트·테스트도 아직 추가하지 않는다.
- 후속 구현에서도 계산식·모델·공개 export·JSON/오류 계약·가정 기본값을 패키징 때문에 바꾸지 않는다.
- BE/FE·Agent·컨텍스트·매뉴얼·공통 정책·루트 설정·requirements·Docker·Compose·CI·Jira는 변경하지 않는다.
- 외부 PyPI/GitLab Registry 업로드, BE 담당자 메시지 전송, 배포 실행은 포함하지 않는다.
  [BE 연결 확인서](be-integration-checklist.md)는 전달 준비 자료이며 실제 전달/합의 기록이 아니다.

## 2. 최신 코드에서 확인한 전제

| 항목 | `d0194e7`의 확인 사실 | 패키징에 반영할 내용 |
|---|---|---|
| 원천 위치 | 모듈 루트 Python 18개, `docs/`, `tests/`. 빌드 메타데이터 없음 | 이 디렉터리 자체를 빌드 프로젝트 루트로 사용 |
| BE 빌드 | [Compose](../../../docker-compose.yml)의 backend/migrate가 저장소 루트 `context: .` 사용 | 빌드 컨텍스트를 다시 바꾸는 작업은 제안하지 않음 |
| BE 이미지 | [Dockerfile](../../../backend-pjt/Dockerfile)은 Python `3.13.14-slim-bookworm`. BE 소스·공통 정책만 복사하고 잠금된 의존성을 설치 | 모듈 설치 단계는 여전히 없음. 실제 이미지 포함은 별도 BE/배포 작업 |
| AI 이미지 | [Dockerfile](../../Dockerfile)이 AI 소스를 복사해 모듈 소스를 포함 | AI 이미지에 존재하는 사실은 BE에서 설치/import 가능하다는 증거가 아님 |
| 입력 원천 | [보고서 스냅샷](../../../backend-pjt/reports/snapshots.py)에 `build_calculation_input_snapshot()`·digest 함수 존재 | 호출자가 재사용 검토. 모듈에서 Django import나 DB 조회를 추가하지 않음 |
| 가정 정책 | [정책 로더](../../../backend-pjt/reports/policy.py)·[공통 정책 JSON](../../../contract-fixtures/financial-calculation/v1/policy.json)에 수익률·물가 존재. MC 변동성 항목 없음 | 패키지에 정책 파일을 복제하거나 샘플 변동성을 운영 기본값으로 주입하지 않음 |
| 로컬 검증 | Miniforge `retirewise-ai`, CPython 3.13.15 | BE의 3.13.14 실행 검증과 구분 |

스냅샷 helper는 여러 관계를 조회하고 digest 함수도 스냅샷을 다시 만든다. 이것만으로
DB의 동일 시점 읽기·없음 확인·원본 계산과 digest의 일치가 보장된다고 가정하지 않는다.
실제 조회 타입/null·동시 수정·정책 버전·인증 경계는 연결 단계에서 확인해야 한다.

## 3. 패키지 선택

### 이름·버전·의존성

| 항목 | 모듈 내부 구현 기준 |
|---|---|
| 배포 이름 | `retirewise-retirement-simulation`. 프로젝트 내부 산출물의 이름이며 공개 저장소의 이름 선점/게시를 의미하지 않음 |
| import 이름 | 기존 `retirement_simulation` 유지 |
| 최초 패키지 버전 | `0.1.0` 예정. 정적 메타데이터로 관리하고 계산 코드 import·Git 명령으로 동적 생성하지 않음 |
| Python 범위 | 초기 `>=3.13,<3.14` 예정. 우선 CPython 3.13 계열에 한정. 이 메타데이터는 모든 patch/OS를 실제 검증했다는 뜻이 아님 |
| 우선 검증 환경 | Linux/WSL의 로컬 3.13.15, BE 연결 인수 시 실제 3.13.14 이미지. 다른 Python/OS는 미검증 |
| 런타임 의존성 | `dependencies = []`. AI/BE 전체 requirements를 패키지 의존성으로 가져오지 않음 |
| 빌드 백엔드 | `setuptools.build_meta`, 초기 구현은 현재 확인한 `setuptools==84.0.0` 기준으로 검증 |
| CLI | 기존 `python -m retirement_simulation.analysis_samples` 등 모듈 진입점 유지. 새 console script/HTTP 서버 불필요 |
| 라이선스·공개 배포 | 임의로 MIT 등 라이선스를 지정하지 않음. 공개 배포는 이번 범위 밖이며 별도 권한 확인 필요 |

패키지 버전은 `retirement-analysis-v1`·`retirement-input-v1`·MC/What-if 계산 모델 버전과 별개다.
단지 설치 가능해졌다는 이유로 JSON에 버전을 추가하거나 기존 `__init__.py`에 `__version__`를 넣지 않는다.

빌드 설정은 표준 `pyproject.toml`에 두고, 메타데이터와 빌드 도구 요구사항을 런타임 의존성과
구분한다. [PyPA 메타데이터 안내](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)와
[setuptools 설정 안내](https://setuptools.pypa.io/en/latest/userguide/pyproject_config.html)를 기준으로 한다.
현재 환경에서 pip 26.2.1·setuptools 84.0.0은 확인했고 `build`·`wheel` 배포 패키지는 설치되지 않았다.
이번에는 설치하지 않았다. 후속 빌드는 별도 환경에서 필요한 도구와 버전을 기록하고, 공용 환경을 갱신하지 않는다.

### 소스 배치

일반적인 새 프로젝트라면 src layout도 선택할 수 있지만, 이 작업은 기존 경로와 import를 유지해야 한다.
따라서 상위 `ai-pjt/`를 자동 탐색하지 않고 **패키지 하나와 현재 디렉터리의 대응을 명시**한다.
[setuptools의 명시적 package-dir 지원](https://setuptools.pypa.io/en/latest/userguide/package_discovery.html)을
사용하며 실제 산출물의 경로는 2·3번에서 검사한다.

아래는 **2번 구현용 설정 초안**이다. 현재 파일로 존재하지 않으며 아직 빌드/설치할 수 없다.

```toml
[build-system]
requires = ["setuptools==84.0.0"]
build-backend = "setuptools.build_meta"

[project]
name = "retirewise-retirement-simulation"
version = "0.1.0"
description = "Standalone retirement Monte Carlo and what-if simulation"
readme = "README.md"
requires-python = ">=3.13,<3.14"
dependencies = []

[tool.setuptools]
packages = ["retirement_simulation"]
package-dir = {retirement_simulation = "."}
include-package-data = false
```

설정 위치는 `ai-pjt/retirement_simulation/pyproject.toml`로 계획한다. 저장소/AI 루트에는 만들지 않는다.
패키지 자동 탐색으로 `tests`, `docs`, Agent·공용 도구가 새 import 패키지가 되지 않게 한다.
설정 가능성과 이 프로젝트에서 실제 빌드되는 것은 구분하며, 2번에서 실패하면 계산 코드 이동으로
우회하지 말고 원인을 확인하고 이 규격/패키징 설정 범위에서 해결한다.

## 4. 산출물과 포함 파일

기본 산출물은 **wheel 1개와 sdist 1개**다. wheel은 설치용, sdist는 같은 소스에서 재빌드하기 위한
소스 배포본이다. 소스 배포본을 만든 뒤 거기에서 wheel을 빌드하는 흐름을 검증한다.
[PyPA 패키징 안내](https://packaging.python.org/en/latest/tutorials/packaging-projects/)를 참고한다.

| 대상 | 포함 | 제외/검사 |
|---|---|---|
| wheel | `retirement_simulation/`의 기존 루트 Python 18개와 정상 배포 메타데이터 | tests·docs 데이터·빌드 설정·BE/AI 앱·공통 정책·계정 정보·빌드 캐시 미포함 |
| sdist | 위 소스, 빌드 설정·표준 메타데이터, README, 모듈 내부 테스트·문서/합성 성능 JSON. 추가 파일은 필요성을 명시 | 저장소 다른 디렉터리·`.git`·환경 파일·고객 자료·venv·캐시·기존 산출물 미포함 |
| 검증 기록 | 소스 커밋, 도구/환경, 파일 목록, 산출물 SHA-256, 설치/실행 결과 | 바이너리/압축 산출물 자체를 Git에 커밋하거나 원격에 게시하지 않음 |

wheel의 Python 목록에는 기존 샘플들과 개발용 `analysis_benchmark.py`도 포함한다. 공개 API로
export하지 않는다는 기존 경계는 유지한다. 테스트 helper를 사용하지 않고 실행되는 합성 CLI는
설치 후에도 사용할 수 있어야 한다. sdist 문서의 BE/FE 상대 링크는 원본 저장소 맥락의 링크다.
이를 해결하려고 BE/FE 소스를 sdist에 포함하지 않는다.

`MANIFEST.in`으로 sdist의 추가 파일을 관리하고 wheel과 별도로 검사한다.
`include-package-data`의 기본값에 의존하지 않는다. 루트 자체가 패키지 디렉터리인 배치에서는
sdist에 넣은 docs/tests가 wheel로 유입되는지 특히 확인한다.
[setuptools 파일 포함 규칙](https://setuptools.pypa.io/en/latest/userguide/miscellaneous.html)을 참고한다.

소스 복제본을 제품 코드로 유지하지 않는다. 검증을 위한 임시 소스 묶음/빌드 디렉터리는 허용하되,
작업 전용 임시 경로와 검증된 원천만 사용한다. 빌드 캐시가 필요하면 모듈 내부 ignore 또는 임시 경로로
격리하고 루트 `.gitignore`는 수정하지 않는다. 경로 탈출·외부 symlink·금지 파일 포함도 실패 조건이다.
동일 입력이 항상 동일 압축 바이트가 된다는 재현 빌드 보장은 아직 하지 않는다.

## 5. 설치 검증의 완료 기준

일반 설치와 editable 설치는 다를 수 있으므로 배포 인수는 **실제 wheel의 일반 설치**로 한다.
[pip의 로컬 프로젝트 설치 안내](https://pip.pypa.io/en/stable/topics/local-project-installs/)를 따른다.
공개 인덱스에서 패키지 이름만으로 다운로드하거나 운영에 editable 설치하지 않는다.
BE에 설치할 때는 승인된 내부 산출물의 정확한 버전/해시를 사용한다.

1. 전용 임시 환경에서 빌드한다. 빌드 도구 확보의 네트워크 사용과 런타임 무의존성을 구분한다.
   `--no-deps`만으로 소스 빌드의 도구 다운로드까지 막았다고 주장하지 않는다.
2. `.git`·상위 AI 프로젝트 없이 sdist에서 wheel을 재빌드하고 두 산출물의 파일/메타데이터를 검사한다.
   Git tag/임의 작업 경로가 없다는 이유로 버전·필요한 파일이 달라져서는 안 된다.
3. 별도 깨끗한 가상환경에 그 wheel만 설치한다. 미리 준비된 산출물 설치는 인덱스/런타임 의존성
   다운로드 없이 가능해야 한다. 현재 `retirewise-ai` 환경에 덮어 설치하지 않는다.
4. 저장소 밖 cwd에서 Python `-I -B`로 공개 함수와 합성 CLI를 실행한다. `PYTHONPATH`나
   테스트의 `sys.path.insert()`로 소스를 다시 보이게 하지 않는다. 실제 `__file__`와 distribution
   위치가 임시 환경 안이며 저장소 밖인지 검사하고 필수 import가 하나라도 없으면 실패한다.
5. 설치된 패키지만으로 기준/What-if·성공/알려진 오류 JSON, 손계산 −2,000→400원·+100pp,
   원본 보존·메타데이터·CLI 종료 코드를 검증한다. 엔진 테스트의 fixture를 import해 소스로 우회하지 않는다.
6. 별도로 저장소의 기존 620개 회귀를 재실행한다. 새 설치 검증은 외부 패키지 출처를 확인하는
   전용 검사이고, 기존 테스트를 실행했다는 이유만으로 wheel 검증 완료를 선언하지 않는다.
7. 실제 BE 이미지의 Python에서 설치·권한·의존성 잠금·요청 부하를 확인하는 것은 BE 연결 인수다.
   로컬 3.13.15 합성 성공으로 3.13.14 이미지나 서비스 통합을 검증했다고 표시하지 않는다.

기존 테스트는 소스 경로를 추가하고 일부는 docs/성능 JSON·테스트 helper를 읽는다.
wheel에서 docs/tests를 제외하면 이 방식으로 설치 여부를 판단할 수 없으므로 위 두 검증을 분리한다.
`-S`는 기존 소스의 표준 라이브러리 경계 검사에 유지한다. 일반 설치의 site-packages도 제외하므로
설치 인수에는 그대로 사용하지 않는다. `-I`의 환경/경로 격리는
[Python 3.13 실행 옵션](https://docs.python.org/3.13/using/cmdline.html#cmdoption-I)을 따른다.

기존 성능 원시 기록·과거 소스 해시는 유지한다. 패키지의 샘플/성능 도구 존재는 API 동시성·
응답시간 보장이 아니며 이 작업에서 전체 성능 행렬이나 시장 가정을 재검증하지 않는다.

## 6. BE 협업 시점

**패키징 준비는 독립 진행 가능, 실제 BE 설치/API 연결 전에는 담당 범위와 계약 확인이 필요**하다.
설치 방식은 지금 미리 공유하는 것을 권장하지만 응답이 없다는 이유로 BE 파일을 수정하지 않는다.

- 모듈 담당: 패키지/설치 검증·공개 호출 가이드·합성 fixture·한계와 오류 설명 제공.
- BE/배포 담당 또는 별도 승인된 작업: 이미지 포함·잠금/해시 관리·인증·상담 조회·가정 설정·실행 제한.
- FE 담당 또는 별도 승인된 작업: 요청 계약 확인, 버튼·조건 입력·준비율/성공확률·안내/오류 표시.
- 다른 AI 담당: 현재 Agent·컨텍스트·매뉴얼 연결은 필요 없음. 공통 정책 자체를 바꿔야 하면 소유자 확인.

실제 확인 항목과 답변란은 [BE 연결 확인서](be-integration-checklist.md)에 모은다.
기존 스냅샷/정책을 재사용하더라도 MC 평균/변동성/물가의 의미·출처, 실제 없음 확인,
부분 분석 허용과 snapshot 일관성은 별도 검증한다. 새로운 금융 API 도입은 필수도 확정도 아니다.

## 7. 브랜치 작업·권장 커밋

| 번호 | 작업 | 권장 커밋 |
|---|---|---|
| 0 — 완료 | 최신 master·기준 테스트·기능 브랜치 준비 | 별도 커밋 없음, 이 문서에 기록 |
| 1 — 완료 | 패키징 규격·설치 검증 기준·BE 확인서 문서화 | `docs: 은퇴 시뮬레이션 패키징 규격 정의` |
| 2 | 패키지 메타데이터·파일 포함 규칙·빌드 설정·기본 검증 | `build: 은퇴 시뮬레이션 패키지 빌드 설정` |
| 3 | 저장소 밖 wheel 설치·import·합성 실행·실패 경계 검증 | `test: 은퇴 시뮬레이션 설치 경계 검증` |
| 4 | 전체 회귀·산출물 검사·실제 설치 방법/인수인계 문서 | `docs: 은퇴 시뮬레이션 패키지 설치 가이드` |
| 5 | 최종 범위·master 충돌 확인 후 요청 시 푸시/MR | 필요한 변경만 별도 커밋. 원격 작업은 명시 요청 시 |

각 요청 단위는 문서화·검증 후 로컬 커밋한다. 아직 요청하지 않은 2~5번을 함께 구현하지 않는다.
그 뒤 서비스 작업은 BE 설치/인증 API → 기준 MC·실제 준비율 화면 → What-if 화면 → 운영/E2E 검증이다.

## 8. 0·1번 검증 기록

2026-09-10, Miniforge `retirewise-ai` / CPython 3.13.15에서 확인했다.

- 시작은 깨끗한 master였다. 원격 fetch 후 HEAD·로컬 master·`origin/master`가 모두 `d0194e7`임을 확인했다.
  기준 테스트 통과 후 `feat/ai-retirement-packaging`을 생성했다. 별도 master 병합·rebase·브랜치 삭제 없음.
- 기존 **620개** 테스트가 문서화 전 일반·`-S`, 변경 후 일반·`-S`·`-S` 역순 실행에서 모두 통과했다.
  신규 테스트나 설치 검증을 추가한 숫자가 아니다.
- 기존 Python **43개 파일**의 구문과 `d0194e7` 대비 byte 단위 동일성을 확인했다. 루트 Python은 **18개**다.
  기존 계약 JSON 예시·성능 원시 기록도 그대로 유지했다.
- 모듈 로컬 Markdown 링크 **335개**·anchor **5개**, 변경 문서의 제목/코드 블록/공백과
  `git diff --check`를 확인했다. 외부 참고 링크는 이 로컬 링크 개수에서 제외한다.
- TOML 초안을 `tomllib`로 파싱해 이름/버전·Python 범위·무의존성·명시적 package-dir·파일 포함 옵션을
  대조했다. 구문 확인일 뿐 setuptools로 빌드한 것이 아니며 실제 설정 파일은 존재하지 않는다.
- 기존 BE 인수인계의 Python 예시를 합성 기준/비교/오류 입력으로 실행하고 손계산·JSON 왕복·원본 보존을 확인했다.
- BE/migrate의 루트 build context·Python 이미지·모듈 설치 부재, 공통 정책의 수익률/물가·변동성 항목 부재를
  소스와 대조했다. 실제 BE import·Django/DB·Docker 빌드·시장 가정 검증은 하지 않았다.
- 변경은 **모듈 내부 Markdown 5개**뿐이다. 빌드/설치 설정·소스·테스트·의존성/개발 환경·BE/FE·배포는 변경하지 않았다.
  BE 연결 확인서 7개 항목은 전부 미확인으로 유지했고 다른 팀원에게 전송하지 않았다.

저장소 루트에서 기존 소스 회귀를 재실행한다.

```bash
conda run -n retirewise-ai python -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
```

빌드·설치·실제 BE 이미지·서비스 인수는 이번 완료 범위가 아니다. 다음은 같은 브랜치의 2번 빌드 설정이다.
