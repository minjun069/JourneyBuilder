// 세그먼트 버킷 키 ↔ 한글 라벨, 크로스 필터 쿼리 매핑 (FR-07)

export const AXIS_LABELS = {
  age_band: '연령대',
  need: '주요 니즈',
  tendency_group: '투자 성향',
  net_worth_band: '자산 규모',
  health: '재무 건전성',
  session_status: '상담 진행',
}

export const BUCKET_LABELS = {
  age_band: { '20s': '20대', '30s': '30대', '40s': '40대', '50s': '50대', '65+': '65세+', unknown: '미입력' },
  need: { 은퇴: '은퇴', 투자: '투자', 주택자금: '주택자금', 교육자금: '교육자금', 보장: '보장', 기타: '기타', unknown: '미입력' },
  tendency_group: { stable: '안정형', balanced: '균형형', aggressive: '공격형', unknown: '미입력' },
  net_worth_band: {
    under_50m: '5천만 미만', '50m_200m': '5천만~2억', '200m_500m': '2억~5억',
    '500m_1b': '5억~10억', over_1b: '10억+', unknown: '미입력',
  },
  health: { healthy: '건전', caution: '주의', risky: '위험', unknown: '미입력' },
  session_status: { onboarding: '온보딩', in_progress: '진행 중', completed: '완료', abandoned: '이탈', none: '세션 없음' },
}

// 축 → 고객 리스트 쿼리 파라미터 이름
export const AXIS_QUERY_PARAM = {
  age_band: 'age_band',
  need: 'need',
  tendency_group: 'tendency',
  net_worth_band: 'net_worth_band',
  health: 'health',
  session_status: 'status',
}

export function bucketLabel(axis, bucket) {
  return BUCKET_LABELS[axis]?.[bucket] ?? bucket
}
