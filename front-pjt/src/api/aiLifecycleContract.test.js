import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  AI_CONTRACT_VERSION,
} from './aiLifecycle.js'


const fixtureUrl = new URL(
  '../../../contract-fixtures/ai-lifecycle/v2/lifecycle.json',
  import.meta.url,
)
const fixture = JSON.parse(readFileSync(fixtureUrl, 'utf8'))


test('공용 lifecycle fixture는 모든 필수 범주와 v2 version을 유지한다', () => {
  const categories = new Set(Object.values(fixture.cases).map(({ category }) => category))
  for (const required of fixture.required_case_categories) {
    assert.equal(categories.has(required), true, required)
  }
  assert.equal(AI_CONTRACT_VERSION, fixture.contract_version)
})
