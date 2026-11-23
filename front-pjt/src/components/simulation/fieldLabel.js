// 서버가 주는 field 경로(pension_assets[1].expected_amount)를 사람이 읽을 위치로 바꾼다.
// 422 의 issues 와 계산 결과의 notices 가 같은 경로 표기를 쓰므로 함께 쓴다.
const SECTION_LABELS = {
  basic_info: '기본 정보',
  financial_assets: '금융자산',
  pension_assets: '연금',
  goal_periods: '은퇴 후 생활비',
  changes: '변경 조건',
}

// 서버 메시지는 "예상 월 수령액을(를) 입력해 주세요." 처럼 어느 행인지 말하지 않는다.
export function sectionLabel(field) {
  const matched = /^([a-z_]+)(?:\[(\d+)\])?/.exec(typeof field === 'string' ? field : '')
  const label = matched ? SECTION_LABELS[matched[1]] : null
  if (!label) return ''
  return matched[2] == null ? label : `${label} ${Number(matched[2]) + 1}번째 항목`
}
