import { ref } from 'vue'
import { defineStore } from 'pinia'
import {
  getSessions, createSession,
  getMessages, streamMessagesV2,
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
import { createIdempotencyKey } from '@/api/aiLifecycle'

export const useConsultationStore = defineStore('consultation', () => {
  const sessionId = ref(null)
  const requestId = ref(null)
  const chatMessages = ref([])
  const sessions = ref([])
  let activeStreamController = null

  function clearActiveSession() {
    cancelActiveStream()
    sessionId.value = null
    requestId.value = null
    chatMessages.value = []
  }

  // 새 상담 시작: 항상 신규 세션 생성
  async function initSession() {
    clearActiveSession()

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
    clearActiveSession()
    if (!latest) return null

    sessionId.value = latest.id
    return latest.id
  }

  // 채팅 메시지 목록 로드
  async function loadMessages() {
    if (!sessionId.value) return
    const { data: res } = await getMessages(sessionId.value)
    chatMessages.value = res.data
  }

  // Browser의 모든 대화형 AI 요청은 v2 인증·멱등성 계약을 유지하며 SSE로 수신한다.
  async function* streamMessageV2(content, {
    stepId,
    form = null,
    requestMode = 'chat',
    reviewTrigger = null,
    elicitAction = null,
  } = {}) {
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
          request_mode: requestMode,
          ...(reviewTrigger ? { review_trigger: reviewTrigger } : {}),
          ...(elicitAction ? { elicit_action: elicitAction } : {}),
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

  async function reviewForm(stepId, form, trigger = 'user_request') {
    for await (const { event, data } of streamMessageV2(
      '현재 입력값을 검토해 주세요.',
      {
        stepId,
        form,
        requestMode: 'error_review',
        reviewTrigger: trigger,
      },
    )) {
      // Error Review는 구조화된 최종 결과만 화면에 반영한다.
      if (event === 'start') continue
      if (event === 'done') return data.message
      if (event === 'error') {
        const reviewError = new Error(data.error.message)
        reviewError.code = data.error.code
        reviewError.retryable = data.error.retryable
        throw reviewError
      }
    }
    throw new Error('AI 검토 스트림이 완료 이벤트 없이 종료되었습니다.')
  }

  function cancelActiveStream() {
    activeStreamController?.abort()
    activeStreamController = null
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
    if (!handler) return
    return handler(sessionId.value, formData)
  }

  async function loadBasicInfo() {
    if (!sessionId.value) return null
    const { data: res } = await getBasicInfo(sessionId.value)
    return res.data
  }

  function clearConsultation() {
    clearActiveSession()
    sessions.value = []
  }

  return {
    sessionId,
    chatMessages,
    sessions,
    initSession, loadSessions, continueLatestSession,
    loadMessages, streamMessageV2, reviewForm, cancelActiveStream,
    saveFormData, loadBasicInfo,
    clearConsultation,
  }
})
