# 은퇴 시뮬레이션 패키지 이름 변경

작성일: 2026-09-09. 비교 기준: 서비스 설계 문서 커밋 `f29b871`.

## 1. 결정과 브랜치 범위

기존 `retirement`를 `retirement_simulation`으로 변경한다. 현금흐름·몬테카를로·
What-if가 공통 입력과 계산 로직을 공유하므로 상위 패키지는 기능 전체를 표현하고,
내부 `monte_carlo.py`, `what_if.py`, `what_if_engine.py`는 역할별 이름을 유지한다.

서비스 연결을 준비하는 로컬 `docs/ai-retirement-service-contract` 브랜치에서
이름 변경을 별도 `refactor:` 커밋으로 분리한다. 기존 설계 커밋은 수정하거나 합치지
않는다. 브랜치 접두어와 별개로 이번 커밋의 실제 범위가 리팩토링임을 제목에 드러낸다.
푸시·MR·병합은 별도 요청 대상이다.

## 2. 변경 대응표

| 대상 | 이전 | 현재 |
|---|---|---|
| 소스 경로 | `ai-pjt/retirement/` | `ai-pjt/retirement_simulation/` |
| Python import | `from retirement import ...` | `from retirement_simulation import ...` |
| 고정 현금흐름 CLI | `python -m retirement.samples` | `python -m retirement_simulation.samples` |
| 몬테카를로 CLI | `python -m retirement.monte_carlo_samples` | `python -m retirement_simulation.monte_carlo_samples` |
| What-if CLI | `python -m retirement.what_if_samples` | `python -m retirement_simulation.what_if_samples` |
| 테스트 탐색 | `ai-pjt/retirement/tests` | `ai-pjt/retirement_simulation/tests` |
| mock patch/내부 테스트 import | `retirement.<module>` | `retirement_simulation.<module>` |
| 사용 문서 | 기존 명령·경로·패키지명 | 현재 명령·경로·패키지명과 유효한 상대 링크 |

레거시 import 별칭이나 CLI 호환 shim은 추가하지 않는다. 이전 명령과 import를
사용하는 개인 스크립트·노트북이 있다면 위 표대로 변경해야 한다. Python 객체의
`__module__`도 달라지므로 과거 Python pickle 객체의 호환성을 약속하지 않는다.
기존 서비스에서 이 패키지를 호출하는 코드는 저장소 검색으로 발견되지 않았다.

문서의 실행 예시는 현재 이름으로 갱신한다. 단, 이전 커밋 범위를 조회하는
`git log --oneline c139054..bc288ce -- ai-pjt/retirement`는 당시 경로를 그대로 사용한다.
과거 브랜치·커밋·MR 이름이나 이미 작성된 검증의 날짜/테스트 개수를 소급 변경하지 않는다.

## 3. 변경하지 않는 것

- 계산 공식, 분포, 난수 사용, 성공·고갈 판정, 금액/비율/기간의 의미.
- 공개 함수/모델 이름과 인자, 결과 필드, `__all__`, 기본값, 제한값.
- `retirement_age`, `retirement_expenses` 같은 도메인 필드.
- 모델 버전 `retirement-mc-v1`, `retirement-what-if-v1`.
- 샘플 데이터·옵션·계산 출력. 출력 중 패키지명을 안내하는 문자열만 변경한다.
- BE·FE·Agent·컨텍스트·도구 등록·의존성·Docker·Compose·CI·DB.

기존 AI 컨텍스트 테스트의 `retirement.date`, FE mock의 `retirement` 값은 이
Python 패키지 참조가 아니므로 변경하지 않는다. 패키지 위치는 여전히 `ai-pjt/`이며,
BE로 소스를 옮기거나 BE에서 import할 수 있게 배포하는 작업은 하지 않는다.

현재 AI Dockerfile은 `ai-pjt/` 전체를 복사하므로 새 경로도 빌드 컨텍스트에 포함된다.
실제 컨테이너 빌드/배포 성공을 뜻하지 않으며 BE 직접 실행에 필요한 패키징은
[서비스 연결 설계](service-integration-design.md)의 미확정 결정으로 남는다.

## 4. 검증 방법

저장소 루트에서 일반 실행과 site-packages 없는 실행을 모두 확인한다.

```bash
conda run -n retirewise-ai python -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
conda run -n retirewise-ai python -S -B -m unittest discover -s ai-pjt/retirement_simulation/tests -q
```

`ai-pjt/`에서 세 CLI의 기본 실행·도움말·상세 출력을 확인한다.

```bash
conda run -n retirewise-ai python -S -B -m retirement_simulation.samples
conda run -n retirewise-ai python -S -B -m retirement_simulation.monte_carlo_samples
conda run -n retirewise-ai python -S -B -m retirement_simulation.what_if_samples
```

각 명령의 `--help`, `--details`도 같은 방식으로 실행한다. 이름 변경 전 9개 실행의
표준 출력에서 패키지 안내 문자열만 새 이름으로 정규화한 SHA-256을 보관하고,
변경 후 같은 실행의 종료 코드·표준 오류·출력 해시를 비교한다. 실제 고객 데이터는
사용하지 않으며 이 비교를 운영 성능 측정이나 시장 모델 검증으로 해석하지 않는다.
도움말의 경우 긴 패키지 이름에 따른 argparse 자동 줄바꿈을 별도로 확인하고,
공백을 제외한 내용도 비교한다.

추가로 기존 Python 소스의 패키지 참조만 정규화한 AST와 이동 후 AST를 비교하고,
공통 엔진 7개 파일은 바이트 단위로 비교한다. [패키지 경계 테스트](../tests/test_package.py)는
공개 export·모듈 경로·모델 버전·외부 애플리케이션 의존 없는 import를 검증한다.
모든 모듈 문서의 로컬 링크와 Python 예시도 새 이름으로 검증한다.

이전 경로에 남은 Git 비추적 Python 바이트코드 캐시는 작업용 임시 디렉터리로
옮겨 보존했다. 이전 디렉터리가 빈 namespace 패키지로 발견되는 것을 방지하며,
캐시를 새 패키지의 실행 근거로 재사용하지 않는다.

## 5. 검증 결과

Miniforge `retirewise-ai`, Python 3.13.15에서 확인했다.

| 검증 | 결과 |
|---|---|
| 파일 이동 | 기존 추적 파일 38개를 새 경로로 이동, 누락 없음 |
| 계산 코드 보존 | 핵심 파일 7개 바이트 일치, 기존 Python 파일 23개가 패키지 참조 정규화 후 AST 일치 |
| 단위 테스트 | 기존 300개 + 패키지 경계 4개 = 304개. 일반·`-S`·역순 실행 모두 통과 |
| CLI 기본/상세 6개 실행 | 안내의 패키지명만 정규화하면 변경 전후 stdout SHA-256·종료 코드·stderr 일치 |
| CLI 도움말 3개 실행 | 고정 현금흐름은 정규화 후 해시 일치. MC/What-if는 자동 줄바꿈 외 내용 동일, 옵션 변경 없음 |
| 문서 | 로컬 링크 156개(anchor 포함) 유효, Python 예시 6개 컴파일·실행 통과, 서비스 JSON 초안의 모델 변환 검증 통과 |
| 잔여 참조 | 실행용 옛 import·patch·CLI·테스트 경로 없음. 변경 안내/과거 Git 조회와 무관한 도메인 값만 보존 |

소스의 공개 export/모델·함수명은 그대로이며 패키지 경로만 변경됐다. 새 API,
실데이터 어댑터, 서비스 연결 또는 배포를 수행한 결과로 해석하지 않는다.
