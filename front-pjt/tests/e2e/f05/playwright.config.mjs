import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: '.',
  testMatch: process.env.F05_TEST_MATCH ?? 'harness-smoke.spec.mjs',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [['line']],
  use: {
    baseURL: process.env.F05_BASE_URL ?? 'http://front:5173',
    browserName: 'chromium',
    headless: true,
    trace: 'retain-on-failure',
  },
})
