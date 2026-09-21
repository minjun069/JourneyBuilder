import { readFile } from 'node:fs/promises'
import path from 'node:path'

import { changeKey, validateScenario } from './scenarios.mjs'

export const COMMON_FIXTURE_PATHS = Object.freeze({
  lifecycle: 'contract-fixtures/ai-lifecycle/v2/lifecycle.json',
  formActions: 'contract-fixtures/ai-lifecycle/v2/form-actions.json',
})

const clone = (value) => structuredClone(value)

function valueAtPath(value, dottedPath) {
  return dottedPath.split('.').reduce((current, key) => current?.[key], value)
}

function fixtureCases(document) {
  return document?.cases ?? document?.scenarios ?? document
}

export async function loadFixtureCase(repoRoot, fixtureName, caseName) {
  const relativePath = COMMON_FIXTURE_PATHS[fixtureName]
  if (!relativePath) throw new Error(`지원하지 않는 공용 fixture입니다: ${fixtureName}`)

  let document
  try {
    document = JSON.parse(await readFile(path.join(repoRoot, relativePath), 'utf8'))
  } catch (error) {
    if (error.code === 'ENOENT') {
      const unavailable = new Error(`공용 fixture가 아직 없습니다: ${relativePath}`)
      unavailable.code = 'P07_FIXTURE_UNAVAILABLE'
      throw unavailable
    }
    throw error
  }

  const fixtureCase = fixtureCases(document)?.[caseName]
  if (!fixtureCase) throw new Error(`${relativePath}에 ${caseName} case가 없습니다.`)
  return clone(fixtureCase)
}

function asAiChange(change) {
  const result = clone(change)
  delete result.change_id
  delete result.decision_status
  return result
}

function asProposalAction(sourceAction, scenario) {
  if (!sourceAction?.changes?.length) {
    throw new Error(`${scenario.source.caseName}의 공용 fixture action에 change가 없습니다.`)
  }
  const sourceChanges = sourceAction.changes.map(asAiChange)
  const existingKeys = new Set(sourceChanges.map(changeKey))
  const extraChanges = (scenario.extraChanges ?? [])
    .filter((change) => !existingKeys.has(changeKey(change)))
    .map(asAiChange)
  const changes = [...sourceChanges, ...extraChanges]
  const changeKeys = new Set(changes.map(changeKey))
  const decisionKeys = new Set(scenario.decisions.map(({ match }) => changeKey(match)))
  if (
    changeKeys.size !== decisionKeys.size
    || [...changeKeys].some((key) => !decisionKeys.has(key))
  ) {
    throw new Error(`${scenario.id}: 공용 fixture의 모든 change는 정확히 한 번 결정되어야 합니다.`)
  }

  const action = {
    type: 'form_change',
    target: 'draft_form',
    step_id: scenario.stepId,
    user_requested: false,
    changes,
  }
  return action
}

export function createProposalOutput(scenario, fixtureCase) {
  validateScenario(scenario)
  if (!fixtureCase) {
    throw new Error(`${scenario.id}: 공용 fixture case가 필요합니다.`)
  }
  const sourceAction = valueAtPath(fixtureCase, scenario.source.actionPath)
  if (!sourceAction) {
    throw new Error(`${scenario.source.caseName}에서 ${scenario.source.actionPath}를 찾을 수 없습니다.`)
  }

  return {
    response: `${scenario.stepId} 변경안을 항목별로 확인해 주세요.`,
    step_id: scenario.stepId,
    action: asProposalAction(sourceAction, scenario),
  }
}

export async function prepareScenarioOutput(repoRoot, scenario) {
  const fixtureCase = await loadFixtureCase(
    repoRoot,
    scenario.source.fixtureName,
    scenario.source.caseName,
  )
  return {
    source: 'common-fixture',
    output: createProposalOutput(scenario, fixtureCase),
  }
}
