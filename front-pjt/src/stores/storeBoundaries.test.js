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
const adminStoreSource = readSource('./admin.js')
const authStoreSource = readSource('./auth.js')
const aiChatViewSource = readSource('../views/ai/AiChatView.vue')
const dashboardSource = [
  readVueDirectory('../views/dashboard/'),
  readVueDirectory('../components/dashboard/'),
].join('\n')
const presentationSource = readVueDirectory('../components/presentation/')
const pbSource = readVueDirectory('../views/pb/')

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
  assert.match(consultationStoreSource, /const isAiRequestActive = ref\(false\)/)
  assert.match(consultationStoreSource, /AI_STREAM_IN_PROGRESS/)
  assert.match(aiChatViewSource, /consultationStore\.isAiRequestActive/)
  assert.doesNotMatch(consultationStoreSource, /getReports|presentationData|dashboardOverview/)
})

test('기존 상담 이어하기는 상태와 무관하게 최신 세션을 선택한다', () => {
  const resumeStart = consultationStoreSource.indexOf('async function continueLatestSession')
  const resumeEnd = consultationStoreSource.indexOf('async function loadActiveSession')
  const resumeSource = consultationStoreSource.slice(resumeStart, resumeEnd)

  assert.match(resumeSource, /const latest = list\[0\]/)
  assert.doesNotMatch(resumeSource, /onboarding|in_progress|\.find\(/)
})

test('Report Store가 보고서와 시뮬레이션의 조회·계산·세션별 캐시를 소유한다', () => {
  assert.match(reportStoreSource, /getReports/)
  assert.match(reportStoreSource, /runBaselineSimulation/)
  assert.match(reportStoreSource, /runWhatIfSimulation/)
  assert.match(reportStoreSource, /reportsBySession/)
  assert.match(reportStoreSource, /simulationsBySession/)
  assert.match(reportStoreSource, /calculatePresentation/)
  assert.match(reportStoreSource, /loadLatestPresentation/)
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
  assert.doesNotMatch(dashboardSource, /@\/api\/simulations/)
})

test('AI 화면만 두 Store를 조합하고 보고서 컴포넌트는 Report Store를 사용한다', () => {
  assert.match(aiChatViewSource, /@\/stores\/consultation/)
  assert.match(aiChatViewSource, /@\/stores\/report/)
  assert.match(presentationSource, /@\/stores\/report/)
  assert.doesNotMatch(presentationSource, /@\/stores\/(?:ai|consultation|dashboard)/)
  assert.doesNotMatch(presentationSource, /@\/api\/simulations/)
})

test('Admin Store가 PB 조회 데이터와 요청 상태를 소유한다', () => {
  assert.match(adminStoreSource, /defineStore\('admin'/)
  assert.match(adminStoreSource, /from '@\/api\/admin'/)
  assert.match(adminStoreSource, /loadingRequestKeys/)
  assert.match(adminStoreSource, /requestErrors/)
  assert.match(adminStoreSource, /activeRequestKeys/)
  assert.match(adminStoreSource, /const inFlight = new Map\(\)/)
  assert.match(adminStoreSource, /function loadCustomers/)
  assert.match(adminStoreSource, /function loadCustomer/)
  assert.match(adminStoreSource, /function loadSummary/)
  assert.match(adminStoreSource, /function loadSegments/)
  assert.match(adminStoreSource, /function clearAdmin/)
})

test('PB 화면은 Admin Store만 소비하고 화면 전용 상태는 로컬에 둔다', () => {
  assert.match(pbSource, /@\/stores\/admin/)
  assert.doesNotMatch(pbSource, /@\/api\/admin/)
  assert.match(pbSource, /const activeAxis = ref/)
  assert.match(pbSource, /const activeTab = ref/)
  assert.match(pbSource, /const statusFilter = ref/)
})

test('로그아웃하면 Admin Store의 고객 데이터도 폐기한다', () => {
  assert.match(authStoreSource, /import\('@\/stores\/admin'\)/)
  assert.match(authStoreSource, /useAdminStore\(\)\.clearAdmin\(\)/)
})
