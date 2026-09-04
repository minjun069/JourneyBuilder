import { prepareScenarioOutput } from './fixture-adapter.mjs'
import { validateIsolation } from './isolation.mjs'
import { validateScenario } from './scenarios.mjs'

const REQUIRED_DRIVER_METHODS = [
  'provision',
  'assertDatabase',
  'openStep',
  'seedDraft',
  'dispatchFixture',
  'waitForProposal',
  'decideChange',
  'assertActionStatus',
  'assertDraft',
  'saveForm',
  'assertSavedScreen',
  'assertIsolation',
  'cleanup',
]

function validateDriver(driver, scenario) {
  const required = scenario.expected.transactionProbe
    ? [...REQUIRED_DRIVER_METHODS, 'assertSaveRejectedAtomically']
    : REQUIRED_DRIVER_METHODS
  const missing = required.filter((name) => typeof driver[name] !== 'function')
  if (missing.length) throw new Error(`E2E driver method가 없습니다: ${missing.join(', ')}`)
}

export async function runP07Scenario({ driver, repoRoot, scenario, isolation }) {
  validateScenario(scenario)
  validateIsolation(isolation)
  validateDriver(driver, scenario)

  const prepared = await prepareScenarioOutput(repoRoot, scenario)
  try {
    await driver.provision({ scenario, isolation })
    await driver.assertDatabase({ expected: scenario.initial.database, phase: 'initial', isolation })
    await driver.openStep({ stepId: scenario.stepId, isolation })
    await driver.seedDraft({ draft: scenario.initial.draft, isolation })
    await driver.dispatchFixture({
      prompt: scenario.prompt,
      output: prepared.output,
      fixtureSource: prepared.source,
      isolation,
    })
    const proposal = await driver.waitForProposal({
      stepId: scenario.stepId,
      expectedChangeCount: scenario.decisions.length,
      isolation,
    })

    for (const [index, decision] of scenario.decisions.entries()) {
      const result = await driver.decideChange({ proposal, decision, isolation })
      await driver.assertActionStatus({
        result,
        expected: scenario.expected.actionStatuses[index],
        isolation,
      })
    }

    await driver.assertDraft({
      expected: scenario.expected.draftAfterDecisions,
      phase: 'after-decisions',
      isolation,
    })
    await driver.assertDatabase({
      expected: scenario.expected.databaseBeforeSave,
      phase: 'before-form-save',
      isolation,
    })

    if (scenario.expected.transactionProbe) {
      await driver.assertSaveRejectedAtomically({
        probe: scenario.expected.transactionProbe,
        unchangedDatabase: scenario.expected.databaseBeforeSave,
        isolation,
      })
    }

    await driver.saveForm({ stepId: scenario.stepId, isolation })
    await driver.assertDatabase({
      expected: scenario.expected.databaseAfterSave,
      phase: 'after-form-save',
      isolation,
    })
    await driver.assertSavedScreen({
      expectedStates: scenario.expected.screenStates,
      isolation,
    })
    await driver.assertIsolation({
      databaseSchema: isolation.database.schema,
      redisNamespace: isolation.redis.namespace,
      foreignUserMustBeDenied: true,
      foreignSessionMustBeUnchanged: true,
      isolation,
    })

    return { scenarioId: scenario.id, fixtureSource: prepared.source }
  } finally {
    await driver.cleanup({ plan: isolation.cleanup, isolation })
  }
}
