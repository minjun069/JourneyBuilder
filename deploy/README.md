# 배포 운영 안내 (deploy/)

배포 담당: 고우정. 이 문서는 서버(EC2 `j15b109.p.ssafy.io`)를 처음부터 다시 세울 수 있는 수준을 목표로 한다.
참고 세팅: 이전 프로젝트 S15P11B204(`.gitlab-ci.yml` deploy 1스테이지 · shell 러너 · 서버 secrets 파일 · 호스트 nginx+certbot).

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
| 서버 | `/srv/s15p21b109/secrets/app.env` (root 600) | `sudo bash deploy/make-env.sh --output /srv/s15p21b109/secrets/app.env` | 1회 |
| CI | 없음 — `.gitlab-ci.yml` 의 `ci-only-*` 더미 | 자동 | 매 실행 |

`make-env.sh` 는 `.env.example` 을 복사해 필수 4개를 채운다.

| 변수 | 출처 | 바꾸면 |
|---|---|---|
| `DJANGO_SECRET_KEY` | 무작위 생성 | 세션·JWT 전부 무효(전원 재로그인). 데이터 무손실 |
| `POSTGRES_PASSWORD` | 무작위 생성 | **볼륨이 이미 있으면 DB 는 옛 비밀번호 유지 → 접속 끊김.** 데이터는 남음. 절차: DB 에서 `ALTER USER retirewise PASSWORD '...'` → 파일 갱신 → 재기동. **`down -v` 금지** |
| `DATABASE_URL` | 위 비밀번호로 자동 조립 | POSTGRES_* 와 항상 일치시킨다 |
| `AI_SERVICE_BEARER_TOKEN` | 무작위 생성 | backend·ai 동시 재기동이면 문제 없음 |
| `GOOGLE_API_KEY` | 팀 Gemini 키를 직접 입력 | 외부 회전 시만 |

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
| secrets | `/srv/s15p21b109/secrets/` root 700, `app.env` 서식 600 | 값 입력 대기 |
| ufw | 22 만 개방 상태. 80/443 은 S15P21B109-123 에서 `sudo ufw allow 80/tcp`, `443/tcp` (SSH 세션 2개 유지) | 대기 |

### 서버 작업 금지 사항 (SSAFY 공지 — 위반 시 초기화 외 복구 불가)
- ufw 에서 22 차단, `authorized_keys` 삭제, `/home`·시스템 디렉터리 퍼미션 변경 금지
- DB·캐시 포트를 호스트에 노출하지 않는다 (compose 는 `expose` 만)
- 비밀값을 저장소·채팅에 올리지 않는다

### 러너 운영 메모
- shell executor 는 잡마다 같은 체크아웃(`/home/gitlab-runner/builds/...`)을 재사용한다. 컨테이너가 root 로 파일을 만들면 다음 잡의 `git clean` 이 실패한다 → CI 는 소스를 `:ro` 로 마운트하고, 쓰기가 필요한 컨테이너는 `--user $(id -u):$(id -g)` 로 실행한다.
- 오염됐을 때 복구: `sudo chown -R gitlab-runner:gitlab-runner /home/gitlab-runner/builds`
- 상태: `sudo gitlab-runner verify`, `systemctl status gitlab-runner`

## 이후 절 (해당 이슈에서 채움)
- HTTPS·호스트 nginx 사이트 — S15P21B109-123
- 배포 스크립트·롤백 — S15P21B109-124
- deploy 스테이지 — S15P21B109-125
- 데이터 배치 컨테이너·보호 브랜치·알림 — S15P21B109-126
