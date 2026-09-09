import path from 'node:path'

import { expect, test } from '@playwright/test'

import { runP07Scenario } from '../p07/e2e-flow.mjs'
import { createIsolation } from '../p07/isolation.mjs'
import { P07_SCENARIOS } from '../p07/scenarios.mjs'
import { F05BrowserDriver } from './browser-driver.mjs'

const scenario = P07_SCENARIOS.find(
  ({ id }) => id === 'basic-info-proposal-mixed-decisions',
)

test('F05 harness reaches the integrated browser/API/DB/Redis path', async ({ browser, request, baseURL }) => {
  const isolation = createIsolation(process.env.F05_DB_SCHEMA.replace(/^p07_/, ''))
  expect(isolation.redis.namespace).toBe(process.env.F05_REDIS_NAMESPACE)
  const driver = new F05BrowserDriver({
    browser,
    request,
    baseURL,
    fixtureURL: process.env.F05_FIXTURE_URL,
    controlToken: process.env.F05_CONTROL_TOKEN,
  })

  const result = await runP07Scenario({
    driver,
    repoRoot: path.resolve(process.cwd(), '..'),
    scenario,
    isolation,
  })
  expect(result.fixtureSource).toBe('common-fixture')
})
