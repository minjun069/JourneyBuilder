import { expect } from '@playwright/test'
import pg from 'pg'
import { createClient as createRedisClient } from 'redis'

import { changeKey } from '../p07/scenarios.mjs'

const { Client: PgClient } = pg
const STEP_LABELS = {
  'basic-info': '기본 정보',
  'financial-mindset': '경제 관념',
  'financial-assets': '금융자산',
}

function authHeaders(token) {
  return { Authorization: `Bearer ${token}` }
}

function subset(actual, fields) {
  return Object.fromEntries(Object.keys(fields).map((key) => [key, actual[key]]))
}

async function redisKeys(client, pattern) {
  const keys = []
  for await (const batch of client.scanIterator({ MATCH: pattern })) {
    keys.push(...(Array.isArray(batch) ? batch : [batch]))
  }
  return keys
}

export class F05BrowserDriver {
  constructor({ browser, request, baseURL, fixtureURL, controlToken }) {
    this.browser = browser
    this.request = request
    this.baseURL = baseURL.replace(/\/$/, '')
    this.fixtureURL = fixtureURL.replace(/\/$/, '')
    this.controlToken = controlToken
    this.observedStates = new Set()
  }

  async api(path, { method = 'GET', token = this.accessToken, data, expected = 200 } = {}) {
    const response = await this.request.fetch(`${this.baseURL}${path}`, {
      method,
      data,
      headers: token ? authHeaders(token) : {},
    })
    const body = await response.json().catch(() => null)
    expect(response.status(), `${method} ${path}: ${JSON.stringify(body)}`).toBe(expected)
    return body
  }

  async createIdentity(email) {
    const password = 'P07-e2e-password-42!'
    await this.api('/api/v1/auth/register/', {
      method: 'POST',
      token: null,
      data: { email, password, name: 'P07 E2E' },
      expected: 201,
    })
    const login = await this.api('/api/v1/auth/login/', {
      method: 'POST',
      token: null,
      data: { email, password },
    })
    const created = await this.api('/api/v1/consultations/', {
      method: 'POST',
      token: login.access,
      data: {},
      expected: 201,
    })
    return {
      access: login.access,
      refresh: login.refresh,
      sessionId: created.data.id,
    }
  }

  async provision({ scenario, isolation }) {
    this.scenario = scenario
    this.isolation = isolation
    this.db = new PgClient({
      host: process.env.POSTGRES_HOST,
      port: Number(process.env.POSTGRES_PORT ?? 5432),
      database: process.env.POSTGRES_DB,
      user: process.env.POSTGRES_USER,
      password: process.env.POSTGRES_PASSWORD,
      options: `-c search_path=${isolation.database.schema}`,
    })
    await this.db.connect()

    const identity = await this.createIdentity(isolation.identity.userEmail)
    this.accessToken = identity.access
    this.refreshToken = identity.refresh
    this.sessionId = identity.sessionId
    this.foreignIdentity = await this.createIdentity(
      isolation.identity.userEmail.replace('@', '.foreign@'),
    )

    if (scenario.stepId === 'basic-info') {
      await this.api(`/api/v1/consultations/${this.sessionId}/data/basic-info/`, {
        method: 'PUT',
        data: scenario.initial.database,
      })
    } else if (scenario.stepId === 'financial-mindset') {
      await this.api(`/api/v1/consultations/${this.sessionId}/data/financial-mindset/`, {
        method: 'PUT',
        data: scenario.initial.database,
      })
    } else {
      const asset = scenario.initial.database[0]
      await this.db.query(
        `INSERT INTO consulting_financial_assets
          (id, session_id, asset_type, name, principal, current_value, monthly_savings, purpose, created_at)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())`,
        [
          asset.id,
          this.sessionId,
          asset.asset_type,
          asset.name,
          asset.principal,
          asset.current_value,
          asset.monthly_savings,
          asset.purpose,
        ],
      )
      await this.db.query(
        `SELECT setval(
          pg_get_serial_sequence('consulting_financial_assets', 'id'),
          (SELECT MAX(id) FROM consulting_financial_assets),
          true
        )`,
      )
    }

    this.context = await this.browser.newContext()
    await this.context.addInitScript(({ access, refresh, prefix }) => {
      localStorage.setItem('access_token', access)
      localStorage.setItem('refresh_token', refresh)
      localStorage.setItem(`${prefix}browser-context`, 'active')
    }, {
      access: this.accessToken,
      refresh: this.refreshToken,
      prefix: isolation.browser.storagePrefix,
    })
    this.page = await this.context.newPage()
  }

  async readDatabase() {
    if (this.scenario.stepId === 'basic-info') {
      const result = await this.db.query(
        'SELECT monthly_income, job FROM consulting_basic_info WHERE session_id = $1',
        [this.sessionId],
      )
      return result.rows[0] ?? null
    }
    if (this.scenario.stepId === 'financial-mindset') {
      const result = await this.db.query(
        'SELECT current_challenge, future_goals FROM consulting_financial_mindset WHERE session_id = $1',
        [this.sessionId],
      )
      return result.rows[0] ?? null
    }
    const result = await this.db.query(
      `SELECT id, asset_type, name, principal, current_value, monthly_savings, purpose
       FROM consulting_financial_assets WHERE session_id = $1 ORDER BY id`,
      [this.sessionId],
    )
    return result.rows.map((row) => Object.fromEntries(
      Object.entries(row).map(([key, value]) => [key, typeof value === 'string' && /^-?\d+$/.test(value) ? Number(value) : value]),
    ))
  }

  assertAssetRows(actual, expected, phase) {
    expect(actual, `${phase}: 금융자산 행 수`).toHaveLength(expected.length)
    const used = new Set()
    for (const item of expected) {
      if (!item.match) {
        const row = actual.find((candidate) => candidate.id === item.id)
        expect(row, `${phase}: id=${item.id}`).toBeTruthy()
        expect(subset(row, item)).toEqual(item)
        used.add(row.id)
        continue
      }

      let row
      if (item.match.id != null || item.match.originalId != null) {
        row = actual.find((candidate) => candidate.id === (item.match.id ?? item.match.originalId))
      } else if (item.match.generatedDatabaseId) {
        row = actual.find((candidate) => !used.has(candidate.id) && candidate.id !== 41 && (
          item.fields.name == null || candidate.name === item.fields.name
        ))
      }
      expect(row, `${phase}: ${JSON.stringify(item.match)}`).toBeTruthy()
      expect(subset(row, item.fields)).toEqual(item.fields)
      used.add(row.id)
    }
  }

  async assertDatabase({ expected, phase }) {
    const actual = await this.readDatabase()
    if (Array.isArray(expected)) {
      this.assertAssetRows(actual, expected, phase)
    } else {
      expect(actual, `${phase}: DB 값`).toEqual(expected)
    }
  }

  async jumpTo(label) {
    await this.page.getByRole('button', { name: label, exact: true }).click()
    const confirm = this.page.getByRole('button', { name: '기본값으로 처리하고 이동' })
    if (await confirm.isVisible().catch(() => false)) await confirm.click()
  }

  async openStep({ stepId }) {
    await this.page.goto('/ai')
    await this.page.getByRole('button', { name: '기존 상담 이어하기' }).click()
    await expect(this.page.locator('.composer')).toBeVisible()
    await this.jumpTo('고객 정보 수집')
    if (stepId === 'financial-assets') await this.jumpTo('재무 정보')
    await this.jumpTo(STEP_LABELS[stepId])
    await expect(this.page.locator('.input-panel .doc-title')).toHaveText(STEP_LABELS[stepId])
  }

  fieldRow(label) {
    return this.page.locator('.input-panel .field-row').filter({
      has: this.page.locator('.field-label', { hasText: label }),
    })
  }

  async fillAssetRow(row, asset) {
    const selects = row.locator('select')
    const inputs = row.locator('input')
    await selects.nth(0).selectOption({ label: asset.assetType })
    await inputs.nth(0).fill(String(asset.name))
    await inputs.nth(1).fill(String(asset.principal))
    await inputs.nth(2).fill(String(asset.value))
    await inputs.nth(3).fill(String(asset.monthly))
    await selects.nth(1).selectOption({ label: asset.purpose })
  }

  async seedDraft({ draft }) {
    if (this.scenario.stepId === 'basic-info') {
      await this.fieldRow('소득').locator('input').fill(String(draft.earnings))
      await this.fieldRow('직업').locator('select').selectOption({ label: draft.job })
      return
    }
    if (this.scenario.stepId === 'financial-mindset') {
      await this.fieldRow('당면과제').locator('textarea').fill(draft.currentChallenge)
      await this.fieldRow('미래인생목표').locator('textarea').fill(draft.futureGoals)
      return
    }

    for (const asset of draft.assets) {
      await this.page.getByRole('button', { name: '+ 행 추가' }).click()
      const row = this.page.locator('.input-panel tbody tr').last()
      await this.fillAssetRow(row, asset)
    }
    await this.page.locator('.input-panel .table-wrap').evaluate((element, identities) => {
      let instance = element.__vueParentComponent
      while (instance && !instance.exposed?.form) instance = instance.parent
      if (!instance?.exposed?.form?.assets) throw new Error('금융자산 draft에 접근할 수 없습니다.')
      identities.forEach((identity, index) => Object.assign(instance.exposed.form.assets[index], identity))
    }, draft.assets.map(({ id, clientItemId }) => ({
      ...(id == null ? {} : { id }),
      ...(clientItemId == null ? {} : { clientItemId }),
    })))
  }

  async dispatchFixture({ prompt, output }) {
    const prepared = await this.request.post(`${this.fixtureURL}/__e2e/fixture`, {
      headers: authHeaders(this.controlToken),
      data: { output },
    })
    expect(prepared.status()).toBe(202)

    const composer = this.page.locator('textarea.chat-input')
    await expect(composer).toBeEnabled({ timeout: 60_000 })
    await composer.fill(prompt)
    await this.page.getByRole('button', { name: '전송' }).click()
    await expect(composer).toBeDisabled()
    this.observedStates.add('processing')
  }

  async waitForProposal({ expectedChangeCount }) {
    const card = this.page.locator('.update-card').last()
    try {
      await expect(card).toBeVisible()
    } catch (error) {
      const bubbles = await this.page.locator('.bubble p').allTextContents()
      const sessionError = await this.page.locator('.session-error').allTextContents()
      throw new Error(
        `AI 제안 카드가 표시되지 않았습니다. bubbles=${JSON.stringify(bubbles)}, sessionError=${JSON.stringify(sessionError)}`,
        { cause: error },
      )
    }
    await expect(card.locator('.update-change')).toHaveCount(expectedChangeCount)
    this.observedStates.add('completed-proposal')

    const messages = await this.api(`/api/v1/consultations/${this.sessionId}/messages/`)
    const message = [...messages.data].reverse().find((item) => item.extracted_data?.action)
    expect(message).toBeTruthy()
    return { messageId: message.id, action: message.extracted_data.action, card }
  }

  async decideChange({ proposal, decision }) {
    const index = proposal.action.changes.findIndex(
      (change) => changeKey(change) === changeKey(decision.match),
    )
    expect(index, `change를 찾을 수 없음: ${changeKey(decision.match)}`).toBeGreaterThanOrEqual(0)
    const change = proposal.action.changes[index]
    const endpoint = `/api/v2/consultations/${this.sessionId}/ai-actions/${proposal.action.action_id}/changes/${change.change_id}/${decision.outcome}/`
    const responsePromise = this.page.waitForResponse((response) => (
      response.url().includes(endpoint) && response.request().method() === 'POST'
    ))
    const buttonName = decision.outcome === 'approve' ? '승인' : '거절'
    await proposal.card.locator('.update-change').nth(index).getByRole('button', { name: buttonName }).click()
    const response = await responsePromise
    const body = await response.json()
    expect(response.status(), JSON.stringify(body)).toBe(200)
    await expect(proposal.card.locator('.update-change').nth(index).locator('.update-decision')).toBeVisible()
    return body.data
  }

  async assertActionStatus({ result, expected }) {
    expect(result.action_status).toBe(expected)
    this.observedStates.add(expected.replaceAll('_', '-'))
  }

  async assetDraftRows() {
    return this.page.locator('.input-panel .table-wrap').evaluate((element) => {
      let instance = element.__vueParentComponent
      while (instance && !instance.exposed?.form) instance = instance.parent
      return structuredClone(instance.exposed.form.assets)
    })
  }

  async assertDraft({ expected, phase }) {
    if (this.scenario.stepId === 'basic-info') {
      await expect(this.fieldRow('소득').locator('input')).toHaveValue(String(expected.earnings))
      await expect(this.fieldRow('직업').locator('select')).toHaveValue(expected.job)
      return
    }
    if (this.scenario.stepId === 'financial-mindset') {
      await expect(this.fieldRow('당면과제').locator('textarea')).toHaveValue(expected.currentChallenge)
      await expect(this.fieldRow('미래인생목표').locator('textarea')).toHaveValue(expected.futureGoals)
      return
    }

    const actual = await this.assetDraftRows()
    expect(actual, `${phase}: 금융자산 draft 행 수`).toHaveLength(expected.assets.length)
    for (const item of expected.assets) {
      let row
      if (item.match.id != null) row = actual.find((candidate) => candidate.id === item.match.id)
      else if (item.match.clientItemId) row = actual.find((candidate) => candidate.clientItemId === item.match.clientItemId)
      else row = actual.find((candidate) => candidate.clientItemId && !['asset-client-02'].includes(candidate.clientItemId) && candidate.name === item.fields.name)
      expect(row, `${phase}: ${JSON.stringify(item.match)}`).toBeTruthy()
      expect(subset(row, item.fields)).toEqual(item.fields)
    }
  }

  async assetRowByName(name) {
    const rows = this.page.locator('.input-panel tbody tr')
    for (let index = 0; index < await rows.count(); index += 1) {
      const row = rows.nth(index)
      if (await row.locator('input').nth(0).inputValue() === name) return row
    }
    throw new Error(`금융자산 행을 찾을 수 없습니다: ${name}`)
  }

  async assertSaveRejectedAtomically({ probe, unchangedDatabase }) {
    const row = await this.assetRowByName(probe.invalidDraftPatch.row)
    const valueInput = row.locator('input').nth(2)
    const validValue = await valueInput.inputValue()
    await valueInput.fill(String(probe.invalidDraftPatch.value))
    const responsePromise = this.page.waitForResponse((response) => (
      response.url().includes(`/api/v1/consultations/${this.sessionId}/data/financial-assets/`)
      && response.request().method() === 'PUT'
    ))
    await this.page.locator('.submit-form-btn').click()
    const response = await responsePromise
    expect(response.status()).toBe(probe.expectedHttpStatus)
    await this.assertDatabase({ expected: unchangedDatabase, phase: 'rejected-atomic-save' })
    await valueInput.fill(validValue)
    await expect(this.page.locator('.submit-form-btn')).toBeEnabled()
  }

  async saveForm({ stepId }) {
    const fragment = {
      'basic-info': 'basic-info',
      'financial-mindset': 'financial-mindset',
      'financial-assets': 'financial-assets',
    }[stepId]
    const responsePromise = this.page.waitForResponse((response) => (
      response.url().includes(`/api/v1/consultations/${this.sessionId}/data/${fragment}/`)
      && response.request().method() === 'PUT'
      && response.status() === 200
    ))
    await this.page.locator('.submit-form-btn').click()
    await responsePromise
  }

  async assertSavedScreen({ expectedStates }) {
    await expect(this.page.getByText('입력하신 정보가 저장되었습니다.', { exact: false })).toBeVisible({ timeout: 30_000 })
    this.observedStates.add('saved')
    for (const state of expectedStates) {
      expect(this.observedStates, `화면 상태 누락: ${state}`).toContain(state)
    }
  }

  async assertIsolation({ databaseSchema, redisNamespace, foreignUserMustBeDenied, foreignSessionMustBeUnchanged }) {
    const schema = await this.db.query('SELECT current_schema() AS name')
    expect(schema.rows[0].name).toBe(databaseSchema)

    if (foreignUserMustBeDenied) {
      await this.api(`/api/v1/consultations/${this.sessionId}/`, {
        token: this.foreignIdentity.access,
        expected: 404,
      })
    }
    if (foreignSessionMustBeUnchanged) {
      const counts = await this.db.query(
        `SELECT
          (SELECT COUNT(*) FROM consulting_basic_info WHERE session_id = $1)::int AS basic,
          (SELECT COUNT(*) FROM consulting_financial_mindset WHERE session_id = $1)::int AS mindset,
          (SELECT COUNT(*) FROM consulting_financial_assets WHERE session_id = $1)::int AS assets`,
        [this.foreignIdentity.sessionId],
      )
      expect(counts.rows[0]).toEqual({ basic: 0, mindset: 0, assets: 0 })
    }

    this.redis = createRedisClient({ url: process.env.REDIS_URL })
    await this.redis.connect()
    const keys = await redisKeys(this.redis, `${redisNamespace}*`)
    expect(keys.length, '현재 실행의 Redis lifecycle key가 필요합니다.').toBeGreaterThan(0)
    expect(keys.every((key) => key.startsWith(redisNamespace))).toBe(true)
  }

  async cleanup({ isolation }) {
    await this.context?.close().catch(() => undefined)
    if (this.redis?.isOpen) {
      const keys = await redisKeys(this.redis, `${isolation.redis.namespace}*`)
      if (keys.length) await this.redis.sendCommand(['DEL', ...keys])
      await this.redis.quit()
    }
    if (this.db) {
      const schema = isolation.database.schema
      if (!/^p07_[a-z0-9]{8,24}$/.test(schema)) throw new Error('안전하지 않은 cleanup schema입니다.')
      await this.db.query(`DROP SCHEMA "${schema}" CASCADE`)
      await this.db.end()
    }
  }
}
