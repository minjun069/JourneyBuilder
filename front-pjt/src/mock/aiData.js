// 백엔드 API 응답 구조와 동일한 형태의 더미 데이터

// ── 프레젠테이션 슬라이드용 사전 계산 데이터 ────────────────
// 백엔드 GET /consultations/{id}/presentation/ 응답 shape와 동일하게 맞출 예정
// 컬럼명·구조는 백엔드 확정 후 조정

export const MOCK_PRESENTATION_DATA = {

  // 재무상태표 슬라이드
  asset_statement: {
    total_assets:      499871099,
    total_liabilities: 88000000,
    net_worth:         411871099,
    debt_ratio:        17.6,
    asset_groups: [
      { key: 'cash',    label: '현금/저축성 자산', total: 75071099,
        items: [
          { label: '정기예금',   value: 7571099  },
          { label: '정기적금',   value: 47500000 },
          { label: '요구불예금', value: 20000000 },
        ],
      },
      { key: 'invest',  label: '투자자산',    total: 0,          items: [] },
      { key: 'estate',  label: '부동산',      total: 400000000,
        items: [{ label: '자가 아파트', value: 400000000 }],
      },
      { key: 'pension', label: '연금자산',    total: 24800000,
        items: [
          { label: '국민연금',      value: 1800000  },
          { label: '회사 퇴직연금', value: 15000000 },
          { label: '개인연금저축',  value: 8000000  },
        ],
      },
    ],
    liability_groups: [
      { key: 'short', label: '단기부채',   total: 0,         items: [] },
      { key: 'long',  label: '중장기부채', total: 88000000,
        items: [{ label: '주택담보대출', value: 88000000 }],
      },
    ],
  },

  // 현금흐름표 슬라이드
  cash_flow: {
    total_income:   4200000,
    total_expense:  2320000,
    surplus:        1880000,
    surplus_ratio:  44.8,
    income_groups: [
      { key: '근로소득', label: '근로소득', total: 3500000,
        items: [{ label: '급여', value: 3500000 }],
      },
      { key: '기타', label: '기타', total: 700000,
        items: [{ label: '부업 수입', value: 700000 }],
      },
    ],
    expense_groups: [
      { key: 'fixed',    label: '고정 지출', total: 1400000,
        items: [
          { label: '주거비',   value: 800000 },
          { label: '보험료',   value: 300000 },
          { label: '대출상환', value: 300000 },
        ],
      },
      { key: 'variable', label: '변동 지출', total: 920000,
        items: [
          { label: '식비',   value: 600000 },
          { label: '교통비', value: 200000 },
          { label: '기타',   value: 120000 },
        ],
      },
    ],
  },

  // 재무 핵심 지표 슬라이드 — value만 전달, zones는 프론트 schema에서 주입
  'financial-indicators': {
    age: 35,
    groups: {
      cashflow: [
        { key: '가계수지지표',   value: 44.8 },
        { key: '총저축성향지표', value: 23.3 },
      ],
      assets: [
        { key: '금융자산비중지표', value: 20.0 },
        { key: '총부채상환지표',   value: 7.1  },
        { key: '노후대비저축지표', value: 11.4 },
      ],
      risk: [
        { key: '비상예비금지표',    value: 11.9 },
        { key: '보장성보험준비지표', value: 7.1  },
      ],
    },
  },

  // 재무목표 슬라이드
  'financial-goal-result': {
    inflation_rate_applied: 0.02,
    pre_retirement: [
      {
        phase_name: '30대', start_age: 30, end_age: 39,
        desired_expenses: [
          {
            from_age: 30, to_age: 39,
            items: [
              { name: '생활비', amount: 2000000, is_fixed: true  },
              { name: '저축',   amount: 500000,  is_fixed: true  },
              { name: '보험료', amount: 200000,  is_fixed: true  },
            ],
            current_total: 2700000, future_total: 2700000,
          },
        ],
        expected_expenses: [
          { age: 35, content: '결혼 자금', note: null, current_amount: 30000000, future_amount: 33200000 },
        ],
        achieved_funds: [],
      },
      {
        phase_name: '40대', start_age: 40, end_age: 49,
        desired_expenses: [
          {
            from_age: 40, to_age: 49,
            items: [
              { name: '생활비', amount: 2500000, is_fixed: true  },
              { name: '교육비', amount: 500000,  is_fixed: false },
              { name: '저축',   amount: 700000,  is_fixed: true  },
            ],
            current_total: 3700000, future_total: 4508000,
          },
        ],
        expected_expenses: [
          { age: 45, content: '자녀 대학', note: null, current_amount: 20000000, future_amount: 29500000 },
        ],
        achieved_funds: [],
      },
      {
        phase_name: '50대', start_age: 50, end_age: 65,
        desired_expenses: [
          {
            from_age: 50, to_age: 65,
            items: [
              { name: '생활비', amount: 3000000, is_fixed: true  },
              { name: '저축',   amount: 1000000, is_fixed: true  },
              { name: '보험료', amount: 300000,  is_fixed: true  },
              { name: '노후대비', amount: 500000, is_fixed: true  },
            ],
            current_total: 4800000, future_total: 6450000,
          },
        ],
        expected_expenses: [
          { age: 55, content: '자녀 결혼', note: null, current_amount: 50000000, future_amount: 72000000 },
        ],
        achieved_funds: [
          { age: 60, content: '퇴직금 수령', note: null, current_amount: 150000000, future_amount: 200000000 },
        ],
      },
    ],
    post_retirement_phases: [
      {
        phase_name: '활동기', start_age: 66, end_age: 75,
        desired_expenses: [
          {
            from_age: 66, to_age: 75,
            items: [
              { name: '생활비',        amount: 250000,  is_fixed: true  },
              { name: '공과금',        amount: 300000,  is_fixed: true  },
              { name: '대출상환액', amount: 400000,  is_fixed: true  },
              { name: '저축',          amount: 1500000, is_fixed: true  },
              { name: '보험료',        amount: 180000,  is_fixed: true  },
              { name: '자녀',          amount: 200000,  is_fixed: false },
              { name: '기타',          amount: 150000,  is_fixed: false },
            ],
            current_total: 2980000, future_total: 3632000,
          },
        ],
        expected_expenses: [
          { age: 66, content: '여행', note: null, current_amount: 1500000, future_amount: 1826000 },
          { age: 69, content: '여행', note: null, current_amount: 1500000, future_amount: 1938000 },
          { age: 73, content: '여행', note: null, current_amount: 2000000, future_amount: 2811000 },
        ],
        achieved_funds: [],
      },
      {
        phase_name: '회상기', start_age: 76, end_age: 85,
        desired_expenses: [
          {
            from_age: 76, to_age: 85,
            items: [
              { name: '생활비',        amount: 250000,  is_fixed: true  },
              { name: '공과금',        amount: 300000,  is_fixed: true  },
              { name: '대출상환액', amount: 400000,  is_fixed: true  },
              { name: '저축',          amount: 1500000, is_fixed: true  },
              { name: '보험료',        amount: 180000,  is_fixed: true  },
              { name: '자녀',          amount: 200000,  is_fixed: false },
              { name: '기타',          amount: 150000,  is_fixed: false },
            ],
            current_total: 2980000, future_total: 3950000,
          },
        ],
        expected_expenses: [],
        achieved_funds: [],
      },
      {
        phase_name: '간병기', start_age: 86, end_age: 90,
        desired_expenses: [
          {
            from_age: 86, to_age: 90,
            items: [
              { name: '생활비',        amount: 250000,  is_fixed: true  },
              { name: '공과금',        amount: 300000,  is_fixed: true  },
              { name: '대출상환액', amount: 400000,  is_fixed: true  },
              { name: '저축',          amount: 1500000, is_fixed: true  },
              { name: '보험료',        amount: 180000,  is_fixed: true  },
              { name: '자녀',          amount: 200000,  is_fixed: false },
              { name: '기타',          amount: 150000,  is_fixed: false },
            ],
            current_total: 2980000, future_total: 4350000,
          },
        ],
        expected_expenses: [],
        achieved_funds: [],
      },
    ],
    assumptions: {
      inflation_rate: 0.02,
      sources: { inflation_rate: { label: '한국은행 물가안정목표', url: null } },
    },
  },

  // 생애현금흐름 및 생애순자산
  'lifetime-cashflow': {
    assumptions: {
      inflation_rate: 0.02,
      investment_rate: 0.04,
      wage_growth_rate: 0.03,
      real_estate_growth_rate: 0.02,
    },
    depletion_point: {
      is_depleted: false,
      depletion_age: null,
    },
    final_status: {
      end_age: 90,
      final_deficit: 0,
      final_net_worth: 680000000,
    },
    chart_data: [
      { age: 35, cash_inflow: 50400000, cash_outflow: -27840000, net_flow: 22560000, total_net_worth: 428000000 },
      { age: 40, cash_inflow: 58400000, cash_outflow: -30740000, net_flow: 27660000, total_net_worth: 526000000 },
      { age: 45, cash_inflow: 67700000, cash_outflow: -33940000, net_flow: 33760000, total_net_worth: 643000000 },
      { age: 50, cash_inflow: 78500000, cash_outflow: -37470000, net_flow: 41030000, total_net_worth: 779000000 },
      { age: 55, cash_inflow: 91000000, cash_outflow: -41370000, net_flow: 49630000, total_net_worth: 936000000 },
      { age: 60, cash_inflow: 105500000, cash_outflow: -45680000, net_flow: 59820000, total_net_worth: 1115000000 },
      { age: 65, cash_inflow: 33600000, cash_outflow: -59850000, net_flow: -26250000, total_net_worth: 1138000000 },
      { age: 70, cash_inflow: 36000000, cash_outflow: -66100000, net_flow: -30100000, total_net_worth: 1119000000 },
      { age: 75, cash_inflow: 38400000, cash_outflow: -73000000, net_flow: -34600000, total_net_worth: 1078000000 },
      { age: 80, cash_inflow: 40800000, cash_outflow: -80600000, net_flow: -39800000, total_net_worth: 999000000 },
      { age: 85, cash_inflow: 43200000, cash_outflow: -89000000, net_flow: -45800000, total_net_worth: 864000000 },
      { age: 90, cash_inflow: 45600000, cash_outflow: -98400000, net_flow: -52800000, total_net_worth: 680000000 },
    ],
  },
}
