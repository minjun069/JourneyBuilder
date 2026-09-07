import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  getSessions, createSession,
  getMessages, streamMessages,
  approveAiAction, rejectAiAction,
  getBasicInfo,
  putBasicInfo, putFinancialMindset, putFinancialSatisfaction,
  putFinancialAssets, putPensionAssets, putRealEstateAssets,
  putLiabilities, putIncomes, putExpenses,
  putGoalPeriods, putGoalMilestones,
  mapBasicInfo, mapFinancialMindset, mapFinancialSatisfaction,
  mapFinancialAssets, mapPensionAssets, mapRealEstateAssets,
  mapLiabilities, mapIncomes, mapExpenses,
  mapGoalPeriods, mapGoalMilestones,
  mapCurrentForm,
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
  let activeStreamController = null

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

  // 사용자 메시지 전송 및 AI SSE 응답 수신
  async function* streamMessage(content, { stepId, form = null } = {}) {
    if (!sessionId.value) return
    cancelActiveStream()
    const controller = new AbortController()
    activeStreamController = controller

    try {
      const events = streamMessages(
        sessionId.value,
        {
          content,
          step_id: stepId,
          form_data: mapCurrentForm(stepId, form ?? {}),
        },
        { signal: controller.signal },
      )
      for await (const event of events) {
        if (event.data?.request_id) requestId.value = event.data.request_id
        yield event
      }
    } finally {
      if (activeStreamController === controller) activeStreamController = null
    }
  }

  function cancelActiveStream() {
    activeStreamController?.abort()
    activeStreamController = null
  }

  async function approveAction(messageId) {
    if (!sessionId.value) return
    return approveAiAction(sessionId.value, messageId)
  }

  async function rejectAction(messageId) {
    if (!sessionId.value) return
    return rejectAiAction(sessionId.value, messageId)
  }

  function syncIds(sourceRows, savedRows) {
    ;(sourceRows ?? []).forEach((row, index) => {
      if (savedRows?.[index]?.id != null) row.id = savedRows[index].id
    })
  }

  async function saveList(putter, mapper, sid, form) {
    const response = await putter(sid, mapper(form))
    syncIds(form.assets, response.data?.data)
    return response
  }

  async function saveGoals(sid, form) {
    const periodResponse = await putGoalPeriods(sid, mapGoalPeriods(form))
    const sourcePeriods = ['before', 'after'].flatMap((phase) => form[phase]?.periods ?? [])
    syncIds(sourcePeriods, periodResponse.data?.data)
    sourcePeriods.forEach((period, periodIndex) => {
      syncIds(period.items, periodResponse.data?.data?.[periodIndex]?.items)
    })

    const milestoneResponse = await putGoalMilestones(sid, mapGoalMilestones(form))
    const sourceMilestones = ['before', 'after'].flatMap(
      (phase) => (form[phase]?.milestones ?? []).filter((item) => item.age),
    )
    syncIds(sourceMilestones, milestoneResponse.data?.data)
    return { periods: periodResponse, milestones: milestoneResponse }
  }

  // 스텝별 폼 데이터 → 백엔드 저장
  const FORM_SAVE_MAP = {
    'basic-info':             (sid, form) => putBasicInfo(sid, mapBasicInfo(form)),
    'financial-mindset':      (sid, form) => putFinancialMindset(sid, mapFinancialMindset(form)),
    'financial-satisfaction': (sid, form) => putFinancialSatisfaction(sid, mapFinancialSatisfaction(form)),
    'financial-assets':       (sid, form) => saveList(putFinancialAssets, mapFinancialAssets, sid, form),
    'pension-assets':         (sid, form) => saveList(putPensionAssets, mapPensionAssets, sid, form),
    'real-estate':            (sid, form) => saveList(putRealEstateAssets, mapRealEstateAssets, sid, form),
    'liabilities':            (sid, form) => saveList(putLiabilities, mapLiabilities, sid, form),
    'income':                 (sid, form) => saveList(putIncomes, mapIncomes, sid, form),
    'expenses':               (sid, form) => saveList(putExpenses, mapExpenses, sid, form),
    'financial-goals':        saveGoals,
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
    cancelActiveStream()
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
    loadMessages, streamMessage, cancelActiveStream, approveAction, rejectAction,
    saveFormData, loadBasicInfo,
    fetchPresentationData,
    clearSession,
  }
})
