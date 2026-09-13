# 배포 운영 안내 (deploy/)

배포 담당: 고우정. 이 문서는 서버(EC2 `j15b109.p.ssafy.io`)를 처음부터 다시 세울 수 있는 수준을 목표로 한다.

## 구성 한눈에

```
브라우저 ──HTTPS 443──▶ 호스트 nginx(certbot) ──127.0.0.1:8080──▶ web 컨테이너(SPA + /api 프록시)
                                                                      └─/api─▶ backend :8000 ─▶ ai-pjt :8001
                                                                                     └─▶ postgres · redis
GitLab master 머지 ──▶ EC2 gitlab-runner(shell) ──▶ docker compose --env-file … -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

- 루트 `docker-compose.yml` — 앱 실행환경(팀 P03). 수정하지 않는다.
- `docker-compose.prod.yml` — 운영 override. `web` 서비스, `restart`, 로그 상한만 추가.
- 호스트에 열리는 포트는 22 / 80 / 443 만. 컨테이너 중 호스트에 바인딩되는 것은 `web`(127.0.0.1:8080) 하나.

## 환경변수 (.env / app.env)

**원칙: 로컬은 팀원 각자 한 번 생성하고 서버의 canonical 원문은 한 파일만 둔다. 값을 서로 공유하거나 Git·CI artifact·채팅·명령 출력에 복사하지 않는다.**

| 어디 | 파일 | 만드는 법 | 횟수 |
|---|---|---|---|
| 로컬 개발 | 저장소 루트 `.env` | `bash deploy/make-env.sh` | PC 당 1회 |
| 서버 | `/srv/s15p21b109/secrets/app.env` (root:gitlab-runner 640 — deploy 잡의 러너가 읽어야 한다) | `sudo bash deploy/make-env.sh --output /srv/s15p21b109/secrets/app.env` | 1회 |
| CI | 없음 — `.gitlab-ci.yml` 의 `ci-only-*` 더미 | 자동 | 매 실행 |

`make-env.sh` 는 `.env.example` 을 복사해 필수 5개를 채운다.

| 변수 | 출처 | 바꾸면 |
|---|---|---|
| `DJANGO_SECRET_KEY` | 무작위 생성 | 세션·JWT 전부 무효(전원 재로그인). 데이터 무손실 |
| `POSTGRES_PASSWORD` | 무작위 생성 | **볼륨이 이미 있으면 DB 는 옛 비밀번호 유지 → 접속 끊김.** 데이터는 남음. 절차: DB 에서 `ALTER USER retirewise PASSWORD '...'` → 파일 갱신 → 재기동. **`down -v` 금지** |
| `DATABASE_URL` | 위 비밀번호로 자동 조립 | POSTGRES_* 와 항상 일치시킨다 |
| `AI_SERVICE_BEARER_TOKEN` | 무작위 생성 | backend·ai 동시 재기동이면 문제 없음 |
| `AI_MASKING_PSEUDONYM_KEY` | 무작위 생성 | 가명화 결과가 전부 바뀐다 — 이전 관측 기록과 같은 사람을 같은 가명으로 더 이상 잇지 못한다. **다만 로그·트레이스에만 쓰이므로**(`core/observability_masking.py` 의 `APPLICATION_LOG`·`TRACE_EXPORT`) **서비스 동작·저장 데이터·API 응답에는 영향이 없다** — 재로그인도 필요 없다. 로그가 30MB(`json-file 10m×3`)에서 회전하고 집계 스택이 없어 실질 손실은 거의 없다. **단 장애 대응 중에는 바꾸지 않는다** — 추적 중이던 가명이 그 순간 끊긴다. `AI_SERVICE_BEARER_TOKEN` 과 다른 값이어야 하고 32바이트 이상이어야 한다 (`ai-pjt/core/config.py` `validate_runtime`) |
| `GOOGLE_API_KEY` | **GMS 키**를 직접 입력 (개인 Google 계정 키 아님 — 아래 절 참고) | 외부 회전 시만 |

- 스크립트는 파일이 이미 있으면 덮어쓰지 않는다(exit 2).
- 서버 canonical 원문은 `/srv/s15p21b109/secrets/app.env` 하나다. 변경 전 원문은 아래 절의 immutable snapshot으로 보존한다. 과거 `app.env.bak`은 새 절차의 복구 원천으로 사용하지 않는다.
- 누락 확인: `docker compose --env-file <파일> -f docker-compose.yml -f docker-compose.prod.yml config --quiet`
- 거부되는 값: `change-me`, `password`, `postgres`, `secret`, `ssafy` 등 (`backend-pjt/config/runtime_env.py`).

### 필수 변수를 새로 추가할 때 (2026-09-11 추가)

`docker-compose.yml` 에 `${VAR:?}` 를 새로 추가하는 MR 은 **저장소 파일만 고쳐서는 배포가 안 된다.** `make-env.sh` 는 기존 파일을 덮어쓰지 않으므로(exit 2) 서버 `app.env` 에는 그 변수가 생기지 않고, 다음 `master` 배포의 `deploy:production` 이 `deploy.sh` 1단계 사전 점검에서 멈춘다.

MR 체크리스트:

1. `.env.example` 과 해당 서비스의 `<서브프로젝트>/.env.example` 에 변수 추가
2. `deploy/make-env.sh` 에 생성 규칙 추가 (무작위 값이면 `openssl rand`)
3. `.gitlab-ci.yml` 에 `CI_*` 더미 변수 + 해당 test 잡의 `-e` 추가
4. 이 문서의 변수 표 갱신
5. **머지 전에 immutable snapshot을 만든 뒤 서버 `app.env` 에 줄 추가** (아래)

서버 갱신 — sudo 필요. 먼저 값 없는 식별자만 셸에 넣어 snapshot을 만들고, 값은 편집기에서 사람이 입력한다:

```bash
snapshot_id="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short HEAD)"
sudo bash deploy/f14-config-snapshot.sh create \
  --source /srv/s15p21b109/secrets/app.env \
  --snapshot-root /srv/s15p21b109/secrets/snapshots \
  --snapshot-id "$snapshot_id" \
  --candidate "$(git rev-parse HEAD)" \
  --recovery-owner root
sudoedit /srv/s15p21b109/secrets/app.env
```

`printf`, `sed -i`, 환경변수 export로 실제 값을 넣지 않는다. 셸 history·프로세스 목록·CI 로그에 평문이 남기 때문이다.

확인은 러너 체크아웃에서. 파일이 `root:gitlab-runner 640` 이라 `ubuntu` 로는 못 읽으니 `sudo` 를 붙인다:

```bash
sudo docker compose --env-file /srv/s15p21b109/secrets/app.env -f docker-compose.yml -f docker-compose.prod.yml config --quiet && echo OK
```

실측(2026-09-11): `AI_MASKING_PSEUDONYM_KEY` 가 사전 갱신 없이 머지돼 파이프라인 #189299 의 `deploy:production` 이 실패했다. 사전 점검이 빌드·기동 전에 멈추므로 운영 컨테이너는 그대로였고, 서버에 줄을 추가한 뒤 잡을 재실행해 복구했다. 같은 실패가 다시 나면 `deploy.sh` 가 누락된 변수 이름만 로그에 찍고 값은 출력하지 않는다.

### 운영 설정 immutable snapshot과 복구

`deploy/f14-config-snapshot.sh`가 설정 원문을 다루는 유일한 백업·복구 진입점이다.

| 대상 | 위치·권한 | 읽는 주체 | 보존 내용 |
|---|---|---|---|
| canonical 설정 | `/srv/s15p21b109/secrets/app.env`, `root:gitlab-runner 0640` | root와 배포 runner | 실제 운영 값. Compose `--env-file` 입력 |
| 변경 전 snapshot | `/srv/s15p21b109/secrets/snapshots/<UTC>-<commit>/`, 디렉터리 `root:root 0500`, `app.env`·`manifest.env` `root:root 0400`, 셋 모두 filesystem immutable `+i` | 복구 owner인 root만 | 당시 원문 1부와 값 없는 SHA-256·소유자·mode·candidate metadata |
| 리허설 보고서 | `/srv/s15p21b109/state/f14-rehearsals/<id>/report.json`, `root:root 0600` | root | snapshot ID·경로·hash·권한, DB/이미지 rollback 판정만. 설정 이름·값 없음 |
| Git 커밋·CI artifact | 저장소 | 팀 | 실행 명령, 위 metadata와 PASS/FAIL만. `app.env`·snapshot 원문은 절대 복사하지 않음 |

복구 권한 owner는 OS 계정 `root`, 실제 실행 담당은 이 문서 상단의 배포 담당 고우정이다. `gitlab-runner`는 canonical 파일만 읽을 수 있고 snapshot 디렉터리에는 접근할 수 없다. snapshot 생성은 동일 ID 덮어쓰기를 거부하고 복사 직후 SHA-256을 대조한 다음 `chattr +i`와 `lsattr`을 확인한다. 파일시스템이 immutable flag를 지원하지 않으면 생성 자체가 실패하므로 단순 `chmod` 백업을 성공으로 간주하지 않는다. snapshot은 자동 삭제하거나 교체하지 않는다.

복구할 때도 snapshot 원문을 `cat`, `grep`, diff로 출력하지 않는다. 먼저 현재 canonical 파일을 새 snapshot으로 보존하고, 복구할 ID를 사람이 경로와 manifest metadata로 확인한 뒤 정확히 같은 ID를 confirmation 인자로 준다.

```bash
rescue_id="$(date -u +%Y%m%dT%H%M%SZ)-pre-restore"
sudo bash deploy/f14-config-snapshot.sh create \
  --source /srv/s15p21b109/secrets/app.env \
  --snapshot-root /srv/s15p21b109/secrets/snapshots \
  --snapshot-id "$rescue_id" \
  --candidate "$(git rev-parse HEAD)" \
  --recovery-owner root

restore_id='<확인한-snapshot-id>'
sudo bash deploy/f14-config-snapshot.sh restore \
  --snapshot "/srv/s15p21b109/secrets/snapshots/$restore_id" \
  --target /srv/s15p21b109/secrets/app.env \
  --confirm-snapshot-id "$restore_id"

sudo docker compose --project-name s15p21b109 \
  --env-file /srv/s15p21b109/secrets/app.env \
  -f docker-compose.yml -f docker-compose.prod.yml config --quiet
sudo AI_REQUIRED=1 bash deploy/deploy.sh --no-build
```

복구 성공은 `app.env`를 눈으로 출력하는 것이 아니라 restore가 확인한 SHA-256, `compose config --quiet`, 서비스 health와 HTTP/API probe로 판단한다. snapshot의 `+i`는 읽기·복원을 막지 않으므로 해제하지 않는다.

## 서버 초기 세팅 (2026-09-07 수행 기록)

| 단계 | 내용 | 상태 |
|---|---|---|
| 접속 | `ssh -i J15B109T.pem ubuntu@j15b109.p.ssafy.io` (로컬 `~/.ssh/config` 에 `Host ssafy`) | 완료 |
| 스펙 | Ubuntu 24.04.4 · 4 vCPU · 15 GiB · 309 GB | 기록 |
| Docker | 공식 apt 저장소 → docker-ce 29.8 + compose v5.5. `ubuntu`, `gitlab-runner` 를 docker 그룹에 | 완료 |
| GitLab Runner | 공식 apt 저장소 → 19.3.1. 프로젝트 러너 등록(shell executor, 태그 `ssafy-ec2`, untagged 금지). 토큰은 `/etc/gitlab-runner/config.toml`(root) | 완료 |
| 호스트 nginx | apt nginx 1.24. 사이트 설정·HTTPS 는 S15P21B109-123 | 설치만 |
| secrets | `/srv/s15p21b109/secrets/` root:gitlab-runner 750, canonical `app.env` 640. 변경 전 snapshot은 `snapshots/<id>` root:root 0500/0400와 `+i`; 복구 owner root. 러너 그룹 읽기 허용은 canonical 파일에만 적용 | canonical 값 입력 완료(GOOGLE_API_KEY 제외), snapshot 절차는 F14-T05 |
| ufw | 22 만 개방 상태. 80/443 은 S15P21B109-123 에서 `sudo ufw allow 80/tcp`, `443/tcp` (SSH 세션 2개 유지) | 대기 |

### 서버 작업 금지 사항 (SSAFY 공지 — 위반 시 초기화 외 복구 불가)
- ufw 에서 22 차단, `authorized_keys` 삭제, `/home`·시스템 디렉터리 퍼미션 변경 금지
- DB·캐시 포트를 호스트에 노출하지 않는다 (compose 는 `expose` 만)
- 비밀값을 저장소·채팅에 올리지 않는다

### 러너 운영 메모
- shell executor 는 잡마다 같은 체크아웃(`/home/gitlab-runner/builds/...`)을 재사용한다. 컨테이너가 root 로 파일을 만들면 다음 잡의 `git clean` 이 실패한다 → CI 는 소스를 `:ro` 로 마운트하고, 쓰기가 필요한 컨테이너는 `--user $(id -u):$(id -g)` 로 실행한다.
- 오염됐을 때 복구: `sudo chown -R gitlab-runner:gitlab-runner /home/gitlab-runner/builds`
- 상태: `sudo gitlab-runner verify`, `systemctl status gitlab-runner`

## HTTPS · 호스트 nginx (S15P21B109-123, 2026-09-07 완료)

- 사이트 설정 사본: `deploy/nginx-host.conf` → 서버 `/etc/nginx/sites-available/s15p21b109` (`sites-enabled` 링크, `default` 해제)
  - `/api/` → `127.0.0.1:8081` **gateway**(루트 compose 의 nginx, SSE 튠은 그 conf 가 단일 기준) · `/` → `127.0.0.1:8080` **web**
  - 운영 `app.env`: `API_GATEWAY_PORT=127.0.0.1:8081`, `WEB_HOST_PORT=8080`
- 인증서: `certbot --nginx -d j15b109.p.ssafy.io --register-unsafely-without-email --agree-tos --redirect` (이메일 미등록 — 알림 대신 아래 점검)
  - 발급 2026-09-07, 만료 2026-12-06. 443 블록·80→443 리다이렉트는 certbot 이 사이트 파일에 추가
  - 자동 갱신: `certbot.timer`(하루 2회). 점검: `sudo certbot renew --dry-run`, `sudo certbot certificates`, `systemctl list-timers certbot.timer`
- 서버 conf 를 바꿀 때: 저장소 `deploy/nginx-host.conf` 를 먼저 고치고 MR → 서버에 복사 → `sudo nginx -t && sudo systemctl reload nginx`. certbot 이 넣은 443 블록은 사본에 없으므로 복사 시 덮어쓰지 말고 `/api/`·`/` location 만 반영한다

## 배포 스크립트 (S15P21B109-124)

`deploy/deploy.sh` — CI deploy 잡이 러너 체크아웃에서 실행한다. 사람이 서버에서 직접 실행해도 동일.

```
deploy/deploy.sh                       # /srv/s15p21b109/secrets/app.env, 빌드 포함
deploy/deploy.sh --no-build            # 재기동만
deploy/deploy.sh --ai-required          # ai-pjt healthy 를 성공 조건에 포함 (= AI_REQUIRED=1)
AI_REQUIRED=0 deploy/deploy.sh         # AI 가 깨진 상황에서 서빙만 급히 배포
ENV_FILE=.env deploy/deploy.sh         # 로컬 재현 (STATE_DIR 도 바꾸면 서버 상태 파일과 분리)
```

| 단계 | 내용 |
|---|---|
| 사전 점검 | env 파일 읽기 가능, `compose config --quiet` |
| 롤백 대상 확인 | 직전 성공 배포가 남긴 유지 태그 `<프로젝트>-<서비스>:last-good` (web·backend·ai-pjt·migrate). 태그가 없는 서비스는 **부트스트랩 승격** — 1순위 그 서비스가 기동 중인 이미지, 없으면 2순위 `<이름>:latest`(컨테이너가 참조하는 이미지 레코드가 사라진 경우). 승격할 이미지도 없으면 그 서비스는 이번 배포가 실패해도 복원되지 않으며 로그에 명시된다. 첫 배포면 대상 없음 → 실패 시 중단만 |
| 기동 | `compose build --pull` → `up -d --remove-orphans` → **`restart gateway`**. migrate one-shot → backend 순서는 compose `depends_on` 이 보장. gateway 재시작 배경: 원래 gateway nginx 의 upstream 이 고정(`server backend:8000`)이라 backend 가 재생성돼 IP 가 바뀌면 옛 IP 를 물고 `/api` 가 502 가 됐다(2026-09-08 첫 자동 배포에서 발생, S15P21B109-127). S15P21B109-128 에서 upstream 을 `resolve` 방식(10s 주기 재조회)으로 바꿔 배포 외 경로(수동 `up`, 크래시 후 재생성)에서도 자동 회복하도록 했고, 재시작은 즉시 반영용 안전망으로 유지한다. web 의 nginx 는 처음부터 `resolver` 방식 |
| 헬스체크 | 최대 150s: `web`·`backend` 컨테이너 healthy **and** `GET http://127.0.0.1:8080/` 200 **and** `GET http://127.0.0.1:8080/api/consulting/v1/consultations/` 가 5xx·000 이 아님(host→web→gateway→backend 전 구간 검사. 로그인 필요 경로라 **401 이 정상**. `--api-health-url` 로 변경). **ai-pjt 는 `AI_REQUIRED=1`(운영 기본) 이면 같은 대기 조건에 포함하고, `0` 이면 상태만 경고**(`--ai-required` 로 필수 전환) |
| 성공 | 방금 성공한 이미지에 `:last-good` 을 다시 붙인다(다음 롤백 대상). 태그가 옮겨가면 직전 세대는 dangling 이 되어 뒤따르는 `docker image prune -f` 가 정리한다 → 서비스당 한 세대만 남는다. `/srv/s15p21b109/state/last_good_images` 는 사람이 읽는 기록용이고 롤백은 이 파일을 읽지 않는다. exit 0 |
| 실패 | backend 로그 20줄 출력 → `:last-good` 을 `:latest` 로 재태그 → `up -d --no-build` → `restart gateway` → 10s 뒤 롤백 후 상태(web·backend·ai-pjt·http·api) 로그 → exit 1 (파이프라인 failed). 롤백 대상이 없으면 컨테이너를 그대로 두고 중단만 한다 — 그때는 **방금 배포한 이미지가 운영에 남는다** |

- 하지 않는 것: 볼륨 삭제(`-v`), DB 스키마 롤백. 마이그레이션은 앞으로만 간다. 스키마를 되돌려야 하면 사람이 한다
- 롤백은 "이미지" 롤백이다. 직전 배포와 이미지가 같으면(코드 변경 없는 재배포) 복원 로그가 없는 것이 정상
- **왜 이미지 ID 가 아니라 유지 태그인가** (2026-09-10 교체): 원래는 배포 전에 기동 중인 이미지 ID 를 기억해 두고 실패 시 그 ID 를 다시 태그하는 방식이었다. 그 이미지는 태그를 잃은 채 남아 있어야 복원되는데, **성공 배포 끝의 `docker image prune -f` 가 정확히 그런 이미지를 지운다.** 2026-09-10 저녁 파이프라인 #188176 에서 web·backend·ai-pjt·migrate **4개 전부 "복원 불가"** 로 롤백이 실패했고 깨진 빌드가 운영에 남았다. 태그가 붙은 이미지는 dangling 이 아니라서 prune 대상에서 빠진다
- 유지 태그 이름은 `ROLLBACK_TAG` 로 바꿀 수 있다(기본 `last-good`). 이미지 이름이 `<프로젝트>-<서비스>` 라서 `deploy.sh` 는 compose 프로젝트 이름을 `s15p21b109` 로 **고정**한다 — 체크아웃 디렉터리 이름에 기대지 않는다
- 검증 기록(2026-09-07, 로컬) — **이 검증은 prune 이 한 번도 돌지 않은 상태에서 통과했고, 그래서 위 결함을 놓쳤다**: 성공 경로 exit 0 · last_good 4개 기록 / 닫힌 포트를 헬스 URL 로 준 실패 경로 → 롤백 분기 → exit 1, 서비스 유지 / **E2E 롤백**: `s15p21b109-web` 태그를 CMD 없는 alpine 으로 바꿔 배포 → web `restarting` 감지 → 4개 서비스 태그를 직전 ID 로 복원 → `up` → web healthy·8080 200, exit 1
- 검증 기록(2026-09-10, 운영): 유지 태그 방식으로 배포 3회(#188209 · #188248 · #188256) 성공. 마지막 잡에서 `web backend ai-pjt migrate` 4개 전부 `:last-good` 승격 확인
- F14-T05부터 `deploy/f14-rollback-rehearsal.sh`가 동일한 production Compose project에서 의도적 실패와 `:last-good` 복원을 실행한다. 아래 리허설 절차는 실행 전 drift를 거부하고, 실패/중단 trap에서도 태그 복원과 전용 DB schema 정리를 다시 수행한다.

### F14 운영 rollback 리허설

이 명령은 실제 운영 서비스에서 실패 rollback 분기를 밟으므로 배포 담당이 SSH 세션에서 실행한다. 시작 전에 `web`, `backend`, `ai-pjt`, `migrate`의 실행 이미지가 각 `:last-good`과 정확히 같고 web·API가 정상인지 검사하며, 하나라도 다르면 아무것도 바꾸지 않고 중단한다.

```bash
sudo bash deploy/f14-rollback-rehearsal.sh \
  --candidate "$(git rev-parse HEAD)" \
  --timeout 30
```

실행 순서는 다음과 같다.

1. `app.env`의 owner/group/mode가 `root:gitlab-runner 0640`인지 확인하고 위 immutable snapshot을 먼저 만든다.
2. 운영 PostgreSQL 안에 `f14_rehearsal_<token>` 전용 schema와 합성 marker 한 건을 만든다. 그 schema만 `pg_dump -Fc`하고 marker 변경·schema 삭제 후 `pg_restore`하여 원래 marker가 정확히 돌아오는지 확인한다. 고객 schema·행은 읽거나 dump하거나 변경하지 않으며, 끝나면 전용 schema를 삭제한다.
3. 실행 중인 PostgreSQL 이미지 ID를 `s15p21b109-web:latest`에 잠시 태그해 web 기동 실패를 만들고, 닫힌 health URL로 기존 `deploy/deploy.sh --no-build`를 호출한다. `deploy.sh`가 non-zero로 끝나고 네 서비스가 모두 기존 `:last-good` image ID로 돌아왔는지 확인한다.
4. 정상 태그를 한 번 더 복원하고 web·backend·ai-pjt health, HTTP 200, API non-5xx를 최대 90초 확인한다. canonical 설정 hash가 실행 전과 같은지, immutable snapshot이 그대로인지 재검증한다.
5. 값 없는 결과만 `/srv/s15p21b109/state/f14-rehearsals/<id>/report.json`에 0600으로 쓴다. 합성 schema dump는 같은 root 전용 디렉터리에 남으며 고객 데이터는 포함하지 않는다.

서비스가 의도적으로 내려가는 구간은 `TIMEOUT + rollback 재기동` 동안 발생할 수 있다. 따라서 트래픽이 가장 적은 시간에 실행하고, 실행 중 다른 deploy 잡이 겹치지 않도록 GitLab `production` resource group이 비어 있는지 먼저 확인한다. 스크립트는 고객 데이터 rollback, volume 삭제, 실제 설정 변경, 외부 LLM 호출을 하지 않는다.

검증 기록(2026-09-12, 운영 서버): candidate `da363e6`의 하네스를 배포 checkout
`2cc81da`에서 실행했다. 변경 전 snapshot
`20260912T025811Z-da363e6b9d0d`은 `/srv/s15p21b109/secrets/snapshots/`에
`root:root` 0500/0400과 디렉터리·두 파일의 `+i`로 보존됐고, 원본 설정 SHA-256은
리허설 전후가 같았다. 전용 합성 schema의 dump→변경→restore가 exact match로
통과하고 잔존 schema는 0개였다. 의도적 web 실패 뒤 web·backend·ai-pjt·migrate가
모두 기존 `:last-good` image ID로 복구됐으며 최종 web 200, API 401, 세 애플리케이션
health가 `healthy`였다. UFW 22/80/443 규칙도 바뀌지 않았다. 운영 checkout과 후보의
calculation policy는 모두 `2026-09-07.v1`, SHA-256
`b50f9e854185c5a6555ebac153e2fec2a309ddbbc86cb946080d3a0df3a70df4`였다. 설정값 없는
Git 증거는 `ai-pjt/evals/artifacts/f14-rollback-rehearsal.json`이 소유한다.

## deploy 스테이지 — master 자동 배포 (S15P21B109-125)

`.gitlab-ci.yml` 의 `deploy:production` 잡. master 파이프라인에서 test 스테이지가 통과하면 러너 체크아웃에서 `bash deploy/deploy.sh` 를 실행한다. 러너가 곧 배포 서버라서 SSH 키·이미지 레지스트리·CI Variables 가 없다.

| 항목 | 값 |
|---|---|
| 트리거 | `master` 브랜치 파이프라인, test 통과 후 자동. MR 파이프라인에서는 실행하지 않음 |
| 실행 위치 | 러너 체크아웃 `/home/gitlab-runner/builds/<id>/0/s15-fintech-finance-sub1/S15P21B109` (러너 사용자 `gitlab-runner`, docker 그룹, sudo 없음) |
| 비밀값 | `/srv/s15p21b109/secrets/app.env` — root:gitlab-runner 640. deploy.sh가 `--env-file`로 읽는다. 변경 전 원문은 root 전용 immutable snapshot에만 보존 |
| 상태 파일 | `/srv/s15p21b109/state/last_good_images` (디렉터리 gitlab-runner 소유) |
| 동시성 | `resource_group: production` — 배포 잡은 한 번에 하나. `interruptible: false` — 새 커밋이 와도 진행 중 배포는 끊지 않음 |
| 환경 | GitLab Environment `production`, URL `https://j15b109.p.ssafy.io` (Deployments → Environments 에서 배포 이력 확인) |
| 실패 | deploy.sh 가 롤백 후 exit 1 → 잡 failed. 파이프라인 알림은 S15P21B109-126 |

- **재배포(코드 변경 없음, env 만 바뀐 경우)**: master 최신 파이프라인의 `deploy:production` 잡을 GitLab 에서 Retry. 또는 서버에서 러너 체크아웃 디렉터리로 가서 `bash deploy/deploy.sh --no-build`
- **특정 서비스만 재기동·강제 재생성** (예: LLM 키 입력·회전 후의 ai-pjt): 러너 체크아웃에서 아래 형태로 실행한다

  ```
  sudo bash -c 'cd /home/gitlab-runner/builds/<id>/0/s15-fintech-finance-sub1/S15P21B109 && docker compose --project-name s15p21b109 --env-file /srv/s15p21b109/secrets/app.env -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate --no-build ai-pjt'
  ```

  - **`--project-name s15p21b109` 을 빼면 안 된다.** compose 는 프로젝트 이름을 현재 디렉터리 이름에서 정하므로, 홈에서 실행하면 프로젝트 `ubuntu` 로 운영 스택과 별개인 중복 컨테이너를 만들려 든다(`web` 이 `127.0.0.1:8080` 을 물어 포트 충돌). `deploy.sh` 가 이름을 고정하는 것과 같은 이유다
  - `app.env` 가 root:gitlab-runner 640, `/home/gitlab-runner` 가 0700 이라 `ubuntu` 계정은 `cd` 까지 `sudo` 안에서 해야 한다. deploy 잡을 Retry 하면 러너 권한으로 돌아 sudo 가 필요 없다
  - **`web`·`backend` 를 대상으로 할 때는 `migrate` 를 함께 지정한다** (`up -d --force-recreate --no-build migrate backend web`). `backend` 가 `migrate: service_completed_successfully` 에 의존해서 compose 가 기존 migrate 컨테이너를 *재생성이 아니라 기동*하려 드는데, 그 컨테이너가 참조하는 이미지가 사라진 상태면 `No such image` 로 명령 전체가 실패한다 (2026-09-11 실측)
  - `--no-build` 를 붙이면 기존 `:latest` 를 그대로 써서 재빌드를 피하고, 이미지가 없을 때 조용히 빌드로 넘어가지 않고 즉시 실패한다. root 로 실행하므로 러너 체크아웃에 root 소유 파일을 남기지 않는 편이 안전하다(다음 잡의 `git clean` 이 깨진다)
- **주의**: `master` 는 보호 브랜치이고(push·merge 는 Maintainer, force push 금지) **"파이프라인 통과 시 머지" 조건도 2026-09-10 에 켰다**(`only_allow_merge_if_pipeline_succeeds=true` — 아래 「master 머지 조건」 절). 빨간불 MR 이 막히는 것과 별개로, **테스트가 잡지 못하는 결함은 그대로 배포된다.** `deploy:production` 은 `rules` 상 MR 파이프라인에 존재하지 않으므로 빌드·헬스체크·롤백 전 과정은 머지 후에야 처음 실행된다
- 검증 기록(2026-09-08, 서버): 기능 브랜치에서 `when: manual` 로 실행. 1차(파이프라인 #183374) 2초 실패 — 첫 배포 경로 `PREV: unbound variable` → deploy.sh 수정. 2차(파이프라인 #183408, 잡 #495972) **62초 성공**: 이미지 3종 빌드 → postgres·redis healthy → migrate 완료 → backend healthy → web healthy → 8080 200 → last_good 4개 기록. `https://j15b109.p.ssafy.io` 502 → **200**. ai-pjt 는 `GOOGLE_API_KEY is required` 로 재시작 반복(예상된 상태, LLM 키 조합 확정 후 입력). 호스트 노출 포트는 22/80/443 과 127.0.0.1 의 8080/8081 만

## AI 컨테이너 기동 실패를 배포 실패로 판정 (S15P21B109-132)

`ai-pjt` 가 healthy 가 되지 못한 배포는 **성공으로 기록하지 않는다.** `deploy:production` 잡이 `AI_REQUIRED=1` 로 실행한다.

왜 필요했나 — S15P21B109-122 때 `ai-pjt` 가 `GOOGLE_API_KEY` 부재로 **이틀간 재시작을 반복**했는데 배포는 계속 성공으로 남았다. 판정에서 빠져 있어 발견이 늦었다. S15P21B109-131 로 AI 상담이 실제로 동작하게 된 뒤로는 서빙 경로의 일부다.

| 항목 | 내용 |
|---|---|
| 동작 | `AI_REQUIRED=1` 이면 `web`·`backend`·HTTP·API 와 **같은 대기 루프**에서 `ai-pjt` healthy 를 함께 기다린다. 최대 `TIMEOUT`(기본 150s) |
| 실패 시 | 다른 대상과 동일 — backend 로그 20줄 → 직전 이미지로 롤백 → exit 1 |
| 기본값 | 스크립트 자체 기본은 `0`(사람이 서버에서 직접 돌릴 때). **CI 잡만 `1`** |

**끄는 법** (AI env 가 깨져 배포가 계속 실패하고 서빙 수정을 급히 내보내야 할 때)

| 범위 | 방법 |
|---|---|
| 그 실행만 | GitLab **Run pipeline** 화면에서 변수 `AI_REQUIRED` = `0` |
| 상시 | Settings → CI/CD → Variables 에 `AI_REQUIRED` = `0` (잡 `variables` 보다 우선) |
| 서버에서 직접 | 러너 체크아웃 디렉터리에서 `AI_REQUIRED=0 bash deploy/deploy.sh --no-build` |

**한계 — healthy 는 LLM 정상을 증명하지 않는다.** `ai-pjt` 의 `/api/system/v1/health` 는 `{"status":"ok"}` 만 반환하고 LLM·임베딩 공급자를 호출하지 않는다(배포마다 크레딧을 태우지 않기 위한 설계). 이 판정이 잡는 것은 **컨테이너가 아예 뜨지 못하는 경우**(env 누락, import 실패, 크래시 루프)다. GMS 키가 만료돼 응답만 실패하는 상황은 이 게이트를 통과한다 — 그건 파이프라인 실패 알림(S15P21B109-126) 쪽 과제다.

- 검증 기록(2026-09-10, 로컬): `docker`·`curl` 을 스텁으로 갈아끼운 하네스로 판정 로직만 격리해 검사했다(실제 컨테이너·키 불필요). **교정 전 2건 실패 → 교정 후 7건 통과.** 시나리오: `AI_REQUIRED=0` 에 ai `starting` → 성공(경고만, 기존 동작 유지) / `AI_REQUIRED=1` 에 ai 가 12s 뒤 healthy → **16s 대기 후 성공**(교정 전에는 0초에 탈출해 실패·롤백) / ai 끝까지 `starting` → 실패·롤백 / ai 컨테이너 없음 → 실패 / web `unhealthy` + ai healthy → 실패(기존 판정 회귀) / `--ai-required` 플래그 경로도 동일
- `--ai-required` 는 **한 번도 실행된 적 없는 분기**였다. 루프 밖에서 헬스를 1회만 읽어 `ai-pjt` 의 HEALTHCHECK `start-period=20s` 를 기다리지 못했고, 켜면 정상 배포까지 실패했다. 2026-09-08 첫 배포를 2초에 깬 `PREV: unbound variable` 과 같은 종류의 사각지대다 — `deploy.sh` 를 고칠 때는 밟지 않는 분기가 있는지 본다

## AI 키 (GMS) 등록 — S15P21B109-122 잔여분

`ai-pjt` 는 GMS(`gms.ssafy.io`)를 통해 gemini 를 쓴다. **GMS 키는 Google 발급 키가 아니라서 Google 로 직접 가면 `API key not valid` 로 거부된다** → 요청 대상을 GMS 로 돌려야 한다.

| 값 | 어디에 | 비고 |
|---|---|---|
| `GOOGLE_API_KEY` | 서버 `app.env` (사람이 입력) | **GMS 키**. 변수명이 GOOGLE_* 인 것은 `ai-pjt/core/config.py` 가 이 이름만 읽기 때문 |
| `GOOGLE_GEMINI_BASE_URL` | `docker-compose.prod.yml` 에 **고정** | `https://gms.ssafy.io/gmsapi/generativelanguage.googleapis.com`. GMS 가 정하는 값이라 env 로 열지 않는다(S15P21B109-130 교훈) |
| `EMBEDDING_MODEL` | 루트 compose와 `docker-compose.prod.yml`에 **고정** | `gemini-embedding-2`. GMS embedding은 이 모델과 3,072차원으로 통일한다 |
| `EMBED_DIM` | 루트 compose 기본값 3072 | 실측 벡터 길이와 일치 확인 |

### 서버 등록 절차

```bash
ssh ssafy
sudo nano /srv/s15p21b109/secrets/app.env        # GOOGLE_API_KEY= 에 GMS 키 입력
sudo grep -q '^GOOGLE_API_KEY=.\+' /srv/s15p21b109/secrets/app.env && echo 설정됨 || echo 비어있음
```

- 키를 `sed -i` 같은 한 줄 명령으로 넣지 않는다 — 셸 히스토리에 평문이 남는다. 편집기로 입력한다
- 편집 전에 「운영 설정 immutable snapshot과 복구」 절의 create 명령을 실행한다. 과거 `app.env.bak`은 갱신하거나 복구 원천으로 사용하지 않는다
- 반영: master 파이프라인의 `deploy:production` 을 Retry. ai-pjt 의 환경변수가 바뀌어 compose 가 그 컨테이너를 재생성한다

### 검증 — healthy 만으로는 부족하다

`ai-pjt/main.py` 의 `/api/system/v1/health` 는 `{"status":"ok"}` 만 반환하고 공급자를 호출하지 않는다. `validate_runtime()` 도 값이 비었는지만 본다.
**틀린 키·틀린 base URL 이어도 컨테이너는 healthy 가 된다.** 그래서 실호출로 판정한다.

```bash
sudo docker compose --env-file /srv/s15p21b109/secrets/app.env -f docker-compose.yml -f docker-compose.prod.yml \
  exec -T ai-pjt python -c "
from core.llm import embed_model
print('embed len =', len(embed_model.get_text_embedding('연금')))"
```

`3072` 가 나오면 임베딩 경로 정상. 실호출은 크레딧을 소모하므로 최소 횟수로 한다.

- 검증 기록(2026-09-09, 운영 이미지 컨테이너): 임베딩 **성공 3072**, `EMBED_DIM` 일치. 원시 HTTP 로 gemini 채팅도 200 OK
- **LLM 은 `ai-pjt` 코드 변경이 있어야 동작한다.** 컨테이너의 `langchain-google-genai` 2.1.12 는 구형 클라이언트를 써 `GOOGLE_GEMINI_BASE_URL` 을 읽지 않는다. 4.x 로 올리면 `langchain-core` 가 1.x 로 끌려 올라가 `langchain`·`langchain-openai` 0.3.x 와 충돌한다(새 SDK 를 쓰는 3.0.0+ 전부가 `langchain-core>=1.0.0` 요구). 해결책은 의존성 변경 없이 gemini 분기에 `transport="rest"` + `client_options={"api_endpoint": <base_url 에서 scheme 제거>}` 를 넘기는 것 — 운영 이미지에서 `invoke`·`stream()` 모두 검증했다. 담당: AI 파트

## 배포 실패 알림 (S15P21B109-126, A11)

파이프라인이 깨지면 팀 Mattermost 채널로 알린다. **실패를 아무도 모르면 자동 배포는 의미가 없다** — `master` 머지가 곧 운영 반영이라 실패는 롤백된 상태를 뜻한다.

| 항목 | 값 |
|---|---|
| 방식 | GitLab `Settings → Integrations → **Mattermost notifications**` |
| 트리거 | **`A pipeline status changes` 하나만.** push·work item·MR·댓글·tag·wiki·deployment·incident 전부 해제 |
| Notify only broken pipelines | **켬** — 성공까지 알리면 하루 수십 건이라 정작 실패를 놓친다 |
| Notify only when pipeline status changes | **끔** — 켜면 직전과 상태가 달라졌을 때만 온다. 연속 실패 시 두 번째부터 알림이 없어진다 |
| Branches to be notified | **`Default branch`** — `master` 만. 배포는 master 에서만 돌고, 기능 브랜치 실패는 MR 올린 본인이 화면에서 본다 |
| 채널 override(`my-channel` 칸) | 비움 — 웹훅 생성 시 지정한 채널로 간다 |

### 왜 CI 잡이 아니라 GitLab 연동인가

`.gitlab-ci.yml` 에 `when: on_failure` 잡을 두고 웹훅을 쏘는 방법도 있지만, 그러면 **웹훅 URL 이 저장소나 CI 변수에 들어간다.** 이 URL 은 그것만 있으면 누구나 채널에 글을 쓸 수 있는 값이다. GitLab 연동으로 붙이면 프로젝트 설정에만 남고 저장소에서 사라지며, 메시지 포맷도 GitLab 이 만든다.

### 웹훅 URL 취급

Mattermost 에서 incoming webhook 을 만들어 **사람이 GitLab 설정 화면에 직접 붙여넣는다.** 저장소·CI 변수·이 문서 어디에도 적지 않는다. `/srv/s15p21b109/secrets/app.env` 와 같은 원칙이다.

### 끄기 / 바꾸기

같은 화면에서 `Enable integration` 을 해제하면 즉시 멈춘다. 채널을 옮기려면 Mattermost 에서 새 웹훅을 만들어 URL 만 교체한다.

### 확인된 것

- 서버 값: `active=true` `pipeline_events=true` `notify_only_broken_pipelines=true` `branches_to_be_notified=default`, 나머지 이벤트 전부 `false`
- **성공 파이프라인은 오지 않는다** — MR·master 파이프라인 성공 2회 모두 채널 무음 (2026-09-10)
- **MR 파이프라인은 실패해도 오지 않는다** — A3 검증에서 MR 파이프라인을 의도적으로 실패시켰을 때도 무음. `branches_to_be_notified=default` 가 `master` 외 ref 를 걸러낸다
- **`job_events=true` 이지만 잡 단위 알림은 오지 않는다** — 위 실패 파이프라인에서 잡 하나가 실패했는데도 무음이었다. 이 GitLab 버전의 Trigger 목록에는 Job 항목 자체가 없고(API 로 끄려면 `webhook` 을 다시 실어 보내야 해서 손대지 않았다), 실측 결과 조용하므로 그대로 둔다
- `Test settings` 샘플은 트리거 설정과 무관하게 **push 이벤트 모양**으로 온다. `push_events=false` 이므로 실제 푸시로는 가지 않는다

**아직 관찰하지 못한 경우: `master` 파이프라인 실패.** 알림이 실제로 도착하는 유일한 조건인데 아직 겪지 않았다. 첫 실패가 곧 실검증이다.

## master 머지 조건 — 파이프라인 통과 (S15P21B109-126, A3)

`master` 머지는 곧 운영 배포다(S15P21B109-125). 그래서 **파이프라인이 통과한 MR 만 머지할 수 있다.**

| 설정 | 값 | 변경 |
|---|---|---|
| Protected branch `master` | push·merge **Maintainers**, force push 금지 | 원래부터 설정돼 있었다 |
| `only_allow_merge_if_pipeline_succeeds` | **`true`** | `false` → `true` (2026-09-10) |
| `allow_merge_on_skipped_pipeline` | **`true`** | `null` → `true` (2026-09-10) |

### `allow_merge_on_skipped_pipeline` 을 켠 이유

`.gitlab-ci.yml` 의 `workflow` 규칙이 **MR 이 열린 브랜치의 푸시 파이프라인을 `never`** 로 만든다(브랜치·MR 이중 실행 방지). 이 경우 파이프라인이 `skipped` 로 남는데, 이 값을 켜두지 않으면 **정상 MR 이 "파이프라인이 성공하지 않았다"로 막힌다.** 조건을 우회하는 설정이 아니라, 우리 workflow 규칙과 짝을 맞추는 설정이다.

### 막혔을 때

MR 화면의 머지 버튼이 비활성화되고 사유가 표시된다. **재실행하면 된다** — 일시적 실패(러너 혼잡, 빌드 실패)면 파이프라인만 다시 돌리고, 실제 결함이면 고쳐서 푸시한다.

API 로 확인하려면:

```
glab api "projects/:id/merge_requests/<iid>" | grep detailed_merge_status
```

`ci_must_pass` 면 이 조건 때문에 막힌 것이다. 구 필드 `merge_status` 는 **충돌 여부만** 뜻하므로 판단에 쓰지 않는다(막힌 MR 에서도 `can_be_merged` 로 나온다).

### 검증 기록 (2026-09-10)

설정값만 확인하지 않고 **실제로 막히는지** 확인했다. `.gitlab-ci.yml` 에 5초 만에 실패하는 잡 하나만 넣은 브랜치로 MR 을 만들어(제목에 `[검증용·머지금지]` 명시) 파이프라인을 실패시켰다.

- 파이프라인 #187061 `failed` → MR `detailed_merge_status = **ci_must_pass**`, `has_conflicts=false` (충돌이 아니라 정확히 이 조건 때문에 막혔다)
- **머지 버튼을 실제로 누르지는 않았다.** 해석이 틀렸다면 의도적으로 깨진 커밋이 master 에 들어가 그대로 배포된다. `ci_must_pass` 로 충분한 증거라 그 위험을 감수하지 않았다
- 확인 후 MR 을 닫고 브랜치를 삭제했다. `master` 에는 반영되지 않았다
- 부수 확인: 이때 **Mattermost 채널에는 알림이 오지 않았다**(MR 파이프라인은 알림 대상이 아니다 — 위 절 참고)


### A3 와 알림의 역할 구분

둘은 겹치지 않는다. **A3 는 머지 전 MR 파이프라인을, 알림은 머지 후 master 파이프라인을** 본다.

특히 **`deploy:production` 잡은 MR 파이프라인에 존재하지 않는다**(위 deploy 스테이지 절의 `rules` 참고 — MR 에서는 `never`, `master` 에서만 생성). 즉 빌드·헬스체크·롤백 전 과정은 머지 전에는 한 번도 실행되지 않으므로, A3 가 초록불을 보장해도 **배포 실패는 걸러낼 수 없다.** 실제로 이 저장소에서 난 배포 사고는 전부 이 영역이었다 — 첫 배포의 `PREV: unbound variable`(S15P21B109-125), 배포는 성공인데 미반영(S15P21B109-129·130), ai-pjt 가 이틀간 재시작 반복인데 배포는 성공 기록(S15P21B109-122·132).

A3 로 막히지 않는 master 실패가 더 있다.

- **머지 커밋에서만 나는 실패** — MR 파이프라인은 `refs/merge-requests/<iid>/head`, 즉 MR 브랜치에서 돈다. master 파이프라인은 병합 결과에서 돈다. 각자 초록불인 두 MR 이 합쳐지며 깨지는 경우는 A3 가 잡지 못한다
- 러너·디스크·도커 데몬 같은 인프라성 실패
- Maintainer 의 master 직접 푸시

정리하면 **A3 는 사전 차단, 알림은 사후 감지**다. 알림이 울린다는 것은 "머지는 됐는데 운영에는 반영되지 않고 직전 이미지로 롤백된 상태" 라는 뜻이고, 이 상태는 화면 어디에도 표시되지 않는다.

반대로 **"머지가 막혔다"는 알림은 만들 수 없다** — GitLab 에 해당 이벤트가 없다. 필요하지도 않다. 막힌 사람은 MR 화면에서 즉시 사유를 본다.
