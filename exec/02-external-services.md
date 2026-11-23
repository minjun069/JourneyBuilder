# 2. 외부 서비스 정보

작성일 2026-09-28 / 기준 커밋 `1b83057` (master)

## 0. 먼저 알아둘 점

- **소셜 로그인은 사용하지 않는다.** 회원 인증은 이메일·비밀번호 기반 JWT(Django simplejwt)로 자체 처리한다.
- **포톤 클라우드, 코드 컴파일 서비스, 결제, 지도, 메일·SMS 발송, 외부 스토리지(S3 등)도 사용하지 않는다.**
- 외부 서비스는 AI 모델 호출(SSAFY GMS)이 핵심이며, 나머지는 인프라·CI/CD·알림 용도다.

## 1. 요약

| 서비스 | 용도 | 필수 여부 | 사용 컴포넌트 | 관련 환경변수 | 설정 위치 |
|---|---|---|---|---|---|
| SSAFY GMS | Gemini LLM 호출, 텍스트 임베딩 | **필수** | ai-pjt, rag-bootstrap | `GOOGLE_API_KEY`, `LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL` | 루트 `.env` / 서버 `app.env`, `docker-compose.yml` |
| Google Gemini API (직접) | GMS 대신 Google 로 직접 호출 | 선택 (현재 미사용) | ai-pjt | `GOOGLE_API_KEY`, `GOOGLE_GEMINI_BASE_URL` | `docker-compose.yml` 고정값 수정 필요 |
| OpenAI API (호환 엔드포인트) | LLM 공급자 전환 | 선택 (현재 미사용) | ai-pjt | `LLM_PROVIDER`, `OPENAI_API_KEY`, `LLM_BASE_URL` | 루트 `.env` |
| Langfuse v4 | 마스킹된 AI 관측 이벤트 수집 | 선택 (기본 비활성) | ai-pjt | `LANGFUSE_*` | 루트 `.env` |
| AWS EC2 (SSAFY 제공) | 운영 서버 | 운영 필수 | 전체 | — | — |
| Let's Encrypt / certbot | HTTPS 인증서 | 운영 필수 | 호스트 nginx | — | 서버 `/etc/nginx/sites-available/s15p21b109` |
| GitLab (lab.ssafy.com) + GitLab Runner | 저장소, CI/CD, 자동 배포 | 운영 필수 | 전체 | CI 더미 변수 | `.gitlab-ci.yml`, 서버 `/etc/gitlab-runner/config.toml` |
| Mattermost incoming webhook | master 파이프라인 실패 알림 | 선택 | GitLab | — | GitLab 프로젝트 설정 |
| Docker Hub | 베이스 이미지 | 빌드 필수 | 전체 | — | Dockerfile, Compose |
| PyPI / npm registry | 의존성 설치 | 빌드 필수 | backend, ai, front | — | lock 파일 |

---

## 2. 서비스별 상세

### 2-1. SSAFY GMS (gms.ssafy.io) — 필수

**무엇에 쓰는가**

| 용도 | 모델 | 호출 위치 |
|---|---|---|
| AI 상담 LLM (질의응답, 입력 보조, 오류 검토, 차트 해석 등) | `gemini-2.5-flash-lite` (기본값, `LLM_MODEL` 로 변경) | `ai-pjt/core/llm.py` `_create_llm()` |
| 텍스트 임베딩 (RAG 검색, 대화 기억) | `gemini-embedding-2`, 3072차원 | `ai-pjt/core/llm.py` `_create_llamaindex_embedding()` |
| 운영 `rag-bootstrap` 의 상담 후보 카드 37건 색인 | `gemini-embedding-2` | `ai-pjt/rag/bootstrap.py` |

**가입·키 발급**

1. SSAFY 교육생 계정으로 GMS 포털(https://gms.ssafy.io)에 로그인한다.
2. GMS 키를 발급받는다. 이 키는 Google 이 발급한 키가 아니라 GMS 프록시용 키다.
3. 사용량(크레딧) 한도는 GMS 포털에서 확인한다.

**프로젝트에 넣는 방법**

| 값 | 위치 | 비고 |
|---|---|---|
| `GOOGLE_API_KEY=<GMS 키>` | 루트 `.env` (로컬), `/srv/s15p21b109/secrets/app.env` (서버) | 변수 이름이 `GOOGLE_*` 인 이유는 `ai-pjt/core/config.py` 가 이 이름만 읽기 때문이다 |
| `GOOGLE_GEMINI_BASE_URL=https://gms.ssafy.io/gmsapi/generativelanguage.googleapis.com` | `docker-compose.yml`, `docker-compose.prod.yml` 에 **고정** | GMS 가 정하는 값이라 env 로 열지 않는다 |
| `EMBEDDING_MODEL=gemini-embedding-2`, `EMBED_DIM=3072` | Compose 고정 | 공식 RAG artifact 가 이 모델·차원으로 만들어졌다 |
| `LLM_PROVIDER=gemini`, `LLM_MODEL=gemini-2.5-flash-lite` | 루트 `.env` (선택) | 모델 교체 시 수정 |
| `LLM_BASE_URL=https://gms.ssafy.io/gmsapi/api.openai.com/v1` | 루트 `.env` (선택) | GMS 의 OpenAI 호환 경로. `LLM_PROVIDER=openai` 일 때 사용 |

서버에서는 편집기로 입력한다. 한 줄 명령은 셸 히스토리에 키가 남는다.

```bash
sudoedit /srv/s15p21b109/secrets/app.env         # GOOGLE_API_KEY= 에 GMS 키 입력
sudo grep -q '^GOOGLE_API_KEY=.\+' /srv/s15p21b109/secrets/app.env && echo 설정됨 || echo 비어있음
```

반영은 master 파이프라인의 `deploy:production` 잡을 Retry 하거나 `rag-bootstrap ai-pjt` 를 재생성한다.

**검증 방법**

ai-pjt 의 헬스체크는 모델을 호출하지 않으므로 틀린 키여도 `healthy` 가 된다. 실호출로 확인한다 (크레딧 소모).

```bash
docker compose exec -T ai-pjt python -c "from core.llm import embed_model; print('embed len =', len(embed_model.get_text_embedding('연금')))"
# embed len = 3072  → GMS 임베딩 정상
```

**주의사항**

- GMS 키를 Google 로 직접 보내면 `API key not valid` 로 거부된다. base URL 이 반드시 GMS 주소여야 한다.
- 설치된 `langchain-google-genai` 2.1.12 는 `GOOGLE_GEMINI_BASE_URL` 을 스스로 읽지 않는다. `ai-pjt/core/llm.py` 가 `transport="rest"` 와 `client_options={"api_endpoint": <scheme 제거한 base URL>}` 을 넘겨 GMS 로 보낸다. 임베딩 SDK(`google-genai`)는 환경변수를 직접 읽는다.
- 모델명이 GMS 에서 지원되지 않으면 404 가 난다. 이때 `LLM_MODEL` 을 GMS 지원 모델로 바꾼다.
- AI 로 보내는 입력은 `ai-pjt/core/masking.py` 등 마스킹 경계를 통과한다. 직접 식별정보·비밀값은 모델 호출 전에 마스킹·가명화된다.
- 키는 저장소·채팅·CI Variables 에 올리지 않는다.

### 2-2. Google Gemini API 직접 사용 / OpenAI API — 선택, 현재 미사용

| 전환 대상 | 필요한 값 | 방법 |
|---|---|---|
| Google AI Studio 직접 호출 | Google AI Studio 에서 발급한 API 키 | `docker-compose.yml` 의 `GOOGLE_GEMINI_BASE_URL` 을 빈 값으로 바꾸고 `GOOGLE_API_KEY` 에 Google 키 입력 |
| OpenAI 또는 OpenAI 호환 LLM | `OPENAI_API_KEY`, 엔드포인트 URL | 루트 `.env` 에 `LLM_PROVIDER=openai`, `OPENAI_API_KEY=<키>`, `LLM_BASE_URL=<엔드포인트>`, `LLM_MODEL=<모델>` |
| vLLM 등 자체 호스팅 | 엔드포인트 URL | `LLM_PROVIDER=opensource`, `LLM_BASE_URL=<엔드포인트>` |

임베딩 공급자는 `gemini` 만 지원한다. 따라서 LLM 을 바꿔도 `GOOGLE_API_KEY` 는 계속 필요하다.

### 2-3. Langfuse v4 — 선택 (기본 비활성)

**무엇에 쓰는가.** AI 요청 단위 관측(요청 → 단계별 observation, 모델 호출, 토큰·지연)을 수집한다. raw LangChain 콜백은 쓰지 않고, `MaskedInstrumentationSink` 를 통과한 마스킹된 typed event 만 보낸다.

**가입·키 발급.** https://cloud.langfuse.com 에서 가입하고 프로젝트를 만든 뒤 Settings → API Keys 에서 public key(`pk-lf-...`)와 secret key(`sk-lf-...`)를 발급한다. 자체 호스팅은 공식 v4 Docker Compose 를 별도 스택으로 띄운다.

**프로젝트에 넣는 방법** (루트 `.env`)

```dotenv
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_TRACING_ENVIRONMENT=development
```

`LANGFUSE_ENABLED=true` 인데 세 값 중 하나라도 비었거나 URL 에 자격증명이 들어 있으면 AI 서비스가 기동을 거부한다. release 값은 env 가 아니라 이미지 빌드 arg `APP_RELEASE` 로 고정된다.

**검증 방법**

```bash
docker compose run --rm --no-deps ai-pjt \
  python -c 'from langfuse import get_client; raise SystemExit(0 if get_client().auth_check() else 1)'
```

**주의.** `LANGCHAIN_TRACING_V2`, `LANGSMITH_TRACING` 을 켜면 AI 서비스가 기동을 거부한다. Langfuse 장애는 AI 응답을 실패시키지 않으며 readiness 의존성도 아니다.

### 2-4. AWS EC2 (SSAFY 제공) — 운영 서버

| 항목 | 값 |
|---|---|
| 도메인 | `j15b109.p.ssafy.io` |
| 서비스 URL | https://j15b109.p.ssafy.io |
| 접속 | `ssh -i J15B109T.pem ubuntu@j15b109.p.ssafy.io` (pem 키는 SSAFY 가 팀에 배포) |
| OS / 사양 | Ubuntu 24.04.4, 4 vCPU, 15 GiB, 309 GB |
| 열린 포트 | 22 (SSH), 80 (HTTP → HTTPS 리다이렉트), 443 (HTTPS) |
| OS 계정 | `ubuntu` (SSH, docker 그룹), `gitlab-runner` (배포 실행, docker 그룹, sudo 없음), `root` (비밀값·스냅샷 소유) |

**금지 사항 (SSAFY 공지, 위반 시 서버 초기화 외 복구 불가)**

- ufw 에서 22 차단
- `~/.ssh/authorized_keys` 삭제
- `/home`·시스템 디렉터리 권한 변경
- DB·캐시 포트를 호스트에 노출

### 2-5. Let's Encrypt / certbot — HTTPS 인증서

| 항목 | 값 |
|---|---|
| 발급 명령 | `sudo certbot --nginx -d j15b109.p.ssafy.io --register-unsafely-without-email --agree-tos --redirect` |
| 발급일 / 만료일 | 2026-09-07 / 2026-12-06 |
| 자동 갱신 | `certbot.timer` (하루 2회) |
| 계정 | 이메일 미등록. 만료 경고 메일이 오지 않으므로 직접 점검 |

```bash
sudo certbot certificates
sudo certbot renew --dry-run
systemctl list-timers certbot.timer
```

certbot 이 사이트 설정 파일에 443 블록과 80→443 리다이렉트를 추가한다. 저장소의 `deploy/nginx-host.conf` 를 서버에 복사할 때는 이 블록을 덮어쓰지 말고 `/api/`·`/` location 만 반영한다.

### 2-6. GitLab (lab.ssafy.com) + GitLab Runner — CI/CD

| 항목 | 값 |
|---|---|
| 저장소 | https://lab.ssafy.com/s15-fintech-finance-sub1/S15P21B109 |
| 기본 브랜치 | `master` (보호: push·merge 는 Maintainer, force push 금지) |
| 머지 조건 | `only_allow_merge_if_pipeline_succeeds=true`, `allow_merge_on_skipped_pipeline=true` |
| Runner | EC2 self-hosted, GitLab Runner 19.3.1, **shell executor**, 태그 `ssafy-ec2`, untagged 잡 거부 |
| Runner 토큰 | `/etc/gitlab-runner/config.toml` (root 소유) |
| Environment | `production`, URL `https://j15b109.p.ssafy.io` (Deployments → Environments 에서 이력 확인) |
| 비밀값 | CI Variables 에 두지 않는다. 서버 `/srv/s15p21b109/secrets/app.env` 를 `deploy.sh` 가 읽는다 |

러너 등록 절차:

1. GitLab 프로젝트 → Settings → CI/CD → Runners → New project runner 에서 태그 `ssafy-ec2` 로 러너를 만들고 토큰을 받는다.
2. 서버에서 `sudo gitlab-runner register` 로 등록한다. executor 는 `shell` 을 선택한다.
3. `sudo gitlab-runner verify` 로 확인하고 `gitlab-runner` 계정을 docker 그룹에 넣는다.

### 2-7. Mattermost 알림 — 선택

| 항목 | 설정값 |
|---|---|
| 방식 | GitLab `Settings → Integrations → Mattermost notifications` |
| Webhook | Mattermost 에서 incoming webhook 을 만들어 GitLab 설정 화면에 붙여넣음 |
| Trigger | `A pipeline status changes` 만 선택 (나머지 이벤트 해제) |
| Notify only broken pipelines | 켬 |
| Notify only when pipeline status changes | 끔 (연속 실패도 알림) |
| Branches to be notified | `Default branch` (`master` 만) |

웹훅 URL 은 그것만 있으면 누구나 채널에 글을 쓸 수 있는 값이므로 저장소·CI 변수·문서에 적지 않는다.

### 2-8. Docker Hub — 공개 베이스 이미지

| 이미지:태그 | 용도 |
|---|---|
| `python:3.13.14-slim-bookworm` | backend, ai 이미지 베이스 |
| `node:22-bookworm-slim` | web 이미지 1단계 (Vite 빌드) |
| `node:22-bookworm` | CI 프론트 테스트 |
| `nginx:1.27-alpine` | web 이미지 2단계 (SPA 서빙) |
| `nginx:1.29.1-alpine` | gateway |
| `pgvector/pgvector:pg16` | PostgreSQL 16 + pgvector |
| `redis:7.4-bookworm` | Redis |

가입 없이 익명 pull 로 사용한다. 익명 pull 한도에 걸리면 `docker login` 후 다시 빌드한다.

### 2-9. PyPI / npm registry — 의존성 설치

| 대상 | 파일 | 고정 방식 |
|---|---|---|
| Backend | `backend-pjt/requirements.lock`, `requirements-build.lock` | `pip install --require-hashes` |
| AI | `ai-pjt/requirements.lock` | `pip install --require-hashes` |
| Frontend | `front-pjt/package-lock.json` (lockfileVersion 3) | `npm ci` |

lock 갱신은 `uv pip compile --python-version 3.13 --python-platform x86_64-unknown-linux-gnu --generate-hashes --exclude-newer 2026-09-03T00:00:00Z` 로 한다.

---

## 3. 비밀값 취급 원칙

| 비밀값 | 두는 곳 | 두지 않는 곳 |
|---|---|---|
| GMS 키, DB 비밀번호, Django 시크릿, 서비스 토큰, 가명화 키 | 로컬 루트 `.env`, 서버 `/srv/s15p21b109/secrets/app.env` | Git, CI Variables, CI 로그, 채팅, 문서 |
| Langfuse 키 | 위와 동일 (사용 시) | 위와 동일 |
| Runner 토큰 | 서버 `/etc/gitlab-runner/config.toml` | 저장소 |
| Mattermost 웹훅 URL | GitLab 프로젝트 Integrations 설정 | 저장소, CI 변수 |
| EC2 pem 키 | 팀원 로컬 `~/.ssh` | 저장소 |

- 서버 `app.env` 를 바꾸기 전에는 `deploy/f14-config-snapshot.sh create` 로 immutable snapshot 을 만든다.
- 값은 편집기(`sudoedit`)로 입력한다. `printf`·`sed -i`·환경변수 export 는 히스토리와 프로세스 목록에 평문을 남긴다.
