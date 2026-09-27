import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'

const { form_cases: formCases } = JSON.parse(readFileSync(new URL(
  '../../../../contract-fixtures/ai-lifecycle/v2/mindset-elicitation.json', import.meta.url,
), 'utf8'))

const fields = ['values', 'past_experience', 'current_situation', 'current_challenge', 'invest_philosophy', 'future_goals']
const session = { id: 41, status: 'in_progress', section_completion: { basic_info: true } }
const savedForm = Object.fromEntries(fields.map((field) => [field, field === 'values' ? '저장된 가치관' : '']))
const result = (overrides = {}) => ({
  field: 'values', summary: '저장된 가치관', answer_count: 0,
  status: 'in_progress', completion_reason: null, next_field: null, ...overrides,
})

test('항목별 자동 질문, 편집 가능한 요약, 잠금·실패 복구와 임시 상태 수명을 지킨다', async ({ page }) => {
  const requests = []
  const writes = []
  const pending = []
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.addInitScript(() => localStorage.setItem('access_token', 'browser-fixture-token'))
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname.endsWith('/messages/stream/')) {
      requests.push(request.postDataJSON())
      const response = await new Promise((resolve) => pending.push(resolve))
      const requestId = `browser-request-${requests.length}`
      const terminal = response.error
        ? { event: 'error', data: { contract_version: '2.0', request_id: requestId, status: 'failed', error: { code: 'UPSTREAM_UNAVAILABLE', message: '잠시 후 다시 말씀해 주세요.', retryable: true } } }
        : { event: 'done', data: { contract_version: '2.0', request_id: requestId, status: 'completed', message: { id: requests.length, role: 'assistant', content: response.text ?? '다음 이야기를 들려주세요.', extracted_data: response.elicitation ? { elicitation: response.elicitation } : {} } } }
      const start = { event: 'start', data: { contract_version: '2.0', request_id: requestId } }
      return route.fulfill({ contentType: 'text/event-stream', body: [start, terminal].map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join('') })
    }
    if (request.method() !== 'GET') writes.push(url.pathname)
    let data = {}
    if (url.pathname.endsWith('/consultations/')) data = [session]
    else if (url.pathname.endsWith('/consultations/41/')) data = session
    else if (url.pathname.endsWith('/messages/')) data = [
      { id: 90, role: 'user', content: '경제관념 항목 상담을 시작합니다.', extracted_data: { system_event: 'mindset_elicitation_start' } },
      { id: 91, role: 'assistant', content: '이전 상담 기록입니다.', extracted_data: { elicitation: result({ summary: '지난 임시 요약', answer_count: 1 }) } },
    ]
    else if (url.pathname.endsWith('/data/financial-mindset/')) data = savedForm
    else if (url.pathname.endsWith('/data/basic-info/')) data = { age: 35, job: '직장인' }
    return route.fulfill({ json: { success: true, data } })
  })
  const reply = async (response) => {
    await expect.poll(() => pending.length).toBe(1)
    pending.shift()(response)
    await expect(page.locator('.composer textarea')).toBeEnabled()
  }
  const say = async (text) => {
    await page.locator('.composer textarea').fill(text)
    await page.getByRole('button', { name: '전송', exact: true }).click()
    await expect.poll(() => pending.length).toBe(1)
  }
  await page.goto('/ai')
  await page.getByRole('button', { name: '기존 상담 이어하기' }).click()
  await expect.poll(() => requests.length).toBe(1)
  expect(requests[0].elicitation_state).toEqual({ event: 'start', field: 'values', answer_count: 0, completed_fields: [] })
  expect(requests[0].form_data.values).toBe('저장된 가치관')
  expect(requests[0].form_data).toEqual(formCases.find(({ id }) => id === 'partial-entry').current_form)
  await expect(page.getByRole('textbox', { name: '가치관', exact: true })).toBeDisabled()
  await expect(page.getByRole('textbox', { name: '미래인생목표', exact: true })).toBeEnabled()
  await expect(page.getByText('경제관념 항목 상담을 시작합니다.', { exact: true })).toHaveCount(0)
  await reply({ elicitation: result(), text: '가치관에 관해 먼저 여쭤보겠습니다.' })
  await expect(page.getByRole('textbox', { name: '가치관', exact: true })).toHaveValue('저장된 가치관')

  await say('생활이 안정적이면 좋겠어요.')
  await expect(page.getByRole('textbox', { name: '가치관', exact: true })).toBeDisabled()
  await page.getByRole('textbox', { name: '미래인생목표', exact: true }).fill('응답 중 직접 쓴 목표')
  await reply({ elicitation: result({ summary: '안정적인 생활을 중시함.', answer_count: 1 }) })
  await expect(page.getByRole('textbox', { name: '가치관', exact: true })).toHaveValue('안정적인 생활을 중시함.')
  await expect(page.getByRole('textbox', { name: '미래인생목표', exact: true })).toHaveValue('응답 중 직접 쓴 목표')
  await page.getByRole('textbox', { name: '가치관', exact: true }).fill('사용자가 정정한 가치관')
  await say('가족의 생활비가 가장 중요해요.')
  expect(requests.at(-1).form_data.values).toBe('사용자가 정정한 가치관')
  expect(requests.at(-1).form_data.future_goals).toBe('응답 중 직접 쓴 목표')
  await reply({ elicitation: result({ summary: '가족의 생활비 확보를 중시함.', answer_count: 2, status: 'completed', completion_reason: 'sufficient', next_field: 'past_experience' }) })
  expect(requests).toHaveLength(3)

  await page.getByRole('textbox', { name: '가치관', exact: true }).fill('완료 후 직접 정정')
  await page.locator('.process-sidebar button').filter({ hasText: '기본 정보' }).click()
  await page.locator('.process-sidebar button').filter({ hasText: '경제 관념' }).click()
  await expect(page.getByRole('textbox', { name: '가치관', exact: true })).toHaveValue('완료 후 직접 정정')
  expect(requests).toHaveLength(3)
  await expect(page.locator('.mindset-progress [aria-current="step"]')).toHaveText(/과거 경험/)

  await say('과거 경험은 어떤 뜻인가요?')
  expect(requests.at(-1).elicitation_state.answer_count).toBe(0)
  await reply({ elicitation: result({ field: 'past_experience', summary: '' }) })
  await say('기억이 잘 나지 않아요.')
  expect(requests.at(-1).elicitation_state.answer_count).toBe(0)
  await reply({ error: true })
  await expect(page.getByRole('textbox', { name: '과거경험', exact: true })).toBeEnabled()
  await say('이 항목은 넘어갈게요.')
  expect(requests.at(-1).elicitation_state.answer_count).toBe(0)
  await reply({ elicitation: result({ field: 'past_experience', summary: '', status: 'completed', completion_reason: 'skipped', next_field: 'current_situation' }) })
  for (let index = 2; index < fields.length; index += 1) {
    await say('넘어갈게요.')
    expect(requests.at(-1).elicitation_state.field).toBe(fields[index])
    await reply({ elicitation: result({ field: fields[index], summary: '', status: 'completed', completion_reason: 'skipped', next_field: fields[index + 1] ?? null }) })
  }
  await say('상담 고마워요.')
  expect(requests.at(-1).request_mode).toBe('chat')
  expect(requests.at(-1).elicitation_state).toBeUndefined()
  await reply({ text: '도움이 되어 다행입니다.' })
  expect(writes).toEqual([])

  await page.reload()
  await page.getByRole('button', { name: '기존 상담 이어하기' }).click()
  await expect.poll(() => pending.length).toBe(1)
  expect(requests.at(-1).elicitation_state).toEqual({ event: 'start', field: 'values', answer_count: 0, completed_fields: [] })
  expect(requests.at(-1).form_data.values).toBe('저장된 가치관')
  await reply({ elicitation: result() })
  expect(pageErrors).toEqual([])
})

test('빈 경제관념 폼의 여섯 항목을 모두 보류한 뒤 기존 작성 완료로 저장한다', async ({ page }) => {
  const deferred = '이 항목에 대한 답변을 보류함.'
  const savedPayloads = []
  const blankForm = Object.fromEntries(fields.map((field) => [field, '']))
  let requestNumber = 0
  await page.addInitScript(() => localStorage.setItem('access_token', 'browser-fixture-token'))
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith('/messages/stream/')) {
      const body = request.postDataJSON()
      const state = body.elicitation_state
      if (state?.event === 'start') {
        expect(body.form_data).toEqual(formCases.find(({ id }) => id === 'empty-entry').current_form)
      }
      const elicitation = state ? result(state.event === 'start' ? { summary: '' } : {
        field: state.field, summary: deferred, answer_count: 0,
        status: 'completed', completion_reason: 'skipped', next_field: fields[fields.indexOf(state.field) + 1] ?? null,
      }) : null
      requestNumber += 1
      const requestId = `all-deferred-${requestNumber}`
      const start = { contract_version: '2.0', request_id: requestId }
      const done = { ...start, status: 'completed', message: {
        id: requestNumber, role: 'assistant', content: '말씀하신 뜻을 반영했습니다.',
        extracted_data: elicitation ? { elicitation } : {},
      } }
      return route.fulfill({ contentType: 'text/event-stream', body: `event: start\ndata: ${JSON.stringify(start)}\n\nevent: done\ndata: ${JSON.stringify(done)}\n\n` })
    }
    let data = {}
    if (path.endsWith('/consultations/')) data = [session]
    else if (path.endsWith('/consultations/41/')) data = session
    else if (path.endsWith('/messages/')) data = []
    else if (path.endsWith('/data/financial-mindset/')) {
      if (request.method() === 'PUT') savedPayloads.push(request.postDataJSON())
      data = savedPayloads.at(-1) ?? blankForm
    }
    return route.fulfill({ json: { success: true, data } })
  })
  await page.goto('/ai')
  await page.getByRole('button', { name: '기존 상담 이어하기' }).click()
  await expect(page.locator('.composer textarea')).toBeEnabled()
  for (const [index] of fields.entries()) {
    await page.locator('.composer textarea').fill('이 항목은 답변을 보류할게요.')
    await page.getByRole('button', { name: '전송', exact: true }).click()
    await expect(page.locator('.doc-fields textarea').nth(index)).toHaveValue(deferred)
    await expect(page.locator('.composer textarea')).toBeEnabled()
  }
  expect(savedPayloads).toEqual([])
  await expect(page.locator('.mindset-progress .complete')).toHaveCount(6)
  await page.getByRole('button', { name: '작성 완료', exact: true }).click()
  await expect.poll(() => savedPayloads.length).toBe(1)
  expect(savedPayloads[0]).toEqual(Object.fromEntries(fields.map((field) => [field, deferred])))
  await expect(page.getByText('경제 관념 항목을 하나 이상 작성해주세요.', { exact: true })).toHaveCount(0)
})
