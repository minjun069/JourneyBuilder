// 재무 지표 공통 스키마
// 대시보드와 프레젠테이션 슬라이드에서 공유
// value, age는 각 사용처에서 주입

function bracket(age) {
  if (!age || age < 30) return '20s'
  if (age < 40) return '30s'
  if (age < 50) return '40s'
  if (age < 65) return '50s'
  return '65+'
}

export const INDICATOR_SCHEMA = {

  // 가계수지지표: (수입-지출)/수입 — 높을수록 좋음
  // 양호 기준(저축률): 20대 50%, 30대 30%, 40대 20%, 50대 10%, 65+ 5%
  // (사용자 제시 기준 50/70/80/90/95%를 지출률로 보고 역산)
  가계수지지표: {
    key: '가계수지지표', label: '가계수지지표', description: '(수입 - 지출) / 수입',
    unit: '%', min: 0, max: 100,
    getZones: (age) => {
      const t = { '20s': 50, '30s': 30, '40s': 20, '50s': 10, '65+': 5 }[bracket(age)]
      return [
        { from: -20, to: t / 2, color: '#ef5350', status: '위험' },
        { from: t / 2, to: t,   color: '#ffa726', status: '주의' },
        { from: t,    to: 100,  color: '#66bb6a', status: '양호' },
      ]
    },
  },

  // 총저축성향지표: 저축액/수입 — 높을수록 좋음
  // 양호 기준: 20대 50%, 30대 30%, 40대 20%, 50대 10%, 65+ 5%
  총저축성향지표: {
    key: '총저축성향지표', label: '총저축성향지표', description: '저축액 / 수입',
    unit: '%', min: 0, max: 100,
    getZones: (age) => {
      const t = { '20s': 50, '30s': 30, '40s': 20, '50s': 10, '65+': 5 }[bracket(age)]
      return [
        { from: 0,    to: t / 2, color: '#ef5350', status: '위험' },
        { from: t / 2, to: t,   color: '#ffa726', status: '주의' },
        { from: t,    to: 100,   color: '#66bb6a', status: '양호' },
      ]
    },
  },

  // 금융자산비중지표: 금융자산/총자산 — 40% 이상 양호 (연령 무관)
  금융자산비중지표: {
    key: '금융자산비중지표', label: '금융자산비중지표', description: '금융자산 / 총자산',
    unit: '%', min: 0, max: 100,
    zones: [
      { from: 0,  to: 20, color: '#ef5350', status: '위험' },
      { from: 20, to: 40, color: '#ffa726', status: '주의' },
      { from: 40, to: 100, color: '#66bb6a', status: '양호' },
    ],
  },

  // 총부채상환지표: 연간부채상환/연간소득 — 낮을수록 좋음
  // 양호 기준(이하): 20~40대 25%, 50대 15%, 65+ 5%
  총부채상환지표: {
    key: '총부채상환지표', label: '총부채상환지표', description: '연간 부채상환액 / 연간소득',
    unit: '%', min: 0, max: 100,
    getZones: (age) => {
      const t = { '20s': 25, '30s': 25, '40s': 25, '50s': 15, '65+': 5 }[bracket(age)]
      return [
        { from: 0,      to: t,      color: '#66bb6a', status: '양호' },
        { from: t,      to: t * 1.6, color: '#ffa726', status: '주의' },
        { from: t * 1.6, to: 100,   color: '#ef5350', status: '위험' },
      ]
    },
  },

  // 노후대비저축지표: 노후저축/월소득 — 50% 이상 양호 (연령 무관)
  노후대비저축지표: {
    key: '노후대비저축지표', label: '노후대비저축지표', description: '노후저축액 / 월소득',
    unit: '%', min: 0, max: 100,
    zones: [
      { from: 0,  to: 25, color: '#ef5350', status: '위험' },
      { from: 25, to: 50, color: '#ffa726', status: '주의' },
      { from: 50, to: 100, color: '#66bb6a', status: '양호' },
    ],
  },

  // 비상예비금지표: 비상예비금/월지출 (개월) — 높을수록 좋음
  // 양호 기준: 20대 2개월, 30대 3개월, 40대 4개월, 50대 5개월, 65+ 6개월
  비상예비금지표: {
    key: '비상예비금지표', label: '비상예비금지표', description: '비상예비금 / 월지출',
    unit: '개월', min: 0, max: 12,
    getZones: (age) => {
      const t = { '20s': 2, '30s': 3, '40s': 4, '50s': 5, '65+': 6 }[bracket(age)]
      return [
        { from: 0,    to: t / 2, color: '#ef5350', status: '위험' },
        { from: t / 2, to: t,   color: '#ffa726', status: '주의' },
        { from: t,    to: 12,   color: '#66bb6a', status: '양호' },
      ]
    },
  },

  // 보장성보험준비지표: 보험료/월소득 — 8~10% 양호
  보장성보험준비지표: {
    key: '보장성보험준비지표', label: '보장성보험준비지표', description: '보험료 / 월소득',
    unit: '%', min: 0, max: 20,
    zones: [
      { from: 0,  to: 5,  color: '#ef5350', status: '위험' },
      { from: 5,  to: 8,  color: '#ffa726', status: '주의' },
      { from: 8,  to: 20, color: '#66bb6a', status: '양호' },
    ],
  },
}

export function makeIndicator(key, value, age) {
  const schema = INDICATOR_SCHEMA[key]
  const zones = schema.getZones ? schema.getZones(age ?? 35) : schema.zones
  return { ...schema, value, zones }
}
