import assert from 'node:assert/strict'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import test from 'node:test'

function readSource(path) {
  return readFileSync(new URL(path, import.meta.url), 'utf8')
}

function readVueDirectory(path) {
  const directory = new URL(path, import.meta.url)
  return readdirSync(directory)
    .filter((name) => name.endsWith('.vue'))
    .map((name) => readFileSync(new URL(name, directory), 'utf8'))
    .join('\n')
}

const consultationStoreSource = readSource('./consultation.js')
const reportStoreSource = readSource('./report.js')
const aiChatViewSource = readSource('../views/ai/AiChatView.vue')
const dashboardSource = [
  readVueDirectory('../views/dashboard/'),
  readVueDirectory('../components/dashboard/'),
].join('\n')
const presentationSource = readVueDirectory('../components/presentation/')

test('상담과 보고서는 두 개의 전용 Store만 사용한다', () => {
  assert.equal(existsSync(new URL('./ai.js', import.meta.url)), false)
  assert.equal(existsSync(new URL('./dashboard.js', import.meta.url)), false)
  assert.match(consultationStoreSource, /defineStore\('consultation'/)
  assert.match(reportStoreSource, /defineStore\('report'/)
})

test('Consultation Store가 세션·채팅·SSE·폼 저장을 소유한다', () => {
  assert.match(consultationStoreSource, /getSessions, createSession/)
  assert.match(consultationStoreSource, /getMessages, streamMessagesV2/)
  assert.match(consultationStoreSource, /async function saveFormData/)
  assert.doesNotMatch(consultationStoreSource, /getReports|presentationData|dashboardOverview/)
})

test('Report Store가 조회·캐시·상태·Dashboard 계산을 소유한다', () => {
  assert.match(reportStoreSource, /getReports/)
  assert.match(reportStoreSource, /reportsBySession/)
  assert.match(reportStoreSource, /const loading = computed/)
  assert.match(reportStoreSource, /function clearActiveReport/)
  assert.match(reportStoreSource, /function clearReports/)
  assert.match(reportStoreSource, /dashboardOverview/)
  assert.match(reportStoreSource, /dashboardCashflow/)
  assert.doesNotMatch(reportStoreSource, /useConsultationStore|streamMessagesV2|saveFormData/)
})

test('Dashboard는 Report Store만 사용하고 기간은 화면 로컬 상태로 둔다', () => {
  assert.match(dashboardSource, /@\/stores\/report/)
  assert.doesNotMatch(dashboardSource, /@\/stores\/(?:ai|consultation|dashboard)/)
  assert.match(dashboardSource, /const selectedPeriod = ref/)
  assert.match(dashboardSource, /const selectedCashflowPeriod = ref/)
})

test('AI 화면만 두 Store를 조합하고 보고서 컴포넌트는 Report Store를 사용한다', () => {
  assert.match(aiChatViewSource, /@\/stores\/consultation/)
  assert.match(aiChatViewSource, /@\/stores\/report/)
  assert.match(presentationSource, /@\/stores\/report/)
  assert.doesNotMatch(presentationSource, /@\/stores\/(?:ai|consultation|dashboard)/)
})
