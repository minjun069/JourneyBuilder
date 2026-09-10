#!/usr/bin/env bash
# 서버 배포 스크립트. CI deploy 잡(러너 체크아웃)에서 실행되며, 사람이 서버에서 직접 실행해도 같다.
#
#   deploy/deploy.sh                                  # 기본: /srv/s15p21b109/secrets/app.env, 빌드 포함
#   ENV_FILE=.env deploy/deploy.sh                    # 로컬 재현
#   deploy/deploy.sh --no-build                       # 이미지 재빌드 없이 재기동만
#   deploy/deploy.sh --health-url http://127.0.0.1:8080/ --timeout 180
#   deploy/deploy.sh --api-health-url http://127.0.0.1:8080/api/v1/consultations/
#   deploy/deploy.sh --ai-required                    # ai-pjt healthy 를 배포 성공 조건에 포함 (AI_REQUIRED=1 과 동일)
#   AI_REQUIRED=0 deploy/deploy.sh                    # AI 가 깨진 상황에서 서빙만 급히 배포할 때 (CI 잡 기본값을 끈다)
#
# 흐름
#   1. 사전 점검: env 파일, compose 병합(config)
#   2. 롤백 대상 확인: 직전 성공 배포가 남긴 유지 태그(:last-good) 존재 여부
#   3. build → up -d --remove-orphans   (migrate one-shot → backend 순서는 compose 가 보장)
#      → gateway 재시작. gateway(nginx) 의 upstream 은 resolve 방식(10s 주기 재조회)이라 backend 재생성 뒤
#        스스로 회복하지만, 배포 직후 헬스체크가 TTL 을 기다리지 않도록 up 뒤에 즉시 다시 해석시킨다.
#   4. 헬스체크: web·backend 컨테이너 healthy + web HTTP 200 + API 경로가 5xx 가 아닌 응답
#      (API 프로브는 host→web→gateway→backend 전 구간을 검사한다. 인증 필요 경로라 401 이 정상 응답)
#      ai-pjt 는 AI_REQUIRED=1 이면 healthy 까지 같은 루프에서 대기하고 판정에 포함한다(운영 기본).
#      0 이면 상태만 로그에 남기고 판정에서 뺀다
#   5. 실패 → :last-good 태그를 :latest 로 되돌려 up -d --no-build, exit 1 (파이프라인 failed)
#      성공 → 지금 이미지에 :last-good 을 붙이고(다음 롤백 대상) last_good 기록, dangling 정리
#
# 하지 않는 것: 볼륨 삭제(-v), DB 스키마 롤백. 마이그레이션은 앞으로만 간다 — 되돌려야 하면 사람이 한다.
set -euo pipefail

ENV_FILE="${ENV_FILE:-/srv/s15p21b109/secrets/app.env}"
STATE_DIR="${STATE_DIR:-/srv/s15p21b109/state}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/}"
API_HEALTH_URL="${API_HEALTH_URL:-http://127.0.0.1:8080/api/v1/consultations/}"   # 로그인 필요 경로 → 정상이면 401
TIMEOUT="${TIMEOUT:-150}"
AI_REQUIRED="${AI_REQUIRED:-0}"
BUILD=1

while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --state-dir) STATE_DIR="$2"; shift 2 ;;
    --health-url) HEALTH_URL="$2"; shift 2 ;;
    --api-health-url) API_HEALTH_URL="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --no-build) BUILD=0; shift ;;
    --ai-required) AI_REQUIRED=1; shift ;;
    -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "알 수 없는 인자: $1" >&2; exit 64 ;;
  esac
done

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"
# 프로젝트 이름을 고정한다. compose 기본값은 체크아웃 디렉터리 이름에서 오는데(러너: S15P21B109 → s15p21b109),
# 롤백이 compose 가 build-only 서비스에 붙이는 이미지 이름(<프로젝트>-<서비스>)을 직접 다뤄야 해서 추측하지 않는다.
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-s15p21b109}"
compose() { docker compose --project-name "$PROJECT_NAME" --env-file "$ENV_FILE" -f docker-compose.yml -f docker-compose.prod.yml "$@"; }
image_ref() { printf '%s-%s' "$PROJECT_NAME" "$1"; }
log() { printf '[deploy %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
# gateway(nginx, 외부 이미지)는 up 에서 재생성되지 않는다. conf 의 upstream 은 resolve 방식이라 backend IP 변경을
# 10s 안에 스스로 따라가지만(S15P21B109-128), 배포 직후 502 창을 없애기 위해 up 뒤에 재시작해 즉시 해석시킨다(1~2초 단절).
# web 의 nginx 는 resolver 방식이라 대상이 아니다.
refresh_gateway() { compose restart gateway >/dev/null 2>&1 && log "gateway 재시작(upstream 재해석)" || log "경고: gateway 재시작 실패"; }
# HTTP 상태코드가 "서버가 응답했다"를 뜻하는지 — 5xx 와 000(연결 실패)만 실패로 본다
responded() { case "$1" in 000|5??) return 1 ;; *) return 0 ;; esac; }

# 빌드하는 서비스만 롤백 대상 (외부 이미지 postgres/redis/gateway 는 태그가 고정이라 제외)
BUILT_SERVICES=(web backend ai-pjt migrate)
# 성공한 배포의 이미지에 붙여 두는 유지 태그. 롤백은 이 태그로만 되돌린다.
# 태그가 붙어 있으면 dangling 이 아니라서 성공 배포 끝의 image prune 이 지우지 못한다 —
# 태그 없이 이미지 ID 만 들고 있던 이전 방식은 #116(2026-09-10)에서 4개 서비스 전부
# "복원 불가" 로 롤백에 실패했고, 깨진 빌드가 그대로 운영에 남았다.
ROLLBACK_TAG="${ROLLBACK_TAG:-last-good}"

# ── 1. 사전 점검 ─────────────────────────────────────────────────────────
[ -r "$ENV_FILE" ] || { log "env 파일을 읽을 수 없습니다: $ENV_FILE"; exit 1; }
compose config --quiet || { log "compose 병합 실패 (필수 env 누락?)"; exit 1; }
mkdir -p "$STATE_DIR"
log "저장소 $(git rev-parse --short HEAD 2>/dev/null || echo '?') · env $ENV_FILE · build=$BUILD"

# ── 2. 롤백 대상 확인: 직전 성공 배포가 남긴 유지 태그 ───────────────────
# 기동 중인 컨테이너의 이미지 ID 를 들고 있는 방식은 쓰지 않는다. 그 이미지는 태그를 잃은 채 남아 있어야만
# 복원되는데, 성공 배포의 image prune 이 정확히 그런 이미지를 지운다(#116 에서 4개 전부 복원 불가).
# 대신 성공한 배포가 남긴 <프로젝트>-<서비스>:$ROLLBACK_TAG 로만 되돌린다.
# 유지 태그가 아직 없는 서비스는 "지금 기동 중인 이미지" 를 승격시켜 둔다. 그게 곧 이번 배포 직전에
# 서비스하던 상태다. 이 부트스트랩이 없으면 이 스크립트가 처음 도는 배포는 롤백 대상 없이 돌게 되고,
# 컨테이너가 태그 없는 이미지(sha256:…)를 참조 중인 경우에도 복원 경로가 생기지 않는다.
ROLLBACK_READY=()
for svc in "${BUILT_SERVICES[@]}"; do
  ref="$(image_ref "$svc"):$ROLLBACK_TAG"
  if ! docker image inspect "$ref" >/dev/null 2>&1; then
    cid="$(compose ps -aq "$svc" 2>/dev/null | head -1 || true)"
    if [ -n "$cid" ]; then
      img_id="$(docker inspect --format '{{.Image}}' "$cid" 2>/dev/null || true)"
      if [ -n "$img_id" ] && docker tag "$img_id" "$ref" 2>/dev/null; then
        log "  $svc: 기동 중인 이미지를 유지 태그로 승격 (부트스트랩)"
      fi
    fi
  fi
  if docker image inspect "$ref" >/dev/null 2>&1; then
    ROLLBACK_READY+=("$svc")
  fi
done
if [ "${#ROLLBACK_READY[@]}" -gt 0 ]; then
  log "롤백 대상(:$ROLLBACK_TAG): ${ROLLBACK_READY[*]}"
else
  log "롤백 대상 없음 — 첫 배포이거나 직전 성공 배포가 유지 태그를 남기지 않았다. 실패 시 중단만 한다"
fi

# ── 3. 빌드 + 기동 ───────────────────────────────────────────────────────
if [ "$BUILD" = 1 ]; then
  log "이미지 빌드"
  compose build --pull 2>&1 | grep -vE '^\s*$' | tail -3
fi
log "compose up"
compose up -d --remove-orphans
refresh_gateway

# ── 4. 헬스체크 ──────────────────────────────────────────────────────────
health_of() {  # 컨테이너 Health.Status (healthcheck 없으면 running 여부)
  local cid; cid="$(compose ps -q "$1" 2>/dev/null | head -1 || true)"
  [ -n "$cid" ] || { echo "missing"; return; }
  docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid"
}

# ai-pjt 의 판정 참여 여부. AI_REQUIRED=1 이면 healthy 를 "대기 조건" 에 포함한다.
# 루프 밖에서 한 번만 읽으면 web·backend 가 이미 healthy 인 배포에서 루프가 즉시 탈출해
# ai 의 HEALTHCHECK start-period(20s)를 기다리지 못하고 정상 배포를 실패로 판정한다 (S15P21B109-132).
ai_ok() { [ "$AI_REQUIRED" != 1 ] || [ "$1" = healthy ]; }

gate="web·backend healthy + GET $HEALTH_URL 200 + GET $API_HEALTH_URL 이 5xx 아님"
if [ "$AI_REQUIRED" = 1 ]; then gate="$gate + ai-pjt healthy"; fi
log "헬스체크 (최대 ${TIMEOUT}s): $gate"
deadline=$(( $(date +%s) + TIMEOUT ))
ok=0
w=unknown; b=unknown; a=unknown; code=000; api=000   # TIMEOUT<=0 이면 루프가 한 번도 안 돌아 set -u 에서 unbound
while [ "$(date +%s)" -lt "$deadline" ]; do
  w="$(health_of web)"; b="$(health_of backend)"; a="$(health_of ai-pjt)"
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH_URL" 2>/dev/null)" || code="${code:-000}"
  api="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$API_HEALTH_URL" 2>/dev/null)" || api="${api:-000}"
  if [ "$w" = healthy ] && [ "$b" = healthy ] && [ "$code" = 200 ] && responded "$api" && ai_ok "$a"; then ok=1; break; fi
  sleep 5
done
log "결과: web=$w backend=$b http=$code api=$api ai-pjt=$a"
if [ "$a" != healthy ]; then
  if [ "$AI_REQUIRED" = 1 ]; then
    log "실패: ai-pjt 가 필수(AI_REQUIRED=1)인데 '$a' — GOOGLE_API_KEY 등 env 와 컨테이너 로그를 확인하세요"
  else
    log "경고: ai-pjt 상태 '$a' — GOOGLE_API_KEY 등 env 를 확인하세요 (AI_REQUIRED=0 이라 배포 판정에는 미포함)"
  fi
fi

# ── 5. 판정 ──────────────────────────────────────────────────────────────
if [ "$ok" = 1 ]; then
  : > "$STATE_DIR/last_good_images"
  tagged=()
  for svc in "${BUILT_SERVICES[@]}"; do
    cid="$(compose ps -aq "$svc" 2>/dev/null | head -1 || true)"
    [ -n "$cid" ] || continue
    img_id="$(docker inspect --format '{{.Image}}' "$cid")"
    echo "$svc=$img_id" >> "$STATE_DIR/last_good_images"
    # 유지 태그를 방금 성공한 이미지로 옮긴다. 다음 배포가 실패하면 여기로 되돌아온다.
    # 태그를 옮기면 직전 세대는 dangling 이 되어 아래 prune 이 정리한다 → 서비스당 한 세대만 남는다.
    if docker tag "$img_id" "$(image_ref "$svc"):$ROLLBACK_TAG"; then
      tagged+=("$svc")
    else
      log "경고: $svc 유지 태그 갱신 실패 — 다음 배포가 실패해도 이 서비스는 롤백되지 않는다"
    fi
  done
  docker image prune -f >/dev/null 2>&1 || true
  log "배포 성공 · $(git rev-parse --short HEAD 2>/dev/null || echo '?') · last_good 갱신 · 유지 태그(:$ROLLBACK_TAG): ${tagged[*]:-없음}"
  exit 0
fi

log "배포 실패 — 롤백 시작"
log "backend 최근 로그 20줄:"; compose logs --no-log-prefix --tail 20 backend 2>/dev/null || true
if [ "${#ROLLBACK_READY[@]}" -eq 0 ]; then
  log "롤백 대상 없음. 컨테이너는 그대로 두고 실패로 종료 — 운영에는 방금 배포한 이미지가 그대로 남는다"
  exit 1
fi
for svc in "${ROLLBACK_READY[@]}"; do
  ref="$(image_ref "$svc")"
  if docker tag "$ref:$ROLLBACK_TAG" "$ref:latest"; then
    log "  $svc ← $ref:$ROLLBACK_TAG"
  else
    log "  경고: $svc 태그 복원 실패 — 이 서비스는 새 이미지로 남는다"
  fi
done
compose up -d --no-build --remove-orphans
refresh_gateway
sleep 10
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH_URL" 2>/dev/null)" || code="${code:-000}"
api="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$API_HEALTH_URL" 2>/dev/null)" || api="${api:-000}"
log "롤백 후 상태: web=$(health_of web) backend=$(health_of backend) ai-pjt=$(health_of ai-pjt) http=$code api=$api"
exit 1
