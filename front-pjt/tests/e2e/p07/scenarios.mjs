const freeze = (value) => {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value)
    Object.values(value).forEach(freeze)
  }
  return value
}

const mindsetChallenge = {
  section: 'financial_mindset',
  path: 'current_challenge',
  operation: 'set',
  value: '은퇴 준비를 시작해야 하지만 매달 얼마를 저축할지 정하지 못했다.',
  label: '당면 과제',
  reason: '사용자의 현재 고민을 한 문장으로 정리',
}

const mindsetGoal = {
  section: 'financial_mindset',
  path: 'future_goals',
  operation: 'set',
  value: '65세 이후에도 현재 생활 수준을 유지하고 싶다.',
  label: '미래 인생 목표',
  reason: '명시적으로 확정되지 않은 장기 목표 후보',
}

export const P07_SCENARIOS = freeze([
  {
    id: 'basic-info-proposal-mixed-decisions',
    stepId: 'basic-info',
    source: {
      fixtureName: 'lifecycle',
      caseName: 'terminal_completed_proposal',
      actionPath: 'browser_event.data.message.extracted_data.action',
    },
    prompt: '월급은 세후 300만 원이고 직업은 직장인으로 정리해 주세요.',
    initial: {
      database: { monthly_income: 2_500_000, job: '프리랜서' },
      draft: { earnings: 250, job: '프리랜서' },
    },
    decisions: [
      { match: { section: 'basic_info', path: 'monthly_income', operation: 'set' }, outcome: 'approve' },
      { match: { section: 'basic_info', path: 'job', operation: 'set' }, outcome: 'reject' },
    ],
    expected: {
      actionStatuses: ['partially_resolved', 'resolved'],
      draftAfterDecisions: { earnings: 300, job: '프리랜서' },
      databaseBeforeSave: { monthly_income: 2_500_000, job: '프리랜서' },
      databaseAfterSave: { monthly_income: 3_000_000, job: '프리랜서' },
      units: { earnings: 'frontend:만원', monthly_income: 'database:KRW' },
      screenStates: ['processing', 'completed-proposal', 'partially-resolved', 'resolved', 'saved'],
    },
  },
  {
    id: 'financial-mindset-proposal-mixed-decisions',
    stepId: 'financial-mindset',
    source: {
      fixtureName: 'formActions',
      caseName: 'financial_mindset_text_proposal',
      actionPath: 'ai_output_action',
    },
    prompt: '요즘 고민과 장기 목표를 정리해 주세요.',
    extraChanges: [mindsetGoal],
    initial: {
      database: { current_challenge: null, future_goals: null },
      draft: { currentChallenge: '', futureGoals: '' },
    },
    decisions: [
      { match: { section: 'financial_mindset', path: 'current_challenge', operation: 'set' }, outcome: 'approve' },
      { match: { section: 'financial_mindset', path: 'future_goals', operation: 'set' }, outcome: 'reject' },
    ],
    expected: {
      actionStatuses: ['partially_resolved', 'resolved'],
      draftAfterDecisions: {
        currentChallenge: mindsetChallenge.value,
        futureGoals: '',
      },
      databaseBeforeSave: { current_challenge: null, future_goals: null },
      databaseAfterSave: { current_challenge: mindsetChallenge.value, future_goals: null },
      screenStates: ['processing', 'completed-proposal', 'partially-resolved', 'resolved', 'saved'],
    },
  },
  {
    id: 'financial-assets-proposal-mixed-decisions',
    stepId: 'financial-assets',
    source: {
      fixtureName: 'formActions',
      caseName: 'financial_assets_list_add_update_remove',
      actionPath: 'ai_output_action',
    },
    prompt: '급여 통장 잔액을 고치고, 기존 종목은 지우고, 연금계좌 ETF를 추가해 주세요.',
    initial: {
      database: [
        {
          id: 41,
          asset_type: '예금/적금',
          name: '급여 통장',
          principal: 5_000_000,
          current_value: 5_000_000,
          monthly_savings: 0,
          purpose: '비상자금',
        },
      ],
      draft: {
        assets: [
          {
            id: 41,
            assetType: '예금/적금',
            name: '급여 통장',
            principal: 5_000_000,
            value: 5_000_000,
            monthly: 0,
            purpose: '비상자금',
          },
          {
            clientItemId: 'asset-client-02',
            assetType: '주식',
            name: '정리할 종목',
            principal: 1_000_000,
            value: 800_000,
            monthly: 0,
            purpose: '자산증식',
          },
        ],
      },
    },
    decisions: [
      { match: { section: 'financial_assets', path: 'current_value', operation: 'update' }, outcome: 'approve' },
      { match: { section: 'financial_assets', path: 'name', operation: 'remove' }, outcome: 'reject' },
      { match: { section: 'financial_assets', path: 'name', operation: 'add' }, outcome: 'approve' },
    ],
    expected: {
      actionStatuses: ['partially_resolved', 'partially_resolved', 'resolved'],
      draftAfterDecisions: {
        assets: [
          { match: { id: 41 }, fields: { name: '급여 통장', value: 5_500_000 } },
          { match: { clientItemId: 'asset-client-02' }, fields: { name: '정리할 종목', value: 800_000 } },
          { match: { generatedClientItemId: true }, fields: { name: '연금계좌 ETF', value: 12_000_000 } },
        ],
      },
      databaseBeforeSave: [
        { match: { id: 41 }, fields: { name: '급여 통장', current_value: 5_000_000 } },
      ],
      databaseAfterSave: [
        { match: { originalId: 41 }, fields: { name: '급여 통장', current_value: 5_500_000 } },
        { match: { generatedDatabaseId: true }, fields: { name: '정리할 종목', current_value: 800_000 } },
        { match: { generatedDatabaseId: true }, fields: { name: '연금계좌 ETF', current_value: 12_000_000 } },
      ],
      transactionProbe: {
        invalidDraftPatch: { row: '연금계좌 ETF', field: 'value', value: -1 },
        expectedHttpStatus: 400,
        databaseMustRemain: 'databaseBeforeSave',
      },
      units: { value: 'frontend:KRW', current_value: 'database:KRW' },
      screenStates: ['processing', 'completed-proposal', 'partially-resolved', 'resolved', 'saved'],
    },
  },
])

export const changeKey = ({ section, path, operation }) => `${section}:${path}:${operation}`

export function validateScenario(scenario) {
  if (scenario.source == null) {
    throw new Error(`${scenario.id}: 공용 fixture source가 필요합니다.`)
  }
  const decisionKeys = scenario.decisions.map(({ match }) => changeKey(match))
  if (decisionKeys.length === 0 || new Set(decisionKeys).size !== decisionKeys.length) {
    throw new Error(`${scenario.id}: 서로 다른 change 결정이 필요합니다.`)
  }
  if (!scenario.decisions.some(({ outcome }) => outcome === 'approve')) {
    throw new Error(`${scenario.id}: 승인 결정이 필요합니다.`)
  }
  if (!scenario.decisions.some(({ outcome }) => outcome === 'reject')) {
    throw new Error(`${scenario.id}: 거절 결정이 필요합니다.`)
  }
  if (JSON.stringify(scenario.expected.databaseBeforeSave) === JSON.stringify(scenario.expected.databaseAfterSave)) {
    throw new Error(`${scenario.id}: draft와 DB 저장 경계가 구분되지 않았습니다.`)
  }
  return scenario
}
