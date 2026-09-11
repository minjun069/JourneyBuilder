# 은퇴 시뮬레이션 패키징 규격

작성일: 2026-09-10. 기준: MR !48 병합(`14eb23c`) 이후 `master=d0194e7`.
브랜치: `feat/ai-retirement-packaging`. **0번 준비·1번 규격·2번 빌드·3번 격리 설치·4번 최종 검증/가이드 완료**.
로컬 wheel/sdist·소스/설치 재검증과 [모듈 설치 가이드](package-installation.md)를 완료했다. BE 이미지/API 연결은 아직 진행하지 않았다.
아래 선택은 모듈 내부 구현 기준이며 BE·배포 담당자가 설치 방식을 승인했다는 뜻이 아니다.

2026-09-11 보완: [비모델링 금융자산 분류 확장](unmodeled-financial-assets.md)을 `0.1.2`로 구분한다.
아래 현재 설정과 설치 runner는 `0.1.2`를 대상으로 하며, 8~11절의 `0.1.0`과 12절의 `0.1.1`
검증 기록은 당시 결과로 유지한다.

## 1. 목표와 변경 경계

기존 `retirement_simulation`을 **단일 소스에서 빌드해 설치 가능한 독립 Python 패키지**로 준비한다.
BE가 설치된 패키지의 공개 함수를 import하는 방향은 유지한다. AI 서버 호출이나 CLI subprocess를
서비스 진입점으로 사용하지 않는다. 공개 함수와 계산 규칙은 [통합 호출 규격](analysis-contract.md)을 따른다.

- 변경 위치는 `ai-pjt/retirement_simulation/` 내부로 제한한다. 소스 이동·복제·import 이름 변경 없음.
- 0·1번은 Markdown만 변경했다. 2번은 모듈 내부 빌드 설정·파일 포함 규칙·ignore·설정 테스트를 추가한다.
- 3번은 설치 검증 runner·독립 설치 검사·소스 방어 테스트와 검증 기록을 추가한다. 테스트/문서는 wheel에 넣지 않는다.
- 4번은 최종 회귀·산출물/설치 확인과 모듈 설치 가이드만 진행한다. 기존 Python/빌드 설정은 바꾸지 않는다.
- 후속 구현에서도 계산식·모델·공개 export·JSON/오류 계약·가정 기본값을 패키징 때문에 바꾸지 않는다.
- BE/FE·Agent·컨텍스트·매뉴얼·공통 정책·루트 설정·requirements·Docker·Compose·CI·Jira는 변경하지 않는다.
- 외부 PyPI/GitLab Registry 업로드, BE 담당자 메시지 전송, 배포 실행은 포함하지 않는다.
  [BE 연결 확인서](be-integration-checklist.md)는 전달 준비 자료이며 실제 전달/합의 기록이 아니다.
- 사용자 요청에 따라 현재 패키징 작업에서 BE 전달 문서를 보완하지 않는다. 관련 설명/문서 보완은 브랜치 종료 후 별도 요청 시 진행한다.

## 2. 브랜치 시작 시 확인한 전제

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
| 패키지 버전 | 현재 `0.1.2`, 최초 `0.1.0`. 정적 메타데이터로 관리하고 계산 코드 import·Git 명령으로 동적 생성하지 않음 |
| Python 범위 | 초기 `>=3.13,<3.14`. 우선 CPython 3.13 계열에 한정. 이 메타데이터는 모든 patch/OS를 실제 검증했다는 뜻이 아님 |
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
0·1번 당시 공용 `retirewise-ai`의 pip 26.2.1·setuptools 84.0.0을 확인했고 `build`·`wheel`은 없었다.
2번에서는 전용 임시 venv에만 `build`·빌드 백엔드와 도구 의존성을 설치했다. 공용 Miniforge 환경은
갱신하지 않았으며 `wheel` 배포 패키지를 별도로 설치할 필요 없이 빌드됐다. 실제 버전은 9절에 기록한다.

### 소스 배치

일반적인 새 프로젝트라면 src layout도 선택할 수 있지만, 이 작업은 기존 경로와 import를 유지해야 한다.
따라서 상위 `ai-pjt/`를 자동 탐색하지 않고 **패키지 하나와 현재 디렉터리의 대응을 명시**한다.
[setuptools의 명시적 package-dir 지원](https://setuptools.pypa.io/en/latest/userguide/package_discovery.html)을
사용하며 실제 산출물의 경로는 2·3번에서 검사한다.

아래는 최초 2번에서 추가하고 `0.1.2`로 갱신한 [현재 빌드 설정](../pyproject.toml)이다.
설정 테스트에서 문서와 파일의 일치를 검사한다.

```toml
[build-system]
requires = ["setuptools==84.0.0"]
build-backend = "setuptools.build_meta"

[project]
name = "retirewise-retirement-simulation"
version = "0.1.2"
description = "Standalone retirement Monte Carlo and what-if simulation"
readme = "README.md"
requires-python = ">=3.13,<3.14"
dependencies = []

[tool.setuptools]
packages = ["retirement_simulation"]
package-dir = {retirement_simulation = "."}
include-package-data = false
```

설정 위치는 `ai-pjt/retirement_simulation/pyproject.toml`이다. 저장소/AI 루트에는 만들지 않는다.
패키지 자동 탐색으로 `tests`, `docs`, Agent·공용 도구가 새 import 패키지가 되지 않게 한다.
2번에서 실제 sdist→wheel 빌드와 파일/메타데이터 검사를 통과했다. 3번에서는 설치된 패키지의
import·실행을 별도로 확인했다. 패키징을 위해 계산 코드를 이동하거나 복제본을 제품 코드로 유지하지 않는다.

## 4. 산출물과 포함 파일

기본 산출물은 **wheel 1개와 sdist 1개**다. wheel은 설치용, sdist는 같은 소스에서 재빌드하기 위한
소스 배포본이다. 소스 배포본을 만든 뒤 거기에서 wheel을 빌드하는 흐름을 검증한다.
[PyPA 패키징 안내](https://packaging.python.org/en/latest/tutorials/packaging-projects/)를 참고한다.

| 대상 | 포함 | 제외/검사 |
|---|---|---|
| wheel | `retirement_simulation/`의 기존 루트 Python 18개와 정상 배포 메타데이터 | tests·docs 데이터·빌드 설정·BE/AI 앱·공통 정책·계정 정보·빌드 캐시 미포함 |
| sdist | 위 소스, 빌드 설정·표준 메타데이터, README, 모듈 내부 테스트·문서/합성 성능 JSON. setuptools가 생성한 `setup.cfg`·`PKG-INFO`·egg-info도 포함 | 저장소 다른 디렉터리·`.git`·환경 파일·고객 자료·venv·캐시·기존 산출물 미포함 |
| 검증 기록 | 소스 커밋, 도구/환경, 파일 목록, 산출물 SHA-256, 설치/실행 결과 | 바이너리/압축 산출물 자체를 Git에 커밋하거나 원격에 게시하지 않음 |

wheel의 Python 목록에는 기존 샘플들과 개발용 `analysis_benchmark.py`도 포함한다. 공개 API로
export하지 않는다는 기존 경계는 유지한다. 테스트 helper를 사용하지 않고 실행되는 합성 CLI는
설치 후에도 사용할 수 있어야 한다. sdist 문서의 BE/FE 상대 링크는 원본 저장소 맥락의 링크다.
이를 해결하려고 BE/FE 소스를 sdist에 포함하지 않는다.

[MANIFEST.in](../MANIFEST.in)으로 sdist의 추가 파일을 관리하고 wheel과 별도로 검사한다.
`include-package-data`의 기본값에 의존하지 않는다. 루트 자체가 패키지 디렉터리인 배치에서는
sdist에 넣은 docs/tests가 wheel로 유입되는지 특히 확인한다.
3번에서 sdist에 `tests/run_installation_checks.py`와 `tests/installed/check_package.py`를 명시적으로
추가했다. 설치 검증을 재실행하기 위한 개발용 파일이며 공개 API나 wheel의 모듈이 아니다.
[setuptools 파일 포함 규칙](https://setuptools.pypa.io/en/latest/userguide/miscellaneous.html)을 참고한다.

소스 복제본을 제품 코드로 유지하지 않는다. 검증을 위한 임시 소스 묶음/빌드 디렉터리는 허용하되,
작업 전용 임시 경로와 검증된 원천만 사용한다. 빌드 캐시가 필요하면 모듈 내부 ignore 또는 임시 경로로
격리하고 루트 `.gitignore`는 수정하지 않는다. 경로 탈출·외부 symlink·금지 파일 포함도 실패 조건이다.
모듈의 [.gitignore](../.gitignore)는 `/build/`·`/dist/`·`/*.egg-info/`와 Python 캐시만 제외한다.
이 ignore 파일 자체는 배포본에 넣지 않는다. 동일 입력이 항상 동일 압축 바이트가 된다는
재현 빌드 보장은 아직 하지 않는다.

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
| 2 — 완료 | 패키지 메타데이터·파일 포함 규칙·빌드 설정·기본 검증 | `build: 은퇴 시뮬레이션 패키지 빌드 설정` |
| 3 — 완료 | 저장소 밖 wheel 설치·import·합성 실행·실패 경계 검증 | `test: 은퇴 시뮬레이션 설치 경계 검증` |
| 4 — 완료 | 전체 회귀·산출물 검사·모듈 설치 가이드(BE 전달 보완은 별도 요청 시) | `docs: 은퇴 시뮬레이션 패키지 설치 가이드` |
| 5 | 최종 범위·master 충돌 확인 후 요청 시 푸시/MR | 필요한 변경만 별도 커밋. 원격 작업은 명시 요청 시 |

각 요청 단위는 문서화·검증 후 로컬 커밋한다. 현재 요청한 4번 다음의 5번 원격 작업은 함께 진행하지 않는다.
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

위는 0·1번 당시 기록이며 빌드·설치·실제 BE 이미지·서비스 인수를 포함하지 않는다.

## 9. 2번 빌드 설정·기본 검증 기록

2026-09-10, `0b3b643` 이후 같은 브랜치에서 진행했다. 아래는 **모듈 내부 개발 검증**이며
BE 전달 문서·설치 가이드나 실제 BE 설치 완료 기록이 아니다.

### 추가한 설정과 기본 테스트

- [pyproject.toml](../pyproject.toml): 배포 이름/정적 버전·Python 범위·무의존성·단일 패키지 대응.
- [MANIFEST.in](../MANIFEST.in): 모듈 루트 문서/설정, `tests/__init__.py`·`tests/test_*.py`,
  `docs/*.md`와 기존 합성 성능 JSON을 추가한다. 후속 인수인계에서 정책 JSON·입출력 예시·진단 결과와
  `tests/handoff_samples.py`를 명시적으로 추가했다. 폴더 전체 `graft`/상위 저장소 탐색은 하지 않는다.
  build/dist·숨김 파일·Python 캐시/백업은 제외한다. `.gitignore`와 생성한 압축 파일은 배포본에서 제외한다.
- [.gitignore](../.gitignore): 모듈 내부 빌드 부산물만 Git에서 제외한다. 루트 ignore는 수정하지 않는다.
- [설정 테스트](../tests/test_packaging_config.py) 10개: 이름/버전·Python 범위·의존성·빌드 백엔드·
  명시적 패키지/파일 포함·진입점/라이선스 경계·README·18개 원본 모듈 목록·문서 TOML 일치.
  표준 라이브러리만 사용하며 산출물 설치/import 검증을 대신하지 않는다.

### 빌드 환경과 재실행

기존 `retirewise-ai`와 분리한 `/tmp` 전용 venv를 만들었다. CPython 3.13.15,
pip 26.2.1, build 1.6.0, setuptools 84.0.0, packaging 26.3, pyproject_hooks 1.2.0을 사용했다.
빌드 도구 확보에만 공식 PyPI를 사용했으며 프로젝트·고객 파일을 업로드하지 않았다.
계산 패키지를 어떤 환경에도 설치하지 않았고 BE의 Python 3.13.14 이미지는 검증하지 않았다.

저장소 루트에서 같은 도구 버전으로 재실행할 수 있다. 소스 사본은 검증용 일회성 사본이다.
실제 제품 소스를 별도 위치에 복제해 관리하는 방식이 아니다.

```bash
packaging_workdir="$(mktemp -d /tmp/retirement-packaging-build.XXXXXXXX)"
conda run --no-capture-output -n retirewise-ai python -B -m venv "$packaging_workdir/build-env"
"$packaging_workdir/build-env/bin/python" -I -B -m pip --isolated --disable-pip-version-check install --no-cache-dir --index-url https://pypi.org/simple "build==1.6.0" "setuptools==84.0.0" "packaging==26.3" "pyproject_hooks==1.2.0"
cp -a -- ai-pjt/retirement_simulation "$packaging_workdir/source"
"$packaging_workdir/build-env/bin/python" -I -B -m build --no-isolation --outdir "$packaging_workdir/dist" "$packaging_workdir/source"
```

`--no-isolation`은 위에서 이미 준비한 **전용 build-env**를 사용한다는 의미다. 공용 환경에서
같은 옵션을 실행했다는 뜻이 아니다. build의 기본 실행으로 먼저 sdist를 만들고, Git/상위 AI 소스가
없는 sdist 추출본에서 wheel을 빌드했다. [build 공식 안내](https://build.pypa.io/en/stable/)를 참고한다.
제외 대상이 없는 깨끗한 소스에서는 MANIFEST의 `no previously-included ...` 경고가 나올 수 있다.
실패를 무시한 것이 아니라 최종 종료 코드와 실제 산출물 목록을 별도로 검사했다.

### 확인한 산출물

| 산출물 | 기본 검증 결과 |
|---|---|
| `retirewise_retirement_simulation-0.1.0-py3-none-any.whl` | 파일 22개 = 기존 Python 18개 + dist-info 4개. docs/tests/빌드 설정/다른 앱 미포함 |
| `retirewise_retirement_simulation-0.1.0.tar.gz` | 파일 82개 = Python 18개 + tests 26개 + docs 29개 + README/설정 3개 + 생성 메타데이터 6개 |

- 두 배포본의 포함 파일을 예상 목록과 대조했고, 포함된 소스/테스트/문서가 빌드 입력과 byte 단위로 일치했다.
- 메타데이터의 이름·버전·`Requires-Python`·Markdown README, 런타임 `Requires-Dist` 부재를 확인했다.
  `py3-none-any`는 순수 Python wheel 형식이며 Python 버전 제한과 실제 OS 검증 범위를 없애는 표시가 아니다.
- wheel CRC·전체 RECORD의 SHA-256/크기, 압축 파일 SHA-256, 중복 경로·경로 탈출·symlink/hardlink 부재를 확인했다.
  실행별 압축 파일 해시는 로컬 검사 출력에 기록했다. 해시를 포함한 문서를 다시 sdist에 넣는 순환을 피하며,
  압축 파일 자체와 검증용 임시 환경은 Git에 커밋하지 않는다.
- 일반·`-S` 실행에서 기존 620개 + 신규 설정 10개, 총 **630개** 회귀 테스트가 통과했다.
- Git의 `check-ignore --no-index`로 모듈 내부 build/dist/egg-info/캐시 제외 규칙을 확인했다.

설정 테스트만 재실행하려면 저장소 루트에서 다음 명령을 사용한다.

```bash
conda run --no-capture-output -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -p test_packaging_config.py -v
```

**2번 종료 당시 다음 작업은 3번:** 깨끗한 설치 환경에 wheel을 일반 설치하고 저장소 밖에서 import 위치·공개 함수·
합성 CLI·오류를 검증한다. 이번 빌드 성공을 설치/실행·BE/FE 통합·운영 성능 검증 완료로 해석하지 않는다.
BE 전달 문서는 이번에 수정하지 않았으며 사용자 요청 전까지 보완·전송하지 않는다.

## 10. 3번 설치 경계 검증 기록

2026-09-10, `d1d8285` 이후 같은 브랜치에서 진행했다. 상세 검사·파일·재실행 방법·한계는
[설치 경계 검증](installation-validation.md)에 정리했다. 실제 BE 설치 가이드가 아니다.

- 새 venv에 신뢰하는 로컬 wheel만 `--no-index --no-deps --no-compile`로 일반 설치했다.
  설치 전 부재, 저장소 밖 `-I -B`의 실제 import/distribution 위치, 원본 해시와 무의존성을 확인했다.
- 독립 설치 검사 **14개**를 skip 없이 통과했다. 기존 Python 18개·공개 export 44개·합성 MC/What-if,
  전체 JSON·손계산·원본/RNG 보존·5개 CLI·오류/종료 코드까지 검증한다.
- 필수 모듈을 일회성 설치 안에서 임시 분리하면 새 프로세스의 import가 실패하고, 복구 후 성공했다.
  실제 소스를 `PYTHONPATH`에 두어도 우회하지 않는다. 사용 후 임시 설치 환경은 자동 정리한다.
- 소스 회귀는 기존 630개와 runner 방어 검사 13개, 총 **643개**다. 설치 14개와 별도 집계한다.
- wheel 파일은 22개로 동일하다. sdist에는 설치 검사 3개 파일과 문서 1개가 추가돼 총 86개다.
  기존 2번의 82개 산출물/630개 테스트 기록은 당시 결과로 유지한다.
- 런타임 Python 18개·공개 export·계산/가정/JSON 규칙·기존 성능 기록은 변경하지 않았다.
  기존 Miniforge·BE/FE/공용 설정·BE 전달 문서를 수정하지 않았다. 새 런타임/빌드 의존성도 없다.

3번 종료 당시 다음은 4번 최종 회귀·산출물 확인과 모듈 설치 가이드였다. BE 전달 보완은 브랜치 종료 후 별도 요청 시,
푸시/MR도 명시 요청 시에만 한다. 실제 BE 이미지/API·화면·운영 성능은 여전히 미검증이다.

## 11. 4번 최종 검증·모듈 설치 가이드

2026-09-10, `fde5cfc` 이후 같은 브랜치에서 Markdown만 추가/수정했다.
[모듈 설치 가이드](package-installation.md)에 커밋된 단일 소스 추출·전용 빌드 환경·산출물/해시 기록,
오프라인 일반 설치·import 위치·합성 CLI/공개 함수·오류 확인·임시 환경 정리 방법을 정리했다.

- 일반·`-S`·역순 소스 회귀 **643개**, 별도 wheel 설치 검사 **14개**를 재검증했다.
- 최종 wheel은 기존 Python 18개와 메타데이터 4개, **22개 파일**이다.
  sdist는 설치 가이드 1개가 추가된 **87개 파일**이며 기존 82/86개 기록은 당시 결과로 유지한다.
- 소스 내용·파일 포함/제외·이름/버전/Python 범위·무의존성·CRC/RECORD·압축 경로/링크를 확인했다.
  가이드의 일반 설치·import 위치·CLI·공개 호출 예시도 저장소 밖 새 환경에서 검증했다.
- 기존 3번과 같은 CPython 3.13.15·전용 빌드 도구 버전을 사용했다. 계산/테스트 Python·
  공개 export·계산/JSON 계약·기존 성능 원시 기록·BE 전달 문서·공용 환경/설정은 변경하지 않았다.
- 실행별 산출물 해시는 `dist/` 밖 빌드 보고서에 기록한다. 압축 파일/보고서는 커밋·게시하지 않는다.

다음은 5번 최종 범위·최신 master 확인 후 별도 요청한 푸시/MR이다. 이번에 원격 갱신·rebase·
푸시/MR·실제 BE 이미지/API·FE 연결을 진행했다는 뜻이 아니다. BE 전달 보완은 브랜치 종료 후 별도 요청 시 진행한다.

## 12. 0.1.1 패키지 갱신

2026-09-11, 기타 금융자산 매핑 수정 `84a67b5` 이후 별도 패키징 커밋이다.
정적 패키지 버전과 설치 runner/테스트를 `0.1.1`로 갱신하고 기존 설치본과 구분했다.
Python 지원 범위·빌드 도구·런타임 의존성·포함 규칙·계산 Python 18개는 그대로 유지한다.

- 소스 전체 **682개**, 실제 wheel 설치 **17개** 검증. 두 개수는 별개다.
- wheel **22개**, sdist **96개 일반 파일**. 정확한 포함 목록과 소스 내용·메타데이터·CRC/RECORD·해시 확인.
- 기타 혼합/전용 입력·미지원 분류 오류를 설치된 공개 API에서 검증한다. 옛 파일명/내부 버전은 설치 전에 거절한다.
- 현재 설치 명령과 전달본 보관은 [설치 가이드](package-installation.md), 실행 기록과 한계는
  [설치 경계 검증](installation-validation.md)에 있다. 최종 커밋과 실제 파일 해시는 별도 보고서로 고정한다.

기존 기록의 소스 해시·성능 JSON·0.1.0 파일명/개수는 역사적 결과로 유지했다.
실제 BE 설치·API 검증·기타 사전 차단 제거는 후속 연결 담당 작업이며 이 로컬 검증에 포함하지 않는다.

## 13. 0.1.2 패키지 갱신

2026-09-11, `eb693e1`의 비모델링 분류 확장 이후 별도 패키징 작업이다.
현재 버전·설치 명령·버전 방어 검사를 0.1.2로 갱신했다. 빌드 백엔드·Python 범위·무의존성·
명시적 단일 패키지·MANIFEST 규칙은 그대로 유지한다.

- 소스 **685개**, 별도 wheel 격리 설치 **18개** 통과. wheel 22개 파일, sdist 97개 일반 파일.
- 자유 문자열/오타 제외·텍스트 오류 구분·5종/ID 검증을 실제 설치된 공개 함수로 확인한다.
  0.1.0/0.1.1 파일명과 내부 버전은 설치 전에 거절한다.
- 최초 BE 대조는 45개 중 **44개 통과, 자유 입력의 모듈 오류 기대값 1개 실패**였다.
  후속 BE MR !80(`master=a2f8650`) 반영 후 **46개 전부 통과**해 충돌을 해결했다.
  모듈의 부분 분석 성공과 BE의 화면 안내 전 임시 차단을 구분하며 실제 차단은 유지한다.
- 최종 소스 커밋·파일 해시·설치 결과는 별도 보고서와 함께 Git 제외 경로에 보관한다.

검증 환경·결과·미완료 경계는 [설치 검증](installation-validation.md), 재실행은
[설치 가이드](package-installation.md), 입력/BE 후속 조치는 [변경 안내](unmodeled-financial-assets.md)를 따른다.
화면에서 제외 사실을 안내하기 전까지 BE의 임시 차단은 유지하며 패키징 성공만으로 이를 제거하지 않는다.
