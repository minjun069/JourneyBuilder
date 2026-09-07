#!/usr/bin/env bash
# 루트 .env(또는 서버 app.env)를 "처음 한 번" 만든다.
#
#   bash deploy/make-env.sh                                   # 루트 .env (로컬 개발)
#   bash deploy/make-env.sh --output /srv/s15p21b109/secrets/app.env   # 서버 (sudo 필요)
#   GOOGLE_API_KEY=... bash deploy/make-env.sh                # 키를 미리 넣어 생성 (셸 히스토리에 남으니 서버에서는 편집기 입력 권장)
#
# 하는 일
#   - .env.example 을 그대로 복사하고 필수 4개만 채운다: DJANGO_SECRET_KEY / POSTGRES_PASSWORD / AI_SERVICE_BEARER_TOKEN 은
#     openssl 무작위값, DATABASE_URL 은 POSTGRES_* 로 조립. GOOGLE_API_KEY 는 환경변수에 있으면 넣고 없으면 비워 둔다.
#   - 결과 파일은 600. 이미 파일이 있으면 절대 덮어쓰지 않는다 — POSTGRES_PASSWORD 는 볼륨과 짝이라 재생성하면 접속이 끊긴다.
set -euo pipefail

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

out=".env"
while [ $# -gt 0 ]; do
  case "$1" in
    --output|-o) out="${2:?--output 경로가 필요합니다}"; shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "알 수 없는 인자: $1" >&2; usage 1 ;;
  esac
done

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
template="$repo_root/.env.example"
[ -f "$template" ] || { echo "서식이 없습니다: $template" >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl 이 필요합니다 (apt install openssl)" >&2; exit 1; }

if [ -e "$out" ]; then
  echo "이미 존재합니다: $out" >&2
  echo "덮어쓰지 않습니다. POSTGRES_PASSWORD 는 기존 postgres_data 볼륨과 짝이라 재생성하면 DB 접속이 끊깁니다." >&2
  echo "정말 새로 만들려면 파일을 직접 옮긴 뒤 다시 실행하세요 (서버에서는 DB 의 ALTER USER 를 먼저)." >&2
  exit 2
fi

# 값 생성. hex 만 쓰면 URL(DATABASE_URL)과 .env 파싱에 특수문자 문제가 없다.
django_secret="$(openssl rand -hex 32)"           # 64자
postgres_password="$(openssl rand -hex 24)"       # 48자
bearer_token="$(openssl rand -hex 32)"            # 64자

postgres_db="$(grep -E '^POSTGRES_DB=' "$template" | cut -d= -f2-)"
postgres_user="$(grep -E '^POSTGRES_USER=' "$template" | cut -d= -f2-)"
database_url="postgresql://${postgres_user}:${postgres_password}@postgres:5432/${postgres_db}"
google_key="${GOOGLE_API_KEY:-}"

umask 077
tmp="$(mktemp "${out}.XXXXXX")"
sed \
  -e "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=${django_secret}|" \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${postgres_password}|" \
  -e "s|^AI_SERVICE_BEARER_TOKEN=.*|AI_SERVICE_BEARER_TOKEN=${bearer_token}|" \
  -e "s|^DATABASE_URL=.*|DATABASE_URL=${database_url}|" \
  -e "s|^GOOGLE_API_KEY=.*|GOOGLE_API_KEY=${google_key}|" \
  "$template" > "$tmp"
mv "$tmp" "$out"
chmod 600 "$out"

echo "생성: $out (600)"
echo "  DJANGO_SECRET_KEY / POSTGRES_PASSWORD / AI_SERVICE_BEARER_TOKEN : 무작위 생성"
echo "  DATABASE_URL : ${postgres_user}@postgres:5432/${postgres_db} 로 조립"
if [ -z "$google_key" ]; then
  echo "  GOOGLE_API_KEY : 비어 있음 → 편집기로 직접 입력하세요 (ai-pjt 는 이 값 없이 기동하지 않습니다)"
fi
echo
echo "주의: 이 파일은 다시 만들지 않습니다. POSTGRES_PASSWORD 는 첫 기동 때 DB 볼륨에 굳습니다."
echo "      서버에서는 'docker compose down -v' 를 쓰지 마세요 (DB 전체 삭제)."
echo "확인: docker compose --env-file $out config --quiet"
