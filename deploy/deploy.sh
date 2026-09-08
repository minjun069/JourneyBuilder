#!/usr/bin/env bash
# 서버 배포 스크립트. CI deploy 잡(러너 체크아웃)에서 실행되며, 사람이 서버에서 직접 실행해도 같다.
#
#   deploy/deploy.sh                                  # 기본: /srv/s15p21b109/secrets/app.env, 빌드 포함
#   ENV_FILE=.env deploy/deploy.sh                    # 로컬 재현
#   deploy/deploy.sh --no-build                       # 이미지 재빌드 없이 재기동만
#   deploy/deploy.sh --health-url http://127.0.0.1:8080/ --timeout 180
#   deploy/deploy.sh --api-health-url http://127.0.0.1:8080/api/v1/consultations/
#
# 흐름
#   1. 사전 점검: env 파일, compose 병합(config)
#   2. 현재 기동 중인 빌드 이미지 ID 기록 (롤백 대상)
#   3. build → up -d --remove-orphans   (migrate one-shot → backend 순서는 compose 가 보장)
#      → gateway 재시작. gateway(nginx) 는 고정 upstream 이라 기동 시점의 backend IP 를 계속 쓴다.
#        backend 가 재생성되면 IP 가 바뀌어 /api 가 502 가 되므로 up 뒤에 항상 다시 해석시킨다.
#   4. 헬스체크: web·backend 컨테이너 healthy + web HTTP 200 + API 경로가 5xx 가 아닌 응답
#      (API 프로브는 host→web→gateway→backend 전 구간을 검사한다. 인증 필요 경로라 401 이 정상 응답)
#      ai-pjt 는 상태만 보고(AI_REQUIRED=1 이면 필수)
#   5. 실패 → 직전 이미지로 태그를 되돌려 up -d --no-build, exit 1 (파이프라인 failed)
#      성공 → 이미지 ID 를 last_good 에 저장, dangling 이미지 정리
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
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "알 수 없는 인자: $1" >&2; exit 64 ;;
  esac
done

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"
compose() { docker compose --env-file "$ENV_FILE" -f docker-compose.yml -f docker-compose.prod.yml "$@"; }
log() { printf '[deploy %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
# gateway(nginx, 외부 이미지)는 up 에서 재생성되지 않는데 conf 의 upstream 이 고정이라 옛 backend IP 를 물고 있다 →
# up 뒤에 재시작해서 다시 해석시킨다(1~2초 단절). web 의 nginx 는 resolver 방식이라 대상이 아니다.
refresh_gateway() { compose restart gateway >/dev/null 2>&1 && log "gateway 재시작(upstream 재해석)" || log "경고: gateway 재시작 실패"; }
# HTTP 상태코드가 "서버가 응답했다"를 뜻하는지 — 5xx 와 000(연결 실패)만 실패로 본다
responded() { case "$1" in 000|5??) return 1 ;; *) return 0 ;; esac; }

# 빌드하는 서비스만 롤백 대상 (외부 이미지 postgres/redis/gateway 는 태그가 고정이라 제외)
BUILT_SERVICES=(web backend ai-pjt migrate)

# ── 1. 사전 점검 ─────────────────────────────────────────────────────────
[ -r "$ENV_FILE" ] || { log "env 파일을 읽을 수 없습니다: $ENV_FILE"; exit 1; }
compose config --quiet || { log "compose 병합 실패 (필수 env 누락?)"; exit 1; }
mkdir -p "$STATE_DIR"
log "저장소 $(git rev-parse --short HEAD 2>/dev/null || echo '?') · env $ENV_FILE · build=$BUILD"

# ── 2. 롤백 대상 기록: 지금 떠 있는 컨테이너의 이미지 ID 와 그 컨테이너가 쓴 이미지 이름(태그) ─────
# 이미지 이름은 compose 가 붙인 것(예: s15p21b109-web)을 컨테이너 설정에서 읽는다 — build-only 서비스는
# compose config 출력에 image 키가 없어서 거기서 읽을 수 없다.
declare -A PREV=() PREV_REF=()   # 반드시 빈 값으로 초기화 — 선언만 된 빈 연관배열은 set -u 에서 ${#PREV[@]} 가 unbound 오류(첫 배포 경로, 어제 로컬 검증은 컨테이너가 있는 상태여서 미노출)
for svc in "${BUILT_SERVICES[@]}"; do
  cid="$(compose ps -aq "$svc" 2>/dev/null | head -1 || true)"
  if [ -n "$cid" ]; then
    PREV[$svc]="$(docker inspect --format '{{.Image}}' "$cid" 2>/dev/null || true)"
    PREV_REF[$svc]="$(docker inspect --format '{{.Config.Image}}' "$cid" 2>/dev/null || true)"
  fi
done
if [ "${#PREV[@]}" -gt 0 ]; then
  log "직전 이미지 기록: ${!PREV[*]}"
else
  log "직전 컨테이너 없음 — 첫 배포. 실패 시 롤백 대상이 없어 중단만 한다"
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

log "헬스체크 (최대 ${TIMEOUT}s): web·backend healthy + GET $HEALTH_URL 200 + GET $API_HEALTH_URL 이 5xx 아님"
deadline=$(( $(date +%s) + TIMEOUT ))
ok=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  w="$(health_of web)"; b="$(health_of backend)"
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH_URL" 2>/dev/null)" || code="${code:-000}"
  api="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$API_HEALTH_URL" 2>/dev/null)" || api="${api:-000}"
  if [ "$w" = healthy ] && [ "$b" = healthy ] && [ "$code" = 200 ] && responded "$api"; then ok=1; break; fi
  sleep 5
done
a="$(health_of ai-pjt)"
log "결과: web=$w backend=$b http=$code api=$api ai-pjt=$a"
if [ "$AI_REQUIRED" = 1 ] && [ "$a" != healthy ]; then ok=0; log "ai-pjt 가 필수인데 healthy 가 아님"; fi
if [ "$a" != healthy ]; then log "경고: ai-pjt 상태 '$a' — GOOGLE_API_KEY 등 env 를 확인하세요 (배포 판정에는 미포함)"; fi

# ── 5. 판정 ──────────────────────────────────────────────────────────────
if [ "$ok" = 1 ]; then
  : > "$STATE_DIR/last_good_images"
  for svc in "${BUILT_SERVICES[@]}"; do
    cid="$(compose ps -aq "$svc" 2>/dev/null | head -1 || true)"
    [ -n "$cid" ] && echo "$svc=$(docker inspect --format '{{.Image}}' "$cid")" >> "$STATE_DIR/last_good_images"
  done
  docker image prune -f >/dev/null 2>&1 || true
  log "배포 성공 · $(git rev-parse --short HEAD 2>/dev/null || echo '?') · last_good 갱신"
  exit 0
fi

log "배포 실패 — 롤백 시작"
log "backend 최근 로그 20줄:"; compose logs --no-log-prefix --tail 20 backend 2>/dev/null || true
if [ "${#PREV[@]}" -eq 0 ]; then
  log "롤백 대상 없음(첫 배포). 컨테이너는 그대로 두고 실패로 종료"
  exit 1
fi
for svc in "${!PREV[@]}"; do
  img="${PREV_REF[$svc]:-}"; prev="${PREV[$svc]}"
  if [ -n "$img" ] && [ -n "$prev" ] && docker image inspect "$prev" >/dev/null 2>&1; then
    docker tag "$prev" "$img" && log "  $svc ← ${prev:7:12} (태그 $img 복원)"
  else
    log "  $svc: 복원 불가 (직전 이미지가 이미 정리됨)"
  fi
done
compose up -d --no-build --remove-orphans
refresh_gateway
sleep 10
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH_URL" 2>/dev/null)" || code="${code:-000}"
api="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$API_HEALTH_URL" 2>/dev/null)" || api="${api:-000}"
log "롤백 후 상태: web=$(health_of web) backend=$(health_of backend) http=$code api=$api"
exit 1
