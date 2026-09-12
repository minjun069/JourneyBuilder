#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
tool="$repo_root/deploy/f14-config-snapshot.sh"
tmp="$(mktemp -d)"
cleanup() {
  chmod -R u+rwX "$tmp" 2>/dev/null || true
  rm -rf "$tmp"
}
trap cleanup EXIT

owner="$(id -un)"
group="$(id -gn)"
source_file="$tmp/app.env"
snapshot_root="$tmp/snapshots"
restore_dir="$tmp/restore"
secret_sentinel='F14_SECRET_VALUE_MUST_NOT_APPEAR'
printf 'SECRET=%s\n' "$secret_sentinel" > "$source_file"
chmod 0640 "$source_file"

fake_chattr="$tmp/chattr"
fake_lsattr="$tmp/lsattr"
attr_log="$tmp/chattr.log"
cat > "$fake_chattr" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$F14_TEST_ATTR_LOG"
SH
cat > "$fake_lsattr" <<'SH'
#!/usr/bin/env bash
printf '%s %s\n' '----i---------------' "${@: -1}"
SH
chmod +x "$fake_chattr" "$fake_lsattr"

export F14_CHATTR_BIN="$fake_chattr"
export F14_LSATTR_BIN="$fake_lsattr"
export F14_TEST_ATTR_LOG="$attr_log"

create_log="$tmp/create.log"
bash "$tool" create \
  --source "$source_file" \
  --snapshot-root "$snapshot_root" \
  --snapshot-id before-change \
  --candidate 0123456789abcdef \
  --source-owner "$owner" \
  --source-group "$group" \
  --source-mode 640 \
  --snapshot-owner "$owner" \
  --snapshot-group "$group" \
  --recovery-owner "$owner" > "$create_log"

snapshot="$snapshot_root/before-change"
test -f "$snapshot/app.env"
test -f "$snapshot/manifest.env"
test "$(stat -c '%a' "$snapshot")" = 500
test "$(stat -c '%a' "$snapshot/app.env")" = 400
test "$(stat -c '%a' "$snapshot/manifest.env")" = 400
test "$(sha256sum "$source_file" | awk '{print $1}')" = "$(sed -n 's/^source_sha256=//p' "$snapshot/manifest.env")"
grep -q '^recovery_owner='"$owner"'$' "$snapshot/manifest.env"
grep -q '^+i .*app.env .*manifest.env$' "$attr_log"
grep -q '^+i .*before-change$' "$attr_log"
if grep -q "$secret_sentinel" "$create_log"; then
  echo 'snapshot 로그가 설정값을 노출했습니다' >&2
  exit 1
fi

verify_log="$tmp/verify.log"
bash "$tool" verify --snapshot "$snapshot" > "$verify_log"
grep -q 'verify=PASS' "$verify_log"
if grep -q "$secret_sentinel" "$verify_log"; then
  echo 'verify 로그가 설정값을 노출했습니다' >&2
  exit 1
fi

mkdir -p "$restore_dir"
restore_target="$restore_dir/app.env"
printf 'SECRET=changed\n' > "$restore_target"
chmod 0640 "$restore_target"
bash "$tool" restore \
  --snapshot "$snapshot" \
  --target "$restore_target" \
  --confirm-snapshot-id before-change > "$tmp/restore.log"
cmp -s "$source_file" "$restore_target"
test "$(stat -c '%a' "$restore_target")" = 640
if grep -q "$secret_sentinel" "$tmp/restore.log"; then
  echo 'restore 로그가 설정값을 노출했습니다' >&2
  exit 1
fi

if bash "$tool" create \
  --source "$source_file" \
  --snapshot-root "$snapshot_root" \
  --snapshot-id before-change \
  --source-owner "$owner" --source-group "$group" --source-mode 640 \
  --snapshot-owner "$owner" --snapshot-group "$group" >/dev/null 2>&1; then
  echo '기존 snapshot ID를 덮어썼습니다' >&2
  exit 1
fi

if bash "$tool" restore \
  --snapshot "$snapshot" \
  --target "$restore_target" \
  --confirm-snapshot-id wrong-id >/dev/null 2>&1; then
  echo '잘못 확인한 snapshot ID로 복원했습니다' >&2
  exit 1
fi

chmod 0600 "$snapshot/app.env"
printf 'tampered\n' > "$snapshot/app.env"
if bash "$tool" verify --snapshot "$snapshot" >/dev/null 2>&1; then
  echo '변조된 snapshot을 통과시켰습니다' >&2
  exit 1
fi

echo 'F14 config snapshot tests passed'
