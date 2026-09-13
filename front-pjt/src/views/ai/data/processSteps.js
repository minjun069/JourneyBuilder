export const PROCESS_STEPS = [
  { id: 'intro',                  label: '서비스 안내',                depth: 0, phase: 'intro' },
  { id: 'collection',             label: '고객 정보 수집',              depth: 0, phase: 'collection' },
  { id: 'basic-info',             label: '기본 정보',                  depth: 1, phase: 'collection' },
  { id: 'financial-mindset',      label: '경제 관념',                  depth: 1, phase: 'collection' },
  { id: 'financial-satisfaction', label: '재무 만족도',                 depth: 1, phase: 'collection' },
  { id: 'financial-info',         label: '재무 정보',                  depth: 1, phase: 'collection' },
  { id: 'financial-assets',       label: '금융자산',                   depth: 2, phase: 'collection' },
  { id: 'pension-assets',         label: '연금자산',                   depth: 2, phase: 'collection' },
  { id: 'real-estate',            label: '부동산자산',                  depth: 2, phase: 'collection' },
  { id: 'liabilities',            label: '부채',                      depth: 2, phase: 'collection' },
  { id: 'income',                 label: '소득',                      depth: 2, phase: 'collection' },
  { id: 'expenses',               label: '지출',                      depth: 2, phase: 'collection' },
  { id: 'financial-goals',        label: '재무목표',                   depth: 1, phase: 'collection' },
  { id: 'presentation',           label: '재무 진단',                  depth: 0, phase: 'presentation' },
  { id: 'balance-sheet',          label: '재무상태표',                  depth: 1, phase: 'presentation' },
  { id: 'cash-flow',              label: '현금흐름표',                  depth: 1, phase: 'presentation' },
  { id: 'financial-indicators',   label: '가계재무지표',                 depth: 1, phase: 'presentation' },
  { id: 'financial-goal-result',  label: '재무목표',                   depth: 1, phase: 'presentation' },
  { id: 'retirement-fund',        label: '은퇴 필요자금',               depth: 1, phase: 'presentation' },
  { id: 'retirement-readiness',   label: '은퇴 준비자금',               depth: 1, phase: 'presentation' },
  { id: 'retirement-simulation',  label: '은퇴 시뮬레이션',             depth: 1, phase: 'presentation' },
  { id: 'pension-schedule',       label: '연금 스케줄',                 depth: 1, phase: 'presentation' },
  { id: 'lifetime-cashflow',      label: '생애현금흐름 및\n생애순자산',   depth: 1, phase: 'presentation' },
  { id: 'required-savings',       label: '필요저축액',                  depth: 1, phase: 'presentation' },
]

export const STEP_IDS = PROCESS_STEPS.map((s) => s.id)

export const PRESENTATION_STEP_IDS = PROCESS_STEPS
  .filter((s) => s.phase === 'presentation' && s.depth === 1)
  .map((s) => s.id)
