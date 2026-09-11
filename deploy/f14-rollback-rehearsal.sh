#!/usr/bin/env bash
# F14-T05 production-only rehearsal.
#
# 실행 전제: 운영 서버의 최신 checkout에서 root로 실행한다. app.env 값은 읽어
# compose에 전달할 뿐 출력하지 않는다. live 고객 schema/data는 dump하거나 변경하지 않고,
# 전용 합성 schema에서 dump→변경→restore를 검증한다.
set -euo pipefail

ENV_FILE="${ENV_FILE:-/srv/s15p21b109/secrets/app.env}"
SNAPSHOT_ROOT="${SNAPSHOT_ROOT:-/srv/s15p21b109/secrets/snapshots}"
STATE_DIR="${STATE_DIR:-/srv/s15p21b109/state/f14-rehearsals}"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-s15p21b109}"
TIMEOUT="${TIMEOUT:-30}"
CANDIDATE=''
REPORT_FILE=''

usage() {
  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
  printf '%s\n' 'usage: sudo bash deploy/f14-rollback-rehearsal.sh --candidate <git-sha> [--report <path>]'
  exit "${1:-0}"
}

die() { printf '[f14-rehearsal] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[f14-rehearsal] %s\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --candidate) CANDIDATE="${2:?--candidate 값이 필요합니다}"; shift 2 ;;
    --report) REPORT_FILE="${2:?--report 값이 필요합니다}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?--env-file 값이 필요합니다}"; shift 2 ;;
    --snapshot-root) SNAPSHOT_ROOT="${2:?--snapshot-root 값이 필요합니다}"; shift 2 ;;
    --state-dir) STATE_DIR="${2:?--state-dir 값이 필요합니다}"; shift 2 ;;
    --timeout) TIMEOUT="${2:?--timeout 값이 필요합니다}"; shift 2 ;;
    -h|--help) usage 0 ;;
    *) die "알 수 없는 인자: $1" ;;
  esac
done

[ "${EUID:-$(id -u)}" -eq 0 ] || die "운영 snapshot 권한과 복구를 검증하려면 root로 실행해야 합니다"
[ -n "$CANDIDATE" ] || die "--candidate가 필요합니다"
case "$CANDIDATE" in *[!A-Fa-f0-9]*) die "candidate는 Git SHA 형식이어야 합니다" ;; esac
case "$TIMEOUT" in ''|*[!0-9]*) die "timeout은 양의 정수여야 합니다" ;; esac
[ "$TIMEOUT" -gt 0 ] || die "timeout은 1 이상이어야 합니다"

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"
snapshot_tool="$repo_root/deploy/f14-config-snapshot.sh"
deploy_tool="$repo_root/deploy/deploy.sh"
[ -x "$snapshot_tool" ] || die "실행 가능한 snapshot 도구가 없습니다: $snapshot_tool"
[ -x "$deploy_tool" ] || die "실행 가능한 deploy 도구가 없습니다: $deploy_tool"
[ -f "$ENV_FILE" ] && [ ! -L "$ENV_FILE" ] || die "운영 env 파일이 없거나 symlink입니다: $ENV_FILE"

compose_files=(-f "$repo_root/docker-compose.yml" -f "$repo_root/docker-compose.prod.yml")
compose() { docker compose --project-name "$PROJECT_NAME" --env-file "$ENV_FILE" "${compose_files[@]}" "$@"; }
image_ref() { printf '%s-%s' "$PROJECT_NAME" "$1"; }
health_of() {
  local cid
  cid="$(compose ps -q "$1" 2>/dev/null | head -1 || true)"
  [ -n "$cid" ] || { printf 'missing'; return; }
  docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid"
}
responded() { case "$1" in 000|5??) return 1 ;; *) return 0 ;; esac; }

services=(web backend ai-pjt migrate)
declare -A last_good_ids=()
snapshot_id="$(date -u +%Y%m%dT%H%M%SZ)-${CANDIDATE:0:12}"
schema_token="$(printf '%s:%s:%s' "$snapshot_id" "$$" "$(date +%s%N)" | sha256sum | cut -c1-16)"
rehearsal_schema="f14_rehearsal_${schema_token}"
marker="f14_${schema_token}"
work_dir=''
db_dump=''
db_dump_sha=''
failure_injected=0
schema_created=0
report_written=0

restore_latest_tags() {
  local svc ref
  [ "$failure_injected" -eq 1 ] || return 0
  for svc in "${services[@]}"; do
    ref="$(image_ref "$svc")"
    docker image inspect "$ref:last-good" >/dev/null 2>&1 || continue
    docker tag "$ref:last-good" "$ref:latest" >/dev/null
  done
  compose up -d --no-build --remove-orphans >/dev/null 2>&1 || true
  compose restart gateway >/dev/null 2>&1 || true
}

drop_rehearsal_schema() {
  [ "$schema_created" -eq 1 ] || return 0
  compose exec -T -e F14_SCHEMA="$rehearsal_schema" postgres sh -euc \
    'case "$F14_SCHEMA" in f14_rehearsal_[a-f0-9]*) ;; *) exit 64 ;; esac; psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP SCHEMA IF EXISTS $F14_SCHEMA CASCADE" >/dev/null' \
    >/dev/null 2>&1 || true
  schema_created=0
}

cleanup() {
  status=$?
  restore_latest_tags
  drop_rehearsal_schema
  if [ "$status" -ne 0 ]; then
    log "FAIL snapshot_id=$snapshot_id report_written=$report_written (snapshot은 복구 증거로 보존)"
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

# 1. 운영 상태와 rollback anchor를 mutation 전에 검증한다.
compose config --quiet
source_meta="$(stat -c '%U:%G:%a' "$ENV_FILE")"
[ "$source_meta" = 'root:gitlab-runner:640' ] \
  || die "운영 env 권한은 root:gitlab-runner 640이어야 합니다(현재 $source_meta)"

initial_web="$(health_of web)"; initial_backend="$(health_of backend)"; initial_ai="$(health_of ai-pjt)"
initial_http="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8080/ || true)"
initial_api="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8080/api/v1/consultations/ || true)"
[ "$initial_web:$initial_backend:$initial_ai:$initial_http" = 'healthy:healthy:healthy:200' ] \
  || die "시작 상태가 healthy가 아닙니다(web=$initial_web backend=$initial_backend ai=$initial_ai http=${initial_http:-000})"
responded "${initial_api:-000}" || die "시작 API 경로가 응답하지 않습니다(code=${initial_api:-000})"

for svc in "${services[@]}"; do
  ref="$(image_ref "$svc")"
  last_good_ids[$svc]="$(docker image inspect --format '{{.Id}}' "$ref:last-good" 2>/dev/null)" \
    || die "$ref:last-good 이미지가 없습니다"
  cid="$(compose ps -aq "$svc" | head -1)"
  [ -n "$cid" ] || die "$svc 컨테이너가 없습니다"
  running_id="$(docker inspect --format '{{.Image}}' "$cid")"
  [ "$running_id" = "${last_good_ids[$svc]}" ] \
    || die "$svc 실행 이미지와 :last-good이 다릅니다. drift를 먼저 해소하세요"
done
config_sha_before="$(sha256sum "$ENV_FILE" | awk '{print $1}')"
log "preflight=PASS services=4 health=PASS config_values_logged=false"

# 2. 사용자가 요구한 변경 전 snapshot. root만 읽고 recovery owner도 root로 고정한다.
bash "$snapshot_tool" create \
  --source "$ENV_FILE" \
  --snapshot-root "$SNAPSHOT_ROOT" \
  --snapshot-id "$snapshot_id" \
  --candidate "$CANDIDATE" \
  --recovery-owner root
snapshot_path="$SNAPSHOT_ROOT/$snapshot_id"
log "config_snapshot=PASS snapshot_id=$snapshot_id"

# 3. live 고객 data를 건드리지 않는 전용 schema에서 실제 pg_dump/pg_restore를 실행한다.
install -d -m 0700 -o root -g root "$STATE_DIR"
work_dir="$STATE_DIR/$snapshot_id"
install -d -m 0700 -o root -g root "$work_dir"
db_dump="$work_dir/rehearsal-schema.dump"
REPORT_FILE="${REPORT_FILE:-$work_dir/report.json}"

compose exec -T -e F14_SCHEMA="$rehearsal_schema" -e F14_MARKER="$marker" postgres sh -euc '
  case "$F14_SCHEMA" in f14_rehearsal_[a-f0-9]*) ;; *) exit 64 ;; esac
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<SQL >/dev/null
CREATE SCHEMA $F14_SCHEMA;
CREATE TABLE $F14_SCHEMA.rollback_marker (marker text PRIMARY KEY);
INSERT INTO $F14_SCHEMA.rollback_marker(marker) VALUES ('"'"'$F14_MARKER'"'"');
SQL
'
schema_created=1
compose exec -T -e F14_SCHEMA="$rehearsal_schema" postgres sh -euc \
  'pg_dump -Fc --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB" -n "$F14_SCHEMA"' \
  > "$db_dump"
chmod 0600 "$db_dump"

compose exec -T -e F14_SCHEMA="$rehearsal_schema" postgres sh -euc '
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "UPDATE $F14_SCHEMA.rollback_marker SET marker = '"'"'mutated'"'"'" >/dev/null
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP SCHEMA $F14_SCHEMA CASCADE" >/dev/null
'
compose exec -T postgres sh -euc \
  'pg_restore --exit-on-error --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < "$db_dump"
restored_marker="$(compose exec -T -e F14_SCHEMA="$rehearsal_schema" postgres sh -euc \
  'psql -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT marker FROM $F14_SCHEMA.rollback_marker"' | tr -d '\r\n')"
[ "$restored_marker" = "$marker" ] || die "DB restore marker가 원본과 다릅니다"
db_dump_sha="$(sha256sum "$db_dump" | awk '{print $1}')"
drop_rehearsal_schema
log "database_restore=PASS scope=dedicated_synthetic_schema customer_data_access=false dump_sha256=$db_dump_sha"

# 4. web:latest에 의도적 실패 이미지를 넣고 기존 deploy.sh의 실패 rollback을 실제로 밟는다.
postgres_cid="$(compose ps -q postgres | head -1)"
[ -n "$postgres_cid" ] || die "failure candidate로 사용할 실행 중 postgres 이미지가 없습니다"
failure_image="$(docker inspect --format '{{.Image}}' "$postgres_cid")"
docker tag "$failure_image" "$(image_ref web):latest"
failure_injected=1

set +e
ENV_FILE="$ENV_FILE" STATE_DIR="/srv/s15p21b109/state" AI_REQUIRED=1 \
  bash "$deploy_tool" --no-build --timeout "$TIMEOUT" \
    --health-url http://127.0.0.1:1/f14-intentional-failure \
    --api-health-url http://127.0.0.1:1/f14-intentional-failure
deploy_exit=$?
set -e
[ "$deploy_exit" -ne 0 ] || die "의도적 실패 배포가 성공으로 판정됐습니다"

for svc in "${services[@]}"; do
  cid="$(compose ps -aq "$svc" | head -1)"
  [ -n "$cid" ] || die "rollback 뒤 $svc 컨테이너가 없습니다"
  running_id="$(docker inspect --format '{{.Image}}' "$cid")"
  [ "$running_id" = "${last_good_ids[$svc]}" ] \
    || die "rollback 뒤 $svc 이미지가 :last-good으로 복원되지 않았습니다"
done
restore_latest_tags
failure_injected=0

deadline=$(( $(date +%s) + 90 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  final_web="$(health_of web)"; final_backend="$(health_of backend)"; final_ai="$(health_of ai-pjt)"
  final_http="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8080/ || true)"
  final_api="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8080/api/v1/consultations/ || true)"
  if [ "$final_web:$final_backend:$final_ai:$final_http" = 'healthy:healthy:healthy:200' ] && responded "${final_api:-000}"; then
    break
  fi
  sleep 5
done
[ "$final_web:$final_backend:$final_ai:$final_http" = 'healthy:healthy:healthy:200' ] \
  || die "rollback 후 health 복구 실패(web=$final_web backend=$final_backend ai=$final_ai http=${final_http:-000})"
responded "${final_api:-000}" || die "rollback 후 API 복구 실패(code=${final_api:-000})"

config_sha_after="$(sha256sum "$ENV_FILE" | awk '{print $1}')"
[ "$config_sha_after" = "$config_sha_before" ] || die "리허설 중 운영 app.env가 변경됐습니다"
bash "$snapshot_tool" verify --snapshot "$snapshot_path"

# 5. 값 없는 증거만 root 전용 JSON에 기록한다. 원문 snapshot은 Git/artifact로 복사하지 않는다.
host_name="$(hostname)"
executed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
umask 077
{
  printf '{\n'
  printf '  "schema_version": 1,\n'
  printf '  "task": "F14-T05",\n'
  printf '  "candidate": "%s",\n' "$CANDIDATE"
  printf '  "executed_at_utc": "%s",\n' "$executed_at"
  printf '  "server_host": "%s",\n' "$host_name"
  printf '  "config_snapshot": {"snapshot_id": "%s", "path": "%s", "sha256": "%s", "owner": "root:root", "mode": "0500/0400", "immutable": true, "recovery_owner": "root", "values_recorded": false},\n' "$snapshot_id" "$snapshot_path" "$config_sha_before"
  printf '  "database": {"scope": "dedicated_synthetic_schema", "customer_data_access": false, "dump_restore_exact": true, "dump_sha256": "%s", "cleanup": true},\n' "$db_dump_sha"
  printf '  "deployment": {"failure_injected": true, "expected_exit_nonzero": true, "services_restored_to_last_good": 4, "web": "%s", "backend": "%s", "ai_pjt": "%s", "http_status": %s, "api_status": %s},\n' "$final_web" "$final_backend" "$final_ai" "$final_http" "$final_api"
  printf '  "config_source_unchanged": true,\n'
  printf '  "secret_values_in_report": false,\n'
  printf '  "result": "PASS"\n'
  printf '}\n'
} > "$REPORT_FILE"
chmod 0600 "$REPORT_FILE"
report_written=1
log "deployment_rollback=PASS services=4 health=PASS"
log "result=PASS report=$REPORT_FILE secret_values_in_report=false snapshot=$snapshot_path"
