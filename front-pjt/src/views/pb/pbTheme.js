// PB 화면 공용 시각 토큰 — 차트 팔레트·아바타 색·축 스타일을 한곳에서 관리

export const CHART_COLORS = ['#5e5ce6', '#22b8a6', '#f2a54a', '#e56399', '#4f9cf7', '#8f93a8']

export const GRADE_COLORS = { 양호: '#3eb37f', 주의: '#f2a54a', 위험: '#e05661' }

const AVATAR_PALETTE = [
  { bg: '#eeeefc', fg: '#5e5ce6' },
  { bg: '#e2f6f2', fg: '#1d9a8a' },
  { bg: '#fdf1e0', fg: '#c07c1d' },
  { bg: '#fcebf2', fg: '#c74d7f' },
  { bg: '#e7f0fd', fg: '#3577d4' },
  { bg: '#efeff4', fg: '#5d6070' },
]

export function avatarStyle(name) {
  const text = name || '?'
  let hash = 0
  for (const ch of text) hash = (hash * 31 + ch.charCodeAt(0)) % 997
  const { bg, fg } = AVATAR_PALETTE[hash % AVATAR_PALETTE.length]
  return { background: bg, color: fg }
}

export function initialOf(name) {
  return (name || '?').trim().charAt(0)
}

// ECharts 공통 축·격자 스타일
export const AXIS_TEXT = { color: '#8a8ca3', fontSize: 11 }
export const SPLIT_LINE = { lineStyle: { color: '#f0f0f7' } }
