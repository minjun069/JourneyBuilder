import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { STEP_IDS } from '../views/ai/data/processSteps.js'

const registryPath = new URL(
  '../../../contract-fixtures/ai-lifecycle/v2/step-registry.json',
  import.meta.url,
)

async function loadRegistry() {
  return JSON.parse(await readFile(registryPath, 'utf8'))
}

test('Frontend 단계 목록은 공용 step registry와 일치한다', async () => {
  const registry = await loadRegistry()
  const registryStepIds = registry.steps.map((step) => step.step_id)

  assert.equal(registry.fixture_kind, 'ai-step-registry')
  assert.equal(registry.contract_version, '2.0')
  assert.deepEqual(registry.transport_fallback_step_ids, ['general'])
  assert.ok(!registryStepIds.includes('general'))
  assert.equal(registryStepIds.length, 24)
  assert.equal(new Set(registryStepIds).size, registryStepIds.length)
  assert.deepEqual(new Set(STEP_IDS), new Set(registryStepIds))
})

test('모든 단계는 등록된 agent와 fallback만 허용한다', async () => {
  const registry = await loadRegistry()
  const agents = new Set(registry.agents)

  assert.ok(agents.has(registry.fallback_agent))
  for (const step of registry.steps) {
    assert.equal(new Set(step.allowed_agents).size, step.allowed_agents.length)
    assert.ok(step.allowed_agents.includes(registry.fallback_agent))
    assert.ok(step.allowed_agents.every((agent) => agents.has(agent)))
  }
})
