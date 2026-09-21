// 커뮤니티 서버 응답 → 화면 뷰모델 순수 매퍼.
// node --test가 직접 로드하는 파일이므로 import 없이 유지한다.

// 조회수 축약 표기 (1000 이상 → 1.2k)
export function formatViews(n) {
  return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n)
}

// ISO 시각 → '방금 전/n분 전/n시간 전/n일 전/n주 전/날짜' 상대 표기
export function relativeTime(iso, nowMs = Date.now()) {
  if (!iso) return '방금 전'
  const ts = new Date(iso).getTime()
  if (Number.isNaN(ts)) return '방금 전'

  const sec = Math.floor((nowMs - ts) / 1000)
  if (sec < 60) return '방금 전'

  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}분 전`

  const hour = Math.floor(min / 60)
  if (hour < 24) return `${hour}시간 전`

  const day = Math.floor(hour / 24)
  if (day < 7) return `${day}일 전`
  if (day < 28) return `${Math.floor(day / 7)}주 전`

  return new Date(iso).toLocaleDateString('ko-KR')
}

// ISO 시각 → 정렬용 숫자 키. 누락/파싱 불가 값은 0으로 떨어뜨려 정렬이 깨지지 않게 한다
export function createdAtMs(iso) {
  const ts = Date.parse(iso)
  return Number.isNaN(ts) ? 0 : ts
}

// 서버 응답(content/like_count/view_count)을 화면이 쓰는 형태(body/likes/views)로 변환한다
export function toViewPost(data) {
  return {
    id: data.id,
    category: data.category || '기타',
    title: data.title,
    body: data.content ?? '',
    sections:
      Array.isArray(data.sections) && data.sections.length
        ? data.sections
        : [{ type: 'text', content: data.content ?? '' }],
    author: data.author || '익명', // 빈 문자열 name 방어 — || 유지
    age: data.age_band || '',
    asset: data.asset_band || '',
    income: data.income_band || '',
    likes: data.like_count ?? 0,
    liked: !!data.liked,
    comments: data.comment_count ?? 0,
    views: data.view_count ?? 0,
    recommended: !!data.is_recommended,
    isMine: !!data.is_mine,
    createdAt: relativeTime(data.created_at),
    createdAtMs: createdAtMs(data.created_at),
    commentList: [],
  }
}

// 댓글 응답 → 화면 댓글 형태
export function toViewComment(data) {
  return {
    id: data.id,
    author: data.author || '익명',
    text: data.content ?? '',
    createdAt: relativeTime(data.created_at),
    isMine: !!data.is_mine,
  }
}
