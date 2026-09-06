# Backend runtime

이 문서는 P02(D2 Backend 실행환경)의 설치·컨테이너·환경변수·migration·readiness와 후속 담당자 인수 정보를 기록한다. 외부 HTTP·AI 계약은 runtime schema와 contract test로 검증하며, 이 문서는 해당 계약을 변경하지 않는다.

## 실행 기준

- Python: 3.13.14
- Container port: 8000
- Application command: `gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2`
- Required runtime dependencies: PostgreSQL, Redis
- Migration command: `python manage.py migrate --noinput`

`requirements.txt`는 직접 의존성의 호환 범위를 사람이 관리한다. 현재 Django·Django REST Framework·Redis의 기존 minor line을 유지하면서 security·bugfix patch를 허용한다. `requirements.lock`은 Python 3.13/Linux에서 해석한 직접·전이 의존성 전체와 배포 파일 hash를 고정하며 Docker와 CI가 설치한다.

lock을 갱신할 때는 저장소 루트에서 다음 명령을 실행하고 unit test와 clean image build를 다시 수행한다. `--exclude-newer` 날짜는 의도적으로 의존성 기준 시점을 올릴 때만 변경한다.

```bash
uv pip compile \
  --python-version 3.13 \
  --python-platform x86_64-unknown-linux-gnu \
  --generate-hashes \
  --exclude-newer 2026-09-03T00:00:00Z \
  --output-file backend-pjt/requirements.lock \
  backend-pjt/requirements.txt
```

## 환경변수

직접 실행할 때는 `.env.example`을 `.env`로 복사한 뒤 실제 값은 secret store나 로컬 비추적 파일에서 주입한다. `.env.example`의 `change-me`는 실행 가능한 기본 credential이 아니다.

| 분류 | 변수 | 현재 판정 |
|---|---|---|
| startup 필수 | `DJANGO_SECRET_KEY` | 빈 값·placeholder를 startup에서 거부하고 Django `SECRET_KEY`로 사용 |
| 현재 사용 | `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` | 명시적으로 파싱하고 안전한 기본값 유지 |
| 현재 사용 | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` | Django `DATABASES`의 단일 입력 |
| 현재 필수 | `REDIS_URL` | AI request 상태 저장과 readiness가 사용하는 Redis URL |
| 현재 사용 | `AI_SERVICE_URL`, `AI_CALLBACK_URL`, `AI_REQUEST_TTL_SECONDS`, `AI_SERVICE_TIMEOUT_SECONDS` | Backend의 AI 호출·callback 설정 |
| startup 필수 | `AI_SERVICE_BEARER_TOKEN` | 빈 값·placeholder 거부. 양방향 서비스 인증 연결은 후속 작업 범위 |
| legacy | `AI_CALLBACK_SECRET` | 현재 선택값. v2 bearer 전환 뒤 제거 후보 |
| readiness | `READINESS_HOST`, `READINESS_PORT`, `READINESS_TIMEOUT_SECONDS` | 기본값은 컨테이너 내부 `127.0.0.1:8000`, 2초 |

`config/settings.py`는 PostgreSQL과 Django secret/debug/host 설정을 환경변수에서 읽고,
필수 secret과 boolean·숫자·host 목록을 startup에서 검증한다. host networking이나
하드코딩 credential로 이 경계를 우회하지 않는다.

## Container build와 기동

Backend 디렉터리를 build context로 사용한다.

```bash
docker build -t retirewise-backend:d2 backend-pjt
```

migration은 각 application replica의 startup에서 실행하지 않는다. Compose는 PostgreSQL이
healthy가 된 뒤 아래 one-shot 명령이 성공해야 Backend application을 시작하도록 구성한다.

```bash
docker run --rm \
  --env-file backend-pjt/.env \
  --network de_net \
  retirewise-backend:d2 \
  python manage.py migrate --noinput
```

Backend 포트는 기본 배포에서 Frontend proxy 또는 내부 서비스에만 공개한다. 로컬 smoke test에서만 loopback에 임시 공개한다.

```bash
docker run --rm \
  --env-file backend-pjt/.env \
  --network de_net \
  -p 127.0.0.1:8000:8000 \
  retirewise-backend:d2
```

## Liveness와 readiness

현재 Backend에는 전용 HTTP health endpoint가 없으므로 `readiness.py`는 다음 순서로 판정한다.

1. 컨테이너 내부 WSGI port가 TCP 연결을 수락한다.
2. Django가 실제 사용하는 PostgreSQL connection에서 `SELECT 1`이 성공한다.
3. Django migration graph에 미적용 migration이 없다.
4. 현재 AI request 상태 저장소인 Redis가 `PING`에 성공한다.

성공하면 `ready`와 exit 0, 실패하면 예외 종류만 출력하고 exit 1을 반환한다. DSN·credential·원격 URL이 오류 출력에 포함되지 않도록 예외 메시지는 출력하지 않는다. Dockerfile의 `HEALTHCHECK`가 이 결과를 `healthy` 또는 `unhealthy`로 변환한다.

AI service와 callback은 Backend 자체의 필수 저장 의존성이 아니므로 readiness에서 호출하지 않는다. 이들을 readiness에 넣으면 AI 장애가 인증·상담 데이터 조회 같은 Backend 전체 트래픽까지 제거할 수 있다. 서비스 간 호출은 I06 smoke test와 운영 지표로 확인한다.

readiness unit test:

```bash
python3 -m unittest discover -s backend-pjt/tests -p 'test_*.py'
```

## P03·P04 인수 사항

P03 Compose 담당자는 다음 조건을 사용한다.

- build context: `./backend-pjt`
- container port: 8000, 기본 host publish 없음
- service DNS 예시: `backend`, `postgres`, `redis`
- PostgreSQL과 Redis가 먼저 healthy가 되도록 dependency condition 설정
- PostgreSQL healthy 뒤 migration one-shot 성공, 그 뒤 Backend application 기동
- Dockerfile healthcheck를 덮어쓴다면 동일한 `python readiness.py` 조건 유지
- application replica마다 migration을 중복 실행하지 않음

P04 clean-start 담당자는 빈 환경에서 lock 설치 또는 image build, PostgreSQL·Redis 기동, migration, Backend `healthy`, 종료 순서를 재현하고 다음 실패도 증거로 남긴다.

- PostgreSQL credential·host 누락 또는 잘못된 값
- PostgreSQL 연결 중단과 미적용 migration
- Redis 연결 중단
- WSGI process 중단
- 0 이하·10초 초과·숫자가 아닌 readiness timeout과 잘못된 port
- 빈 `DJANGO_SECRET_KEY`와 `AI_SERVICE_BEARER_TOKEN`의 startup 거부

## 후속 Backend lifecycle 인수 사항

G1은 환경 기반 Django·PostgreSQL 설정과 실패 폐쇄형 parsing을 반영했다. 후속 Backend
lifecycle 구현에는 다음 항목이 남아 있다.

1. PostgreSQL connect timeout을 readiness timeout과 일관되게 제한한다.
2. `AI_SERVICE_BEARER_TOKEN`을 실제 Backend→AI request와 AI→Backend callback 인증에 연결한다.
3. legacy `AI_CALLBACK_SECRET` 선택 동작을 v2 bearer 계약으로 교체하고 이행 뒤 환경 예시에서 제거한다.
6. startup/config/callback 오류에 password, token, 전체 DSN을 남기지 않는다.
7. `/health` 또는 `/ready` endpoint를 추가한다면 process·dependency 의미를 분리하고 `readiness.py`와 서로 다른 판정을 만들지 않는다.

I06 반영 전까지 정상 다중 컨테이너 PostgreSQL 연결, secret 기본 거부, 전체 G1 판정은 완료로 간주하지 않는다.

## P02 검증 결과

2026-09-04에 Docker client·server 29.7.2에서 다음 범위를 직접 검증했다.

- `python3 -m unittest discover -s backend-pjt/tests -p 'test_*.py'`: readiness unit test 10개 성공
- Python 3.13/Linux 기준 직접·전이 의존성 18개와 hash lock 생성 성공
- `docker build --no-cache -t retirewise-backend:d2 backend-pjt`: clean build와 `pip --require-hashes` 설치 성공
- image runtime: Python `3.13.14`, user `app`/effective UID `999`
- image config: Gunicorn bind `0.0.0.0:8000`, `python readiness.py` healthcheck
- `python manage.py check`: 문제 없음
- 잠긴 image에서 기존 `consulting.tests` 11개 성공
- 의존 서비스·WSGI process가 없는 일회성 probe: `not ready: ConnectionRefusedError`, exit 1, 연결 정보 출력 없음
- branch 밖의 임시 settings overlay와 격리 network에서 PostgreSQL·Redis `healthy`, 전체 migration one-shot, Gunicorn, Backend Docker `healthy` 확인
- 같은 격리 검증에서 Redis 중단 뒤 `not ready: ConnectionError`, exit 1, Redis URL 출력 없음

임시 overlay는 probe와 image 경계를 검증하기 위한 것으로 branch에 남기지 않았다. 실제 `config/settings.py`, P03 Compose service name·credential·dependency condition을 사용한 clean start와 Docker 연속 실패 뒤 `unhealthy` 전환은 I06·P03 준비 후 P04가 G1에서 다시 검증해야 한다.
