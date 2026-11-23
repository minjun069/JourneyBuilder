/* global window, document */
import assert from 'node:assert/strict'
import { chromium } from '@playwright/test'

const baseURL = process.argv[2] ?? 'http://127.0.0.1:5174'
const screenshotPath = process.argv[3]
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
page.setDefaultTimeout(10_000)
const pageErrors = []
const apiRequests = []
page.on('pageerror', (error) => pageErrors.push(error.message))
await page.route((url) => url.pathname.startsWith('/api/'), (route) => {
  apiRequests.push(route.request().url())
  return route.abort()
})

try {
  await page.goto(baseURL)
  await page.waitForSelector('nav')
  await page.evaluate(async () => {
    const { useReportStore } = await import('/src/stores/report.js')
    const bundle = await (await fetch('/__dev/presentation-data')).json()
    const app = document.querySelector('#app').__vue_app__
    window.originalReportStore = useReportStore(app.config.globalProperties.$pinia)
    window.originalReportStore.loadDevelopmentPresentation({ ...bundle.scenarios[0], id: 'existing-session' })
    window.originalReports = JSON.stringify(window.originalReportStore.reports)
    localStorage.setItem('presentation-lab-sentinel', 'keep')
    await app.config.globalProperties.$router.push('/dev/presentation')
  })
  await page.waitForSelector('.scenario-control option', { state: 'attached' })
  assert.equal(await page.locator('.lab-item').count(), 10)

  for (const id of ['normal', 'deficit', 'no-assets', 'high-debt']) {
    await page.locator('.scenario-control select').selectOption(id)
    for (let index = 0; index < 10; index++) {
      await page.locator('.lab-item').nth(index).click()
      await page.waitForFunction(() => document.querySelector('.preview-root .slide'))
      const content = await page.locator('.preview-root').innerText()
      assert.ok(!/NaN|undefined|Infinity|계산 데이터를 표시할 수 없습니다/.test(content), `${id}/${index}: ${content}`)
      if (id === 'no-assets' && index === 6) assert.match(content, /시뮬레이션에 필요한 정보를 먼저 보완/)
      if (id === 'high-debt' && index === 0) assert.match(content, /-1억 8,000만원/)
      assert.equal(await page.locator('.simulation-panel button').count(), 0, 'Preview must not expose backend calculation actions')
    }
  }
  await page.locator('.scenario-control select').selectOption('normal')
  await page.locator('.lab-item').nth(5).click()
  await page.getByLabel('채팅 분할 배치').check()
  await page.getByLabel('차트 너비').fill('55')
  await page.getByRole('button', { name: '초기화', exact: true }).click()
  assert.equal(await page.locator('.chat-placeholder').isVisible(), true)
  if (screenshotPath) await page.screenshot({ path: screenshotPath, fullPage: true })
  assert.deepEqual(await page.evaluate(async () => {
    const { useReportStore } = await import('/src/stores/report.js')
    return {
      sameStore: useReportStore() === window.originalReportStore,
      sameData: JSON.stringify(window.originalReportStore.reports) === window.originalReports,
      session: window.originalReportStore.activeSessionId,
      storage: localStorage.getItem('presentation-lab-sentinel'),
    }
  }), { sameStore: true, sameData: true, session: 'dev:existing-session', storage: 'keep' })

  await page.route('**/__dev/presentation-data', (route) => route.fulfill({
    status: 503, contentType: 'application/json', body: JSON.stringify({ message: '계산 데이터를 준비해 주세요: npm run prepare:presentation' }),
  }))
  await page.getByRole('button', { name: '계산 결과 새로 읽기' }).click()
  await page.waitForSelector('.lab-error')
  assert.match(await page.locator('.lab-error').innerText(), /prepare:presentation/)
  assert.equal(await page.locator('.preview-root .slide').count(), 0)
  await page.evaluate(async () => {
    await document.querySelector('#app').__vue_app__.config.globalProperties.$router.push('/')
  })
  assert.equal(await page.evaluate(async () => {
    const { useReportStore } = await import('/src/stores/report.js')
    return useReportStore() === window.originalReportStore
      && JSON.stringify(window.originalReportStore.reports) === window.originalReports
  }), true)
  assert.deepEqual(pageErrors, [])
  assert.deepEqual(apiRequests, [])
  console.log('PASS: 4 scenarios × 10 slides, split/reset, error recovery, Store/localStorage isolation, no Backend/AI requests')
} catch (error) {
  console.error({ pageErrors, apiRequests, body: (await page.locator('body').innerText()).slice(0, 1500) })
  if (screenshotPath) await page.screenshot({ path: screenshotPath, fullPage: true })
  throw error
} finally {
  await browser.close()
}
