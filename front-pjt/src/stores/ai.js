import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  getSessions, createSession,
  getMessages, streamMessages, streamMessagesV2,
  approveAiAction, rejectAiAction,
  postAsyncMessage, getAsyncMessage,
  approveAsyncChange, rejectAsyncChange,
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
import {
  createIdempotencyKey,
  lifecycleErrorFromResponse,
  pollUntilTerminal,
  terminalLifecycleError,
  validateAcceptedEnvelope,
  validateDecisionEnvelope,
} from '@/api/aiLifecycle'
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
  const decisionIdempotencyKeys = new Map()

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

  // frozen v2 기본 경로: 접수 후 3초 간격으로 최대 90초 polling
  async function sendMessage(content, { stepId, form = null } = {}) {
    if (!sessionId.value) return null
    cancelActiveStream()
    const controller = new AbortController()
    activeStreamController = controller

    try {
      const response = await postAsyncMessage(
        sessionId.value,
        {
          content,
          step_id: stepId,
          form_data: mapCurrentForm(stepId, form ?? {}),
        },
        {
          idempotencyKey: createIdempotencyKey(),
          signal: controller.signal,
        },
      )
      const accepted = validateAcceptedEnvelope(response.data)
      requestId.value = accepted.request_id
      const terminal = await pollUntilTerminal({
        requestId: accepted.request_id,
        signal: controller.signal,
        poll: async (currentRequestId, options) => {
          const pollingResponse = await getAsyncMessage(
            sessionId.value,
            currentRequestId,
            options,
          )
          return pollingResponse.data
        },
      })
      if (terminal.status !== 'completed') throw terminalLifecycleError(terminal)
      return terminal.message
    } catch (requestError) {
      throw lifecycleErrorFromResponse(
        requestError,
        '메시지 전송 중 오류가 발생했습니다. 다시 시도해주세요.',
      )
    } finally {
      if (activeStreamController === controller) activeStreamController = null
    }
  }

  // v1 SSE는 장애 전환과 비교 검증을 위한 호환 경로다.
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

  // 일반 AI 상담 기본 경로: v2 인증·멱등성 계약을 유지하며 SSE로 수신한다.
  async function* streamMessageV2(content, { stepId, form = null } = {}) {
    if (!sessionId.value) return
    cancelActiveStream()
    const controller = new AbortController()
    activeStreamController = controller

    try {
      const events = streamMessagesV2(
        sessionId.value,
        {
          content,
          step_id: stepId,
          form_data: mapCurrentForm(stepId, form ?? {}),
        },
        {
          idempotencyKey: createIdempotencyKey(),
          signal: controller.signal,
        },
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

  async function decideChange(actionId, changeId, decision) {
    if (!sessionId.value) return null
    const key = `${actionId}:${changeId}:${decision}`
    const idempotencyKey = decisionIdempotencyKeys.get(key) ?? createIdempotencyKey()
    decisionIdempotencyKeys.set(key, idempotencyKey)
    const requester = decision === 'approve' ? approveAsyncChange : rejectAsyncChange
    try {
      const response = await requester(
        sessionId.value,
        actionId,
        changeId,
        { idempotencyKey },
      )
      decisionIdempotencyKeys.delete(key)
      return validateDecisionEnvelope(response.data, { actionId, changeId, decision })
    } catch (requestError) {
      if (requestError?.response) decisionIdempotencyKeys.delete(key)
      throw lifecycleErrorFromResponse(
        requestError,
        'AI 변경 제안을 처리하지 못했습니다. 다시 시도해주세요.',
      )
    }
  }

  const approveChange = (actionId, changeId) => decideChange(actionId, changeId, 'approve')
  const rejectChange = (actionId, changeId) => decideChange(actionId, changeId, 'reject')

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
    decisionIdempotencyKeys.clear()
  }

  return {
    sessionId,
    chatMessages,
    sessions,
    presentationData,
    loading, error,
    initSession, loadSessions, continueLatestSession,
    loadMessages, sendMessage, streamMessage, streamMessageV2, cancelActiveStream,
    approveAction, rejectAction, approveChange, rejectChange,
    saveFormData, loadBasicInfo,
    fetchPresentationData,
    clearSession,
  }
})
