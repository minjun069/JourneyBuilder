import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  getSessions, createSession,
  getMessages, postMessage, getMessage,
  getBasicInfo,
  putBasicInfo, putFinancialMindset, putFinancialSatisfaction,
  putFinancialAssets, putPensionAssets, putRealEstateAssets,
  putLiabilities, putIncomes, putExpenses,
  putGoalPeriods, putGoalMilestones,
  mapBasicInfo, mapFinancialMindset, mapFinancialSatisfaction,
  mapFinancialAssets, mapPensionAssets, mapRealEstateAssets,
  mapLiabilities, mapIncomes, mapExpenses,
  mapGoalPeriods, mapGoalMilestones,
} from '@/api/consulting'
import { getReports } from '@/api/reports'
import { MOCK_PRESENTATION_DATA } from '@/mock/aiData'
import { normalizePresentationData } from '@/utils/reportData'

export const useAiStore = defineStore('ai', () => {

  const sessionId        = ref(null)
  const requestId        = ref(null)
  const chatMessages     = ref([])
  const sessions         = ref([])
  const presentationData = ref(null)
  const loading          = ref(false)
  const error            = ref(null)

  // 새 상담 시작: 항상 신규 세션 생성
  async function initSession() {
    clearSession()

    const { data: createRes } = await createSession()
    return (sessionId.value = createRes.data.id)
  }

  // 기존 상담 조회
  async function loadSessions() {
    const { data: listRes } = await getSessions()
    sessions.value = listRes.data ?? []
    return sessions.value
  }

  // 기존 상담 이어하기: 최신 세션 선택
  async function continueLatestSession() {
    const list = await loadSessions()
    const latest = list[0]
    if (!latest) {
      clearSession()
      return null
    }

    clearSession()
    sessionId.value = latest.id
    return latest.id
  }

  // 채팅 메시지 목록 로드
  async function loadMessages() {
    if (!sessionId.value) return
    const { data: res } = await getMessages(sessionId.value)
    chatMessages.value = res.data
  }

  // 사용자 메시지 전송
  async function sendMessage(content, { stepId, form = null } = {}) {
    if (!sessionId.value) return
    const { data: res } = await postMessage(sessionId.value, { content, step_id: stepId, form })
    return (requestId.value = res.data.request_id)
  }

  // AI 메시지 수신 
  async function receiveMessage() {
    if (!sessionId.value || !requestId.value) return null
    const { data: res } = await getMessage(sessionId.value, requestId.value)
    const message = res.data?.message
    if (!message) return null
    return {
      message: message.content,
      update:  message.extracted_data?.pending_changes ?? null,
    }
  }

  // 스텝별 폼 데이터 → 백엔드 저장
  const FORM_SAVE_MAP = {
    'basic-info':             (sid, form) => putBasicInfo(sid, mapBasicInfo(form)),
    'financial-mindset':      (sid, form) => putFinancialMindset(sid, mapFinancialMindset(form)),
    'financial-satisfaction': (sid, form) => putFinancialSatisfaction(sid, mapFinancialSatisfaction(form)),
    'financial-assets':       (sid, form) => putFinancialAssets(sid, mapFinancialAssets(form)),
    'pension-assets':         (sid, form) => putPensionAssets(sid, mapPensionAssets(form)),
    'real-estate':            (sid, form) => putRealEstateAssets(sid, mapRealEstateAssets(form)),
    'liabilities':            (sid, form) => putLiabilities(sid, mapLiabilities(form)),
    'income':                 (sid, form) => putIncomes(sid, mapIncomes(form)),
    'expenses':               (sid, form) => putExpenses(sid, mapExpenses(form)),
    'financial-goals': async  (sid, form) => {
      await putGoalPeriods(sid, mapGoalPeriods(form))
      await putGoalMilestones(sid, mapGoalMilestones(form))
    },
  }

  async function saveFormData(stepId, formData) {
    if (!sessionId.value) return
    const handler = FORM_SAVE_MAP[stepId]
    if (!handler) return   // financial-info 등 저장 불필요 스텝은 그냥 통과
    return handler(sessionId.value, formData)
  }

  async function loadBasicInfo() {
    if (!sessionId.value) return null
    const { data: res } = await getBasicInfo(sessionId.value)
    return res.data
  }

  // 진단 보고서 전체 조회
  async function fetchPresentationData() {
    loading.value = true
    error.value   = null
    try {
      if (import.meta.env.DEV) {
        presentationData.value = normalizePresentationData(MOCK_PRESENTATION_DATA)
        return
      }
      if (!sessionId.value) return

      const { data: res } = await getReports(sessionId.value)
      const mapped = {}
      for (const report of res.data) {
        mapped[report.report_type] = report.content
      }
      presentationData.value = normalizePresentationData(mapped)
    } catch (err) {
      error.value = err
    } finally {
      loading.value = false
    }
  }

  // 로그아웃 시 실행
  function clearSession() {
    sessionId.value        = null
    requestId.value        = null
    chatMessages.value     = []
    sessions.value         = []
    presentationData.value = null
    error.value            = null
  }

  return {
    sessionId,
    chatMessages,
    sessions,
    presentationData,
    loading, error,
    initSession, loadSessions, continueLatestSession,
    loadMessages, sendMessage, receiveMessage,
    saveFormData, loadBasicInfo,
    fetchPresentationData,
    clearSession,
  }
})
