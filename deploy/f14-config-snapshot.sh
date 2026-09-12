#!/usr/bin/env bash
# F14-T05: 운영 app.env의 값은 출력하지 않고, root 전용 immutable snapshot을 만든다.
#
# create 예시:
#   sudo bash deploy/f14-config-snapshot.sh create \
#     --source /srv/s15p21b109/secrets/app.env \
#     --snapshot-root /srv/s15p21b109/secrets/snapshots \
#     --snapshot-id 20260912T120000Z-abcdef0
# verify 예시:
#   sudo bash deploy/f14-config-snapshot.sh verify \
#     --snapshot /srv/s15p21b109/secrets/snapshots/20260912T120000Z-abcdef0
#
# snapshot에는 app.env 원문이 포함되지만 root:root 0400 + filesystem immutable(+i)로
# 잠근다. stdout에는 식별자·경로·SHA-256·권한만 쓰고 설정 이름이나 값은 쓰지 않는다.
set -euo pipefail

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

die() { printf '[f14-config-snapshot] ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[f14-config-snapshot] %s\n' "$*"; }

CHATTR_BIN="${F14_CHATTR_BIN:-chattr}"
LSATTR_BIN="${F14_LSATTR_BIN:-lsattr}"

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "필수 명령을 찾을 수 없습니다: $1"
}

validate_id() {
  case "$1" in
    ''|*[!A-Za-z0-9._-]*) die "snapshot ID는 영문·숫자·점·밑줄·하이픈만 허용합니다" ;;
  esac
}

manifest_value() {
  local manifest="$1" key="$2"
  sed -n "s/^${key}=//p" "$manifest" | tail -1
}

is_immutable() {
  local path="$1" attrs
  attrs="$($LSATTR_BIN -d "$path" 2>/dev/null | awk '{print $1}')" || return 1
  case "$attrs" in *i*) return 0 ;; *) return 1 ;; esac
}

verify_snapshot() {
  local snapshot="$1" manifest payload
  local expected actual owner group mode path
  manifest="$snapshot/manifest.env"
  payload="$snapshot/app.env"

  [ -d "$snapshot" ] || die "snapshot 디렉터리가 없습니다: $snapshot"
  [ ! -L "$snapshot" ] || die "snapshot 디렉터리는 symlink일 수 없습니다: $snapshot"
  [ -f "$manifest" ] && [ ! -L "$manifest" ] || die "manifest가 없거나 symlink입니다"
  [ -f "$payload" ] && [ ! -L "$payload" ] || die "snapshot app.env가 없거나 symlink입니다"

  expected="$(manifest_value "$manifest" source_sha256)"
  [ -n "$expected" ] || die "manifest에 source_sha256이 없습니다"
  actual="$(sha256sum "$payload" | awk '{print $1}')"
  [ "$actual" = "$expected" ] || die "snapshot app.env SHA-256 불일치"

  for path in "$snapshot" "$manifest" "$payload"; do
    is_immutable "$path" || die "filesystem immutable(+i)이 아닙니다: $path"
  done

  owner="$(stat -c '%U' "$snapshot")"; group="$(stat -c '%G' "$snapshot")"; mode="$(stat -c '%a' "$snapshot")"
  [ "$owner:$group:$mode" = "$(manifest_value "$manifest" snapshot_owner):$(manifest_value "$manifest" snapshot_group):500" ] \
    || die "snapshot 디렉터리 권한 불일치: $owner:$group $mode"
  owner="$(stat -c '%U' "$payload")"; group="$(stat -c '%G' "$payload")"; mode="$(stat -c '%a' "$payload")"
  [ "$owner:$group:$mode" = "$(manifest_value "$manifest" snapshot_owner):$(manifest_value "$manifest" snapshot_group):400" ] \
    || die "snapshot app.env 권한 불일치: $owner:$group $mode"
  owner="$(stat -c '%U' "$manifest")"; group="$(stat -c '%G' "$manifest")"; mode="$(stat -c '%a' "$manifest")"
  [ "$owner:$group:$mode" = "$(manifest_value "$manifest" snapshot_owner):$(manifest_value "$manifest" snapshot_group):400" ] \
    || die "snapshot manifest 권한 불일치: $owner:$group $mode"

  log "verify=PASS snapshot_id=$(manifest_value "$manifest" snapshot_id) path=$snapshot sha256=$actual owner=$(manifest_value "$manifest" snapshot_owner):$(manifest_value "$manifest" snapshot_group) mode=0500/0400 immutable=+i recovery_owner=$(manifest_value "$manifest" recovery_owner)"
}

create_snapshot() {
  local source='' snapshot_root='' snapshot_id='' candidate='unknown'
  local source_owner='root' source_group='gitlab-runner' source_mode='640'
  local snapshot_owner='root' snapshot_group='root' recovery_owner='root'
  local source_actual_owner source_actual_group source_actual_mode source_sha created_at
  local tmp='' final='' manifest='' payload=''

  while [ $# -gt 0 ]; do
    case "$1" in
      --source) source="${2:?--source 값이 필요합니다}"; shift 2 ;;
      --snapshot-root) snapshot_root="${2:?--snapshot-root 값이 필요합니다}"; shift 2 ;;
      --snapshot-id) snapshot_id="${2:?--snapshot-id 값이 필요합니다}"; shift 2 ;;
      --candidate) candidate="${2:?--candidate 값이 필요합니다}"; shift 2 ;;
      --source-owner) source_owner="${2:?--source-owner 값이 필요합니다}"; shift 2 ;;
      --source-group) source_group="${2:?--source-group 값이 필요합니다}"; shift 2 ;;
      --source-mode) source_mode="${2:?--source-mode 값이 필요합니다}"; shift 2 ;;
      --snapshot-owner) snapshot_owner="${2:?--snapshot-owner 값이 필요합니다}"; shift 2 ;;
      --snapshot-group) snapshot_group="${2:?--snapshot-group 값이 필요합니다}"; shift 2 ;;
      --recovery-owner) recovery_owner="${2:?--recovery-owner 값이 필요합니다}"; shift 2 ;;
      *) die "알 수 없는 create 인자: $1" ;;
    esac
  done

  [ -n "$source" ] && [ -n "$snapshot_root" ] && [ -n "$snapshot_id" ] \
    || die "create에는 --source, --snapshot-root, --snapshot-id가 필요합니다"
  validate_id "$snapshot_id"
  [ -f "$source" ] && [ ! -L "$source" ] || die "source가 일반 파일이 아니거나 symlink입니다: $source"

  source_actual_owner="$(stat -c '%U' "$source")"
  source_actual_group="$(stat -c '%G' "$source")"
  source_actual_mode="$(stat -c '%a' "$source")"
  [ "$source_actual_owner:$source_actual_group:$source_actual_mode" = "$source_owner:$source_group:$source_mode" ] \
    || die "source 권한은 $source_owner:$source_group $source_mode 이어야 합니다(현재 $source_actual_owner:$source_actual_group $source_actual_mode)"

  install -d -m 0700 -o "$snapshot_owner" -g "$snapshot_group" "$snapshot_root"
  [ ! -e "$snapshot_root/$snapshot_id" ] || die "같은 snapshot ID가 이미 있습니다: $snapshot_id"
  tmp="$(mktemp -d "$snapshot_root/.tmp.${snapshot_id}.XXXXXX")"
  final="$snapshot_root/$snapshot_id"
  manifest="$tmp/manifest.env"
  payload="$tmp/app.env"
  cleanup_failed_create() {
    [ -n "$tmp" ] && [ -d "$tmp" ] || return 0
    "$CHATTR_BIN" -i "$tmp/app.env" "$tmp/manifest.env" "$tmp" >/dev/null 2>&1 || true
    rm -f "$tmp/app.env" "$tmp/manifest.env"
    rmdir "$tmp" 2>/dev/null || true
  }
  trap cleanup_failed_create RETURN

  install -m 0400 -o "$snapshot_owner" -g "$snapshot_group" "$source" "$payload"
  source_sha="$(sha256sum "$payload" | awk '{print $1}')"
  created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  {
    printf 'format_version=1\n'
    printf 'snapshot_id=%s\n' "$snapshot_id"
    printf 'created_at_utc=%s\n' "$created_at"
    printf 'candidate=%s\n' "$candidate"
    printf 'source_path=%s\n' "$source"
    printf 'source_sha256=%s\n' "$source_sha"
    printf 'source_owner=%s\n' "$source_owner"
    printf 'source_group=%s\n' "$source_group"
    printf 'source_mode=%s\n' "$source_mode"
    printf 'snapshot_owner=%s\n' "$snapshot_owner"
    printf 'snapshot_group=%s\n' "$snapshot_group"
    printf 'recovery_owner=%s\n' "$recovery_owner"
  } > "$manifest"
  chown "$snapshot_owner:$snapshot_group" "$manifest" "$tmp"
  chmod 0400 "$manifest"
  chmod 0500 "$tmp"
  mv "$tmp" "$final"
  tmp=''
  manifest="$final/manifest.env"
  payload="$final/app.env"

  "$CHATTR_BIN" +i "$payload" "$manifest"
  "$CHATTR_BIN" +i "$final"
  verify_snapshot "$final"
  log "create=PASS values_logged=false git_stored=false"
}

restore_snapshot() {
  local snapshot='' target='' confirm=''
  local manifest snapshot_id owner group mode tmp expected actual
  while [ $# -gt 0 ]; do
    case "$1" in
      --snapshot) snapshot="${2:?--snapshot 값이 필요합니다}"; shift 2 ;;
      --target) target="${2:?--target 값이 필요합니다}"; shift 2 ;;
      --confirm-snapshot-id) confirm="${2:?--confirm-snapshot-id 값이 필요합니다}"; shift 2 ;;
      *) die "알 수 없는 restore 인자: $1" ;;
    esac
  done
  [ -n "$snapshot" ] && [ -n "$target" ] && [ -n "$confirm" ] \
    || die "restore에는 --snapshot, --target, --confirm-snapshot-id가 필요합니다"
  verify_snapshot "$snapshot"
  manifest="$snapshot/manifest.env"
  snapshot_id="$(manifest_value "$manifest" snapshot_id)"
  [ "$confirm" = "$snapshot_id" ] || die "확인한 snapshot ID가 실제 ID와 다릅니다"
  owner="$(manifest_value "$manifest" source_owner)"
  group="$(manifest_value "$manifest" source_group)"
  mode="$(manifest_value "$manifest" source_mode)"
  expected="$(manifest_value "$manifest" source_sha256)"
  install -d -m 0750 -o "$owner" -g "$group" "$(dirname "$target")"
  tmp="$(mktemp "$(dirname "$target")/.restore.${snapshot_id}.XXXXXX")"
  trap 'rm -f "${tmp:-}"' RETURN
  install -m "$mode" -o "$owner" -g "$group" "$snapshot/app.env" "$tmp"
  actual="$(sha256sum "$tmp" | awk '{print $1}')"
  [ "$actual" = "$expected" ] || die "restore 임시 파일 SHA-256 불일치"
  mv -f "$tmp" "$target"
  tmp=''
  actual="$(sha256sum "$target" | awk '{print $1}')"
  [ "$actual" = "$expected" ] || die "restore 대상 SHA-256 불일치"
  log "restore=PASS snapshot_id=$snapshot_id target=$target sha256=$actual owner=$owner:$group mode=$mode"
}

require_command sha256sum
require_command stat
require_command install
require_command "$CHATTR_BIN"
require_command "$LSATTR_BIN"

command_name="${1:-}"
[ -n "$command_name" ] || usage 2
shift
case "$command_name" in
  create) create_snapshot "$@" ;;
  verify)
    [ "${1:-}" = --snapshot ] && [ -n "${2:-}" ] && [ "$#" -eq 2 ] || die "verify에는 --snapshot <경로>만 필요합니다"
    verify_snapshot "$2"
    ;;
  restore) restore_snapshot "$@" ;;
  -h|--help) usage 0 ;;
  *) die "알 수 없는 명령: $command_name" ;;
esac
