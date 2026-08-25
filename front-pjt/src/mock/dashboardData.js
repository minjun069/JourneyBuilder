import { makeIndicator } from '@/components/charts/financialIndicatorSchema'

export const PERIODS = [
  '2024년 7월', '2024년 8월', '2024년 9월', '2024년 10월', '2024년 11월', '2024년 12월',
  '2025년 1월', '2025년 2월', '2025년 3월', '2025년 4월', '2025년 5월', '2025년 6월',
]

const GRADES = ['C+', 'C+', 'B-', 'B-', 'B', 'B', 'B', 'B+', 'B+', 'B+', 'A-', 'A-']

function buildIndicators(i) {
  return {
    cashflow: [
      makeIndicator('가계수지지표',   parseFloat((25.9 + i * 0.3).toFixed(1)), 35),
      makeIndicator('총저축성향지표', parseFloat((20.7 + i * 0.2).toFixed(1)), 35),
    ],
    assets: [
      makeIndicator('금융자산비중지표', parseFloat((28.1 + i * 0.2).toFixed(1))),
      makeIndicator('총부채상환지표',   parseFloat((24.8 - i * 0.1).toFixed(1)), 35),
      makeIndicator('노후대비저축지표', parseFloat((12.3 + i * 0.2).toFixed(1))),
    ],
    risk: [
      makeIndicator('비상예비금지표',    parseFloat((4.5  + i * 0.1).toFixed(1)), 35),
      makeIndicator('보장성보험준비지표', parseFloat((7.1  + i * 0.05).toFixed(1))),
    ],
  }
}

export const periodData = Object.fromEntries(
  PERIODS.map((period, i) => [
    period,
    {
      retirementScore: { score: 72 + i, grade: GRADES[i] },
      aiComment: `현재 은퇴 준비 점수는 ${72 + i}점(${GRADES[i]}) 수준으로, 꾸준한 개선이 이루어지고 있습니다. 부동산 비중이 전체 자산의 60% 이상을 차지해 유동성 리스크가 존재하며, 금융자산·연금자산 비중을 점진적으로 확대하는 포트폴리오 재편이 필요합니다. 비상예비금은 ${(4.5 + i * 0.1).toFixed(1)}개월치로 목표(6개월)에 근접하고 있으니 현재 저축 기조를 유지하시기 바랍니다.`,
      assets: {
        total: 575000000 + i * 5000000,
        debt: 156000000 - i * 1000000,
        net: 419000000 + i * 6000000,
        breakdown: [
          { category: '현금', amount: 28000000 + i * 200000, color: '#5e5ce6' },
          { category: '실물자산', amount: 48000000 + i * 300000, color: '#ff9f40' },
          { category: '투자자산', amount: 75000000 + i * 800000, color: '#4bc0c0' },
          { category: '부동산', amount: 370000000 - i * 1000000, color: '#ff6384' },
          { category: '연금자산', amount: 54000000 + i * 1700000, color: '#36a2eb' },
        ],
      },
      cashflow: {
        inflow: 5800000 + i * 30000,
        outflow: 4300000 - i * 10000,
      },
      financialIndicators: buildIndicators(i),
    },
  ])
)

export const monthlyHistory = PERIODS.map((period, i) => ({
  period,
  inflow: 5800000 + i * 30000,
  outflow: 4300000 - i * 10000,
}))
