import assert from 'node:assert/strict'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  createProposalOutput,
  prepareScenarioOutput,
} from './fixture-adapter.mjs'
import { runP07Scenario } from './e2e-flow.mjs'
import { createIsolation } from './isolation.mjs'
import { P07_SCENARIOS, validateScenario } from './scenarios.mjs'

test('세 대표 단계는 각각 proposal 승인과 거절, draft와 DB 저장 경계를 정의한다', () => {
  assert.deepEqual(
    P07_SCENARIOS.map(({ stepId }) => stepId),
    ['basic-info', 'financial-mindset', 'financial-assets'],
  )
  for (const scenario of P07_SCENARIOS) {
    assert.equal(validateScenario(scenario), scenario)
    assert.ok(scenario.decisions.some(({ outcome }) => outcome === 'approve'))
    assert.ok(scenario.decisions.some(({ outcome }) => outcome === 'reject'))
    assert.notDeepEqual(scenario.expected.databaseBeforeSave, scenario.expected.databaseAfterSave)
    assert.equal(scenario.expected.screenStates.at(-1), 'saved')
  }
})

test('금융자산 시나리오는 stable ID와 add/update/remove, transaction rollback을 포함한다', () => {
  const scenario = P07_SCENARIOS.find(({ stepId }) => stepId === 'financial-assets')
  assert.deepEqual(
    new Set(scenario.fallbackChanges.map(({ operation }) => operation)),
    new Set(['add', 'update', 'remove']),
  )
  assert.equal(scenario.fallbackChanges.find(({ operation }) => operation === 'update').item_id, 41)
  assert.equal(
    scenario.fallbackChanges.find(({ operation }) => operation === 'remove').client_item_id,
    'asset-client-02',
  )
  assert.equal(scenario.expected.transactionProbe.expectedHttpStatus, 400)
})

test('fixture adapter는 원본을 바꾸지 않고 server-owned ID를 제거해 proposal을 만든다', () => {
  const scenario = P07_SCENARIOS[0]
  const fixtureCase = {
    response: {
      body: {
        data: {
          message: {
            extracted_data: {
              action: {
                action_id: 'act-common',
                mode: 'direct',
                status: 'direct',
                user_requested: true,
                confidence: 0.96,
                changes: scenario.fallbackChanges.map((change, index) => ({
                  ...change,
                  change_id: `chg-${index}`,
                  decision_status: 'not_required',
                })),
              },
            },
          },
        },
      },
    },
  }
  const before = structuredClone(fixtureCase)
  const output = createProposalOutput(scenario, fixtureCase)

  assert.deepEqual(fixtureCase, before)
  assert.equal(output.action.user_requested, false)
  assert.ok(output.action.confidence < 0.9)
  assert.equal(output.action.action_id, undefined)
  assert.ok(output.action.changes.every((change) => change.change_id === undefined))
  assert.ok(output.action.changes.every((change) => change.decision_status === undefined))
})

test('G0 전에는 test fallback을 쓰고 공용 fixture가 생기면 같은 adapter가 원본을 읽는다', async () => {
  const scenario = P07_SCENARIOS[1]
  const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), 'p07-fixture-'))
  try {
    const fallback = await prepareScenarioOutput(temporaryRoot, scenario)
    assert.equal(fallback.source, 'test-fallback')

    const fixturePath = path.join(
      temporaryRoot,
      'docs/contracts/fixtures/ai-lifecycle/v2/form-actions.json',
    )
    await mkdir(path.dirname(fixturePath), { recursive: true })
    await writeFile(fixturePath, JSON.stringify({
      cases: {
        financial_mindset_text_proposal: {
          ai_output_action: {
            confidence: 0.84,
            user_requested: false,
            changes: scenario.fallbackChanges,
          },
        },
      },
    }))

    const common = await prepareScenarioOutput(temporaryRoot, scenario)
    assert.equal(common.source, 'common-fixture')
    assert.equal(common.output.action.changes.length, 2)
  } finally {
    await rm(temporaryRoot, { recursive: true, force: true })
  }
})

test('DB schema와 Redis namespace는 실행마다 함께 격리되고 cleanup은 run 범위만 가진다', () => {
  const first = createIsolation('A1234567-first-run')
  const second = createIsolation('B1234567-second-run')
  assert.notEqual(first.database.schema, second.database.schema)
  assert.notEqual(first.redis.namespace, second.redis.namespace)
  assert.equal(first.parallelSafe, true)
  assert.ok(first.cleanup.every(({ target }) => target.includes('a1234567firstrun')))
})

function fakeDriver({ failAt = null } = {}) {
  const calls = []
  const method = (name, result) => async (payload) => {
    calls.push({ name, payload })
    if (failAt === name) throw new Error(`${name} failure`)
    return typeof result === 'function' ? result(payload) : result
  }
  return {
    calls,
    provision: method('provision'),
    assertDatabase: method('assertDatabase'),
    openStep: method('openStep'),
    seedDraft: method('seedDraft'),
    dispatchFixture: method('dispatchFixture'),
    waitForProposal: method('waitForProposal', { actionId: 'runtime-action' }),
    decideChange: method('decideChange', ({ decision }) => ({ decision })),
    assertActionStatus: method('assertActionStatus'),
    assertDraft: method('assertDraft'),
    assertSaveRejectedAtomically: method('assertSaveRejectedAtomically'),
    saveForm: method('saveForm'),
    assertSavedScreen: method('assertSavedScreen'),
    assertIsolation: method('assertIsolation'),
    cleanup: method('cleanup'),
  }
}

test('runner-neutral flow는 proposal 결정 후 DB 불변을 확인하고 폼 저장과 cleanup을 수행한다', async () => {
  const driver = fakeDriver()
  const scenario = P07_SCENARIOS[0]
  const isolation = createIsolation('C1234567-happy-path')
  const result = await runP07Scenario({
    driver,
    repoRoot: '/fixture-not-published-yet',
    scenario,
    isolation,
  })

  assert.equal(result.fixtureSource, 'test-fallback')
  assert.deepEqual(
    driver.calls.map(({ name }) => name),
    [
      'provision',
      'assertDatabase',
      'openStep',
      'seedDraft',
      'dispatchFixture',
      'waitForProposal',
      'decideChange',
      'assertActionStatus',
      'decideChange',
      'assertActionStatus',
      'assertDraft',
      'assertDatabase',
      'saveForm',
      'assertDatabase',
      'assertSavedScreen',
      'assertIsolation',
      'cleanup',
    ],
  )
})

test('금융자산 flow는 정상 저장 전에 transaction rollback probe를 실행한다', async () => {
  const driver = fakeDriver()
  await runP07Scenario({
    driver,
    repoRoot: '/fixture-not-published-yet',
    scenario: P07_SCENARIOS[2],
    isolation: createIsolation('E1234567-asset-transaction'),
  })
  const names = driver.calls.map(({ name }) => name)
  assert.ok(names.indexOf('assertSaveRejectedAtomically') < names.indexOf('saveForm'))
  assert.equal(driver.calls.at(-1).name, 'cleanup')
})

test('중간 실패가 발생해도 격리 cleanup은 실행된다', async () => {
  const driver = fakeDriver({ failAt: 'openStep' })
  await assert.rejects(
    runP07Scenario({
      driver,
      repoRoot: '/fixture-not-published-yet',
      scenario: P07_SCENARIOS[0],
      isolation: createIsolation('D1234567-failure-path'),
    }),
    /openStep failure/,
  )
  assert.equal(driver.calls.at(-1).name, 'cleanup')
})

test('provision 도중 실패해도 현재 run 범위 cleanup을 시도한다', async () => {
  const driver = fakeDriver({ failAt: 'provision' })
  await assert.rejects(
    runP07Scenario({
      driver,
      repoRoot: '/fixture-not-published-yet',
      scenario: P07_SCENARIOS[0],
      isolation: createIsolation('F1234567-provision-failure'),
    }),
    /provision failure/,
  )
  assert.deepEqual(driver.calls.map(({ name }) => name), ['provision', 'cleanup'])
})
