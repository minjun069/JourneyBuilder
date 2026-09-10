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

**원칙: 로컬은 팀원 각자 한 번 생성, 서버는 한 파일. 값을 서로 공유·복사하지 않는다. 커밋하지 않는다.**

| 어디 | 파일 | 만드는 법 | 횟수 |
|---|---|---|---|
| 로컬 개발 | 저장소 루트 `.env` | `bash deploy/make-env.sh` | PC 당 1회 |
| 서버 | `/srv/s15p21b109/secrets/app.env` (root:gitlab-runner 640 — deploy 잡의 러너가 읽어야 한다) | `sudo bash deploy/make-env.sh --output /srv/s15p21b109/secrets/app.env` | 1회 |
| CI | 없음 — `.gitlab-ci.yml` 의 `ci-only-*` 더미 | 자동 | 매 실행 |

`make-env.sh` 는 `.env.example` 을 복사해 필수 4개를 채운다.

| 변수 | 출처 | 바꾸면 |
|---|---|---|
| `DJANGO_SECRET_KEY` | 무작위 생성 | 세션·JWT 전부 무효(전원 재로그인). 데이터 무손실 |
| `POSTGRES_PASSWORD` | 무작위 생성 | **볼륨이 이미 있으면 DB 는 옛 비밀번호 유지 → 접속 끊김.** 데이터는 남음. 절차: DB 에서 `ALTER USER retirewise PASSWORD '...'` → 파일 갱신 → 재기동. **`down -v` 금지** |
| `DATABASE_URL` | 위 비밀번호로 자동 조립 | POSTGRES_* 와 항상 일치시킨다 |
| `AI_SERVICE_BEARER_TOKEN` | 무작위 생성 | backend·ai 동시 재기동이면 문제 없음 |
| `GOOGLE_API_KEY` | **GMS 키**를 직접 입력 (개인 Google 계정 키 아님 — 아래 절 참고) | 외부 회전 시만 |

- 스크립트는 파일이 이미 있으면 덮어쓰지 않는다(exit 2).
- 서버 파일은 백업 1부를 `/srv/s15p21b109/secrets/app.env.bak`(root 600)으로 둔다 — DB 비밀번호를 잃지 않기 위해.
- 누락 확인: `docker compose --env-file <파일> -f docker-compose.yml -f docker-compose.prod.yml config --quiet`
- 거부되는 값: `change-me`, `password`, `postgres`, `secret`, `ssafy` 등 (`backend-pjt/config/runtime_env.py`).

## 서버 초기 세팅 (2026-09-07 수행 기록)

| 단계 | 내용 | 상태 |
|---|---|---|
| 접속 | `ssh -i J15B109T.pem ubuntu@j15b109.p.ssafy.io` (로컬 `~/.ssh/config` 에 `Host ssafy`) | 완료 |
| 스펙 | Ubuntu 24.04.4 · 4 vCPU · 15 GiB · 309 GB | 기록 |
| Docker | 공식 apt 저장소 → docker-ce 29.8 + compose v5.5. `ubuntu`, `gitlab-runner` 를 docker 그룹에 | 완료 |
| GitLab Runner | 공식 apt 저장소 → 19.3.1. 프로젝트 러너 등록(shell executor, 태그 `ssafy-ec2`, untagged 금지). 토큰은 `/etc/gitlab-runner/config.toml`(root) | 완료 |
| 호스트 nginx | apt nginx 1.24. 사이트 설정·HTTPS 는 S15P21B109-123 | 설치만 |
| secrets | `/srv/s15p21b109/secrets/` root:gitlab-runner 750, `app.env` 640, `app.env.bak` root 600. 러너 그룹 읽기 허용은 S15P21B109-125 에서(deploy 잡이 `--env-file` 로 읽음) | 값 입력 완료(GOOGLE_API_KEY 제외) |
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
| 직전 기록 | 떠 있는 web·backend·ai-pjt·migrate 컨테이너의 이미지 ID (롤백 대상). 첫 배포면 없음 |
| 기동 | `compose build --pull` → `up -d --remove-orphans` → **`restart gateway`**. migrate one-shot → backend 순서는 compose `depends_on` 이 보장. gateway 재시작 배경: 원래 gateway nginx 의 upstream 이 고정(`server backend:8000`)이라 backend 가 재생성돼 IP 가 바뀌면 옛 IP 를 물고 `/api` 가 502 가 됐다(2026-09-08 첫 자동 배포에서 발생, S15P21B109-127). S15P21B109-128 에서 upstream 을 `resolve` 방식(10s 주기 재조회)으로 바꿔 배포 외 경로(수동 `up`, 크래시 후 재생성)에서도 자동 회복하도록 했고, 재시작은 즉시 반영용 안전망으로 유지한다. web 의 nginx 는 처음부터 `resolver` 방식 |
| 헬스체크 | 최대 150s: `web`·`backend` 컨테이너 healthy **and** `GET http://127.0.0.1:8080/` 200 **and** `GET http://127.0.0.1:8080/api/v1/consultations/` 가 5xx·000 이 아님(host→web→gateway→backend 전 구간 검사. 로그인 필요 경로라 **401 이 정상**. `--api-health-url` 로 변경). **ai-pjt 는 `AI_REQUIRED=1`(운영 기본) 이면 같은 대기 조건에 포함하고, `0` 이면 상태만 경고**(`--ai-required` 로 필수 전환) |
| 성공 | `/srv/s15p21b109/state/last_good_images` 갱신, `docker image prune -f`, exit 0 |
| 실패 | backend 로그 20줄 출력 → 직전 이미지 ID 를 compose 이미지 이름에 다시 태그 → `up -d --no-build` → `restart gateway` → exit 1 (파이프라인 failed) |

- 하지 않는 것: 볼륨 삭제(`-v`), DB 스키마 롤백. 마이그레이션은 앞으로만 간다. 스키마를 되돌려야 하면 사람이 한다
- 롤백은 "이미지" 롤백이다. 직전 배포와 이미지가 같으면(코드 변경 없는 재배포) 복원 로그가 없는 것이 정상
- 검증 기록(2026-09-07, 로컬): 성공 경로 exit 0 · last_good 4개 기록 / 닫힌 포트를 헬스 URL 로 준 실패 경로 → 롤백 분기 → exit 1, 서비스 유지 / **E2E 롤백**: `s15p21b109-web` 태그를 CMD 없는 alpine 으로 바꿔 배포 → web `restarting` 감지 → 4개 서비스 태그를 직전 ID 로 복원 → `up` → web healthy·8080 200, exit 1

## deploy 스테이지 — master 자동 배포 (S15P21B109-125)

`.gitlab-ci.yml` 의 `deploy:production` 잡. master 파이프라인에서 test 스테이지가 통과하면 러너 체크아웃에서 `bash deploy/deploy.sh` 를 실행한다. 러너가 곧 배포 서버라서 SSH 키·이미지 레지스트리·CI Variables 가 없다.

| 항목 | 값 |
|---|---|
| 트리거 | `master` 브랜치 파이프라인, test 통과 후 자동. MR 파이프라인에서는 실행하지 않음 |
| 실행 위치 | 러너 체크아웃 `/home/gitlab-runner/builds/<id>/0/s15-fintech-finance-sub1/S15P21B109` (러너 사용자 `gitlab-runner`, docker 그룹, sudo 없음) |
| 비밀값 | `/srv/s15p21b109/secrets/app.env` — root:gitlab-runner 640. deploy.sh 가 `--env-file` 로 읽는다. 백업 `app.env.bak` 은 root 600 유지 |
| 상태 파일 | `/srv/s15p21b109/state/last_good_images` (디렉터리 gitlab-runner 소유) |
| 동시성 | `resource_group: production` — 배포 잡은 한 번에 하나. `interruptible: false` — 새 커밋이 와도 진행 중 배포는 끊지 않음 |
| 환경 | GitLab Environment `production`, URL `https://j15b109.p.ssafy.io` (Deployments → Environments 에서 배포 이력 확인) |
| 실패 | deploy.sh 가 롤백 후 exit 1 → 잡 failed. 파이프라인 알림은 S15P21B109-126 |

- **재배포(코드 변경 없음, env 만 바뀐 경우)**: master 최신 파이프라인의 `deploy:production` 잡을 GitLab 에서 Retry. 또는 서버에서 러너 체크아웃 디렉터리로 가서 `bash deploy/deploy.sh --no-build`
- **ai 컨테이너만 재기동(LLM 키 입력·회전 후)**: 러너 체크아웃 디렉터리에서 `sudo docker compose --env-file /srv/s15p21b109/secrets/app.env -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate ai-pjt` (app.env 가 root:gitlab-runner 640 이라 `ubuntu` 계정은 `sudo` 필요. deploy 잡을 Retry 하면 러너 권한으로 돌아 sudo 불필요)
- **주의**: `master` 는 보호 브랜치이지만(push·merge 는 Maintainer, force push 금지) **"파이프라인 통과 시 머지" 조건이 꺼져 있다**(`only_allow_merge_if_pipeline_succeeds=false`, 2026-09-10 확인). 빨간불 MR 을 머지하면 test 가 실패해 deploy 는 실행되지 않지만(스테이지 순서), 테스트가 잡지 못하는 결함은 그대로 배포된다. 이 머지 조건을 켜는 것은 S15P21B109-126
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

**한계 — healthy 는 LLM 정상을 증명하지 않는다.** `ai-pjt` 의 `/health` 는 `{"status":"ok"}` 만 반환하고 LLM·임베딩 공급자를 호출하지 않는다(배포마다 크레딧을 태우지 않기 위한 설계). 이 판정이 잡는 것은 **컨테이너가 아예 뜨지 못하는 경우**(env 누락, import 실패, 크래시 루프)다. GMS 키가 만료돼 응답만 실패하는 상황은 이 게이트를 통과한다 — 그건 파이프라인 실패 알림(S15P21B109-126) 쪽 과제다.

- 검증 기록(2026-09-10, 로컬): `docker`·`curl` 을 스텁으로 갈아끼운 하네스로 판정 로직만 격리해 검사했다(실제 컨테이너·키 불필요). **교정 전 2건 실패 → 교정 후 7건 통과.** 시나리오: `AI_REQUIRED=0` 에 ai `starting` → 성공(경고만, 기존 동작 유지) / `AI_REQUIRED=1` 에 ai 가 12s 뒤 healthy → **16s 대기 후 성공**(교정 전에는 0초에 탈출해 실패·롤백) / ai 끝까지 `starting` → 실패·롤백 / ai 컨테이너 없음 → 실패 / web `unhealthy` + ai healthy → 실패(기존 판정 회귀) / `--ai-required` 플래그 경로도 동일
- `--ai-required` 는 **한 번도 실행된 적 없는 분기**였다. 루프 밖에서 헬스를 1회만 읽어 `ai-pjt` 의 HEALTHCHECK `start-period=20s` 를 기다리지 못했고, 켜면 정상 배포까지 실패했다. 2026-09-08 첫 배포를 2초에 깬 `PREV: unbound variable` 과 같은 종류의 사각지대다 — `deploy.sh` 를 고칠 때는 밟지 않는 분기가 있는지 본다

## AI 키 (GMS) 등록 — S15P21B109-122 잔여분

`ai-pjt` 는 GMS(`gms.ssafy.io`)를 통해 gemini 를 쓴다. **GMS 키는 Google 발급 키가 아니라서 Google 로 직접 가면 `API key not valid` 로 거부된다** → 요청 대상을 GMS 로 돌려야 한다.

| 값 | 어디에 | 비고 |
|---|---|---|
| `GOOGLE_API_KEY` | 서버 `app.env` (사람이 입력) | **GMS 키**. 변수명이 GOOGLE_* 인 것은 `ai-pjt/core/config.py` 가 이 이름만 읽기 때문 |
| `GOOGLE_GEMINI_BASE_URL` | `docker-compose.prod.yml` 에 **고정** | `https://gms.ssafy.io/gmsapi/generativelanguage.googleapis.com`. GMS 가 정하는 값이라 env 로 열지 않는다(S15P21B109-130 교훈) |
| `EMBEDDING_MODEL` | `docker-compose.prod.yml` 에 **고정** | `gemini-embedding-2`. 루트 compose 기본값 `gemini-embedding-001` 은 GMS 에 없다 |
| `EMBED_DIM` | 루트 compose 기본값 3072 | 실측 벡터 길이와 일치 확인 |

### 서버 등록 절차

```bash
ssh ssafy
sudo nano /srv/s15p21b109/secrets/app.env        # GOOGLE_API_KEY= 에 GMS 키 입력
sudo grep -q '^GOOGLE_API_KEY=.\+' /srv/s15p21b109/secrets/app.env && echo 설정됨 || echo 비어있음
sudo cp /srv/s15p21b109/secrets/app.env /srv/s15p21b109/secrets/app.env.bak && sudo chmod 600 /srv/s15p21b109/secrets/app.env.bak
```

- 키를 `sed -i` 같은 한 줄 명령으로 넣지 않는다 — 셸 히스토리에 평문이 남는다. 편집기로 입력한다
- **백업(`app.env.bak`)도 같이 갱신한다.** 옛 값이 남은 백업을 복원하면 되돌아간다
- 반영: master 파이프라인의 `deploy:production` 을 Retry. ai-pjt 의 환경변수가 바뀌어 compose 가 그 컨테이너를 재생성한다

### 검증 — healthy 만으로는 부족하다

`ai-pjt/main.py` 의 `/health` 는 `{"status":"ok"}` 만 반환하고 공급자를 호출하지 않는다. `validate_runtime()` 도 값이 비었는지만 본다.
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

## 이후 절 (해당 이슈에서 채움)
- 데이터 배치 컨테이너·보호 브랜치·알림 — S15P21B109-126
