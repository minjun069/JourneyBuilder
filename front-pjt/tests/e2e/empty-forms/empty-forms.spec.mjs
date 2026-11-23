import { expect, test } from '@playwright/test'

const steps = [
  ['financial-assets', 'financial_assets'], ['pension-assets', 'pension_assets'],
  ['real-estate', 'real_estate_assets'], ['liabilities', null],
  ['income', 'incomes'], ['expenses', 'expenses'], ['financial-goals', 'financial_goals'],
]

async function openEmptyForm(page, step, { failConfirmation = false } = {}) {
  const writes = []
  const sectionCompletion = { basic_info: true, financial_mindset: true, financial_satisfaction: true }
  for (const [id, section] of steps) {
    if (id === step || id === 'liabilities' && step === 'income') break
    if (section) sectionCompletion[section] = true
  }
  const session = { id: 41, status: 'in_progress', section_completion: sectionCompletion }
  await page.addInitScript(() => localStorage.setItem('access_token', 'browser-fixture-token'))
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (request.method() !== 'GET') writes.push(path)
    if (path.endsWith('/confirm-empty/')) {
      if (failConfirmation) return route.fulfill({ status: 500, json: { error: { message: '확인 저장 실패' } } })
      const section = steps.find(([id]) => id === step)?.[1]
      if (section) sectionCompletion[section] = true
      return route.fulfill({ json: { success: true, data: session } })
    }
    let data = []
    if (path.endsWith('/consultations/')) data = [session]
    else if (path.endsWith('/consultations/41/')) data = session
    else if (path.endsWith('/data/basic-info/')) data = { age: 35, retirement_age: 60, life_expectancy: 90, job: '직장인' }
    return route.fulfill({ json: { success: true, data } })
  })
  await page.goto('/ai')
  await page.getByRole('button', { name: '기존 상담 이어하기' }).click()
  if (step === 'liabilities' || step === 'income') {
    await page.locator('.process-sidebar button').filter({ hasText: step === 'liabilities' ? '부채' : '소득' }).click()
    if (step === 'income') await page.getByRole('button', { name: '건너뛰고 이동', exact: true }).click()
  }
  await expect(page.getByRole('button', { name: '작성 완료', exact: true })).toBeEnabled()
  return writes
}

for (const [step] of steps) {
  test(`${step}: 빈 폼은 확인 전 저장하지 않고 확인 후 없음 endpoint로 저장한다`, async ({ page }) => {
    const writes = await openEmptyForm(page, step)
    await page.getByRole('button', { name: '작성 완료', exact: true }).click()
    const label = step === 'financial-assets' ? '보유 금융자산 없음' : '해당 없음으로 진행'
    await expect(page.getByRole('button', { name: label, exact: true })).toBeVisible()
    expect(writes).toEqual([])
    await page.getByRole('button', { name: label, exact: true }).click()
    await expect.poll(() => writes.filter((path) => path.endsWith('/confirm-empty/'))).toEqual([
      `/api/consulting/v1/consultations/41/data/${step}/confirm-empty/`,
    ])
    await expect(page.locator('.form-warning')).toHaveCount(0)
    expect(writes.filter((path) => path.includes('/data/'))).toHaveLength(1)
  })
}

test('없음 확인 저장이 실패하면 경고를 유지하고 다시 확인할 수 있다', async ({ page }) => {
  const writes = await openEmptyForm(page, 'income', { failConfirmation: true })
  await page.getByRole('button', { name: '작성 완료', exact: true }).click()
  await page.getByRole('button', { name: '해당 없음으로 진행', exact: true }).click()
  await expect(page.getByText('확인 저장 실패', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '해당 없음으로 진행', exact: true })).toBeEnabled()
  expect(writes).toHaveLength(1)
})

test('경고 뒤 입력한 데이터는 없음 확인으로 삭제하지 않고 일반 저장한다', async ({ page }) => {
  const writes = await openEmptyForm(page, 'income')
  await page.getByRole('button', { name: '작성 완료', exact: true }).click()
  await page.getByRole('button', { name: '+ 행 추가', exact: true }).click()
  await page.getByPlaceholder('급여 / 이자수익 / 월세 수입 ...').fill('급여')
  await page.getByRole('button', { name: '해당 없음으로 진행', exact: true }).click()
  await expect.poll(() => writes).toEqual(['/api/consulting/v1/consultations/41/data/incomes/'])
  await expect(page.locator('.form-warning')).toHaveCount(0)
})
