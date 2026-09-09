import path from 'node:path'

import { expect, test } from '@playwright/test'

import { runP07Scenario } from '../p07/e2e-flow.mjs'
import { createIsolation } from '../p07/isolation.mjs'
import { P07_SCENARIOS } from '../p07/scenarios.mjs'
import { F05BrowserDriver } from './browser-driver.mjs'

const scenarioId = process.env.F05_SCENARIO_ID
const scenario = P07_SCENARIOS.find((candidate) => candidate.id === scenarioId)
const repoRoot = path.resolve(process.cwd(), '..')

test('F05 representative flow: proposal, decisions, form save, database', async ({ browser, request, baseURL }) => {
  expect(scenario, `알 수 없는 F05_SCENARIO_ID: ${scenarioId}`).toBeTruthy()
  const isolation = createIsolation(process.env.F05_DB_SCHEMA.replace(/^p07_/, ''))
  expect(isolation.database.schema).toBe(process.env.F05_DB_SCHEMA)
  expect(isolation.redis.namespace).toBe(process.env.F05_REDIS_NAMESPACE)

  const driver = new F05BrowserDriver({
    browser,
    request,
    baseURL,
    fixtureURL: process.env.F05_FIXTURE_URL,
    controlToken: process.env.F05_CONTROL_TOKEN,
  })
  const result = await runP07Scenario({ driver, repoRoot, scenario, isolation })
  expect(result).toEqual({ scenarioId, fixtureSource: 'common-fixture' })
})
