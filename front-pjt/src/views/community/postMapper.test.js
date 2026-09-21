import assert from 'node:assert/strict'
import test from 'node:test'

import { toViewPost, toViewComment, relativeTime, formatViews } from './postMapper.js'

const NOW = new Date('2026-01-15T12:00:00Z').getTime()

// ─── toViewPost ───────────────────────────────────────────

test('toViewPost는 서버 필드를 화면 뷰모델 필드로 매핑한다', () => {
  const view = toViewPost({
    id: 7,
    author: '홍길동',
    title: '제목',
    category: '투자',
    content: '미리보기 본문',
    sections: [{ type: 'component', name: 'BalanceSheetSlide', caption: '자산' }],
    like_count: 12,
    view_count: 340,
    comment_count: 3,
    liked: true,
    is_recommended: true,
    age_band: '30대',
    asset_band: '1~3억',
    income_band: '3~5천',
    created_at: '2026-01-15T11:59:30Z',
  })

  assert.equal(view.id, 7)
  assert.equal(view.category, '투자')
  assert.equal(view.body, '미리보기 본문')
  assert.deepEqual(view.sections, [{ type: 'component', name: 'BalanceSheetSlide', caption: '자산' }])
  assert.equal(view.author, '홍길동')
  assert.equal(view.age, '30대')
  assert.equal(view.asset, '1~3억')
  assert.equal(view.income, '3~5천')
  assert.equal(view.likes, 12)
  assert.equal(view.liked, true)
  assert.equal(view.comments, 3)
  assert.equal(view.views, 340)
  assert.equal(view.recommended, true)
  assert.deepEqual(view.commentList, [])
})

test('sections가 없거나 빈 배열이면 content 텍스트 섹션으로 fallback한다', () => {
  const noSections = toViewPost({ id: 1, title: 't', content: '본문' })
  assert.deepEqual(noSections.sections, [{ type: 'text', content: '본문' }])

  const emptySections = toViewPost({ id: 2, title: 't', content: '본문', sections: [] })
  assert.deepEqual(emptySections.sections, [{ type: 'text', content: '본문' }])

  const notArray = toViewPost({ id: 3, title: 't', content: '본문', sections: 'oops' })
  assert.deepEqual(notArray.sections, [{ type: 'text', content: '본문' }])
})

test('작성자·밴드·플래그 기본값 — 빈 문자열 author는 익명으로 방어한다', () => {
  const view = toViewPost({ id: 1, title: 't', author: '' })

  assert.equal(view.author, '익명')
  assert.equal(view.category, '기타')
  assert.equal(view.body, '')
  assert.equal(view.age, '')
  assert.equal(view.asset, '')
  assert.equal(view.income, '')
  assert.equal(view.likes, 0)
  assert.equal(view.liked, false)
  assert.equal(view.comments, 0)
  assert.equal(view.views, 0)
  assert.equal(view.recommended, false)
  assert.equal(view.createdAt, '방금 전')
})

// ─── toViewComment ────────────────────────────────────────

test('toViewComment는 댓글 필드를 매핑하고 is_mine을 불리언으로 강제한다', () => {
  const comment = toViewComment({
    id: 5,
    author: '익명',
    content: '좋은 글이네요',
    created_at: '2026-01-15T11:00:00Z',
    is_mine: 1,
  })

  assert.equal(comment.id, 5)
  assert.equal(comment.author, '익명')
  assert.equal(comment.text, '좋은 글이네요')
  assert.equal(comment.isMine, true)
})

test('toViewComment는 빈 author와 누락 필드를 방어한다', () => {
  const comment = toViewComment({ id: 6, author: '' })

  assert.equal(comment.author, '익명')
  assert.equal(comment.text, '')
  assert.equal(comment.isMine, false)
  assert.equal(comment.createdAt, '방금 전')
})

// ─── relativeTime ─────────────────────────────────────────

test('relativeTime 경계값 — nowMs 주입으로 검증', () => {
  const iso = (msAgo) => new Date(NOW - msAgo).toISOString()
  const MIN = 60 * 1000
  const HOUR = 60 * MIN
  const DAY = 24 * HOUR

  assert.equal(relativeTime(iso(0), NOW), '방금 전')
  assert.equal(relativeTime(iso(59 * 1000), NOW), '방금 전')
  assert.equal(relativeTime(iso(60 * 1000), NOW), '1분 전')
  assert.equal(relativeTime(iso(59 * MIN), NOW), '59분 전')
  assert.equal(relativeTime(iso(60 * MIN), NOW), '1시간 전')
  assert.equal(relativeTime(iso(23 * HOUR), NOW), '23시간 전')
  assert.equal(relativeTime(iso(24 * HOUR), NOW), '1일 전')
  assert.equal(relativeTime(iso(6 * DAY), NOW), '6일 전')
  assert.equal(relativeTime(iso(7 * DAY), NOW), '1주 전')
  assert.equal(relativeTime(iso(27 * DAY), NOW), '3주 전')
  assert.equal(
    relativeTime(iso(28 * DAY), NOW),
    new Date(NOW - 28 * DAY).toLocaleDateString('ko-KR'),
  )
})

test('relativeTime은 유효하지 않은 입력과 미래 시각을 방금 전으로 처리한다', () => {
  assert.equal(relativeTime(undefined, NOW), '방금 전')
  assert.equal(relativeTime(null, NOW), '방금 전')
  assert.equal(relativeTime('', NOW), '방금 전')
  assert.equal(relativeTime('not-a-date', NOW), '방금 전')
  assert.equal(relativeTime(new Date(NOW + 60 * 60 * 1000).toISOString(), NOW), '방금 전')
})

// ─── formatViews ──────────────────────────────────────────

test('formatViews는 1000 이상을 k 표기로 축약한다', () => {
  assert.equal(formatViews(0), '0')
  assert.equal(formatViews(999), '999')
  assert.equal(formatViews(1000), '1.0k')
  assert.equal(formatViews(2340), '2.3k')
})
