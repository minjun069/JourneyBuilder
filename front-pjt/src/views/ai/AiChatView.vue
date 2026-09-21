<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useConsultationStore } from '@/stores/consultation'
import { useReportStore } from '@/stores/report'
import { mapCurrentForm } from '@/api/consulting'
import {
  applyCanonicalChanges,
  applyErrorReviewSuggestion,
  cancelErrorReviewSuggestion,
  continueSaveAfterOptionalReview,
  visibleErrorReviewItems,
} from '@/utils/formChanges'
import { decideFormChangeLocally } from '@/utils/formDecision'
import { addSentenceLineBreaks, renderChatMarkdown } from '@/utils/chatMarkdown'
import { validateConsultationForm } from '@/utils/formValidation'
import {
  cloneFormState,
  completedStepsForSession,
  replaceFormState,
  resolveResumeStep,
} from '@/utils/consultationState'
import { PROCESS_STEPS, PRESENTATION_STEP_IDS, STEP_IDS } from './data/processSteps.js'
import { initialMessages, STEP_GUIDE_MESSAGES } from './data/stepGuideMessages.js'
import { createPresentationEntryIdempotencyKey } from '@/api/aiLifecycle'
import {
  hasPresentationEntryReceipt,
  presentationEntryReceiptKey,
  savePresentationEntryReceipt,
} from '@/utils/presentationEntry'
import BasicInfoForm from '@/components/forms/BasicInfoForm.vue'
import FinancialMindsetForm from '@/components/forms/FinancialMindsetForm.vue'
import FinancialSatisfactionForm from '@/components/forms/FinancialSatisfactionForm.vue'
import FinancialInfoForm from '@/components/forms/FinancialInfoForm.vue'
import FinancialAssetsForm from '@/components/forms/FinancialAssetsForm.vue'
import PensionAssetsForm from '@/components/forms/PensionAssetsForm.vue'
import RealEstateForm from '@/components/forms/RealEstateForm.vue'
import LiabilitiesForm from '@/components/forms/LiabilitiesForm.vue'
import IncomeForm from '@/components/forms/IncomeForm.vue'
import ExpensesForm from '@/components/forms/ExpensesForm.vue'
import FinancialGoalsForm from '@/components/forms/FinancialGoalsForm.vue'
import BalanceSheetSlide from '@/components/presentation/BalanceSheetSlide.vue'
import CashFlowSlide from '@/components/presentation/CashFlowSlide.vue'
import FinancialIndicatorsSlide from '@/components/presentation/FinancialIndicatorsSlide.vue'
import FinancialGoalSlide from '@/components/presentation/FinancialGoalSlide.vue'
import RetirementFundSlide from '@/components/presentation/RetirementFundSlide.vue'
import RetirementReadinessSlide from '@/components/presentation/RetirementReadinessSlide.vue'
import RetirementSimulationSlide from '@/components/presentation/RetirementSimulationSlide.vue'
import PensionScheduleSlide from '@/components/presentation/PensionScheduleSlide.vue'
import LifetimeCashflowSlide from '@/components/presentation/LifetimeCashflowSlide.vue'
import RequiredSavingsSlide from '@/components/presentation/RequiredSavingsSlide.vue'
import ProcessSidebar from './ProcessSidebar.vue'
import StepSkipWarningModal from './StepSkipWarningModal.vue'

const consultationStore = useConsultationStore()
const reportStore = useReportStore()
const hasActiveSession = computed(() => !!consultationStore.sessionId)

// ─── Resize ───────────────────────────────────────────────
const mainAreaRef = ref(null)
const dragging    = ref(false)
const leftWidth   = ref(71.4)
const rightWidth  = ref(28.6)

function startResize(event) {
  dragging.value = true
  event.preventDefault()
  document.body.style.cursor = 'col-resize'
}

function onResizeMove(event) {
  if (!dragging.value || !mainAreaRef.value) return
  const bounds   = mainAreaRef.value.getBoundingClientRect()
  const nextLeft = ((event.clientX - bounds.left) / bounds.width) * 100
  if (isPresentationPhase.value) {
    leftWidth.value = Math.min(45, Math.max(15, nextLeft))
  } else {
    leftWidth.value = Math.min(85, Math.max(55, nextLeft))
  }
  rightWidth.value = 100 - leftWidth.value
}

function stopResize() {
  dragging.value = false
  document.body.style.cursor = ''
}

// ─── 스텝 네비게이션 ───────────────────────────────────────
const currentStepId    = ref('intro')
const visitedStepIds   = ref(new Set())
const completedStepIds = ref(new Set())
const skippedStepIds   = ref(new Set())

const isPresentationPhase = computed(() => {
  const step = PROCESS_STEPS.find((s) => s.id === currentStepId.value)
  return step?.phase === 'presentation'
})

const isIntroPhase = computed(() => currentStepId.value === 'intro')

function dismissNextStepAction() {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    if (messages.value[i].action?.type === 'next-step') {
      messages.value[i] = { ...messages.value[i], action: undefined }
      break
    }
  }
}

async function navigateToStep(stepId) {
  if (currentStepId.value === stepId) return
  try {
    captureActiveFormDraft()
  } catch {
    sessionError.value = '현재 입력 화면의 임시본을 보관하지 못했지만 요청한 단계로 이동했습니다.'
  }
  dismissNextStepAction()
  const next = new Set(visitedStepIds.value)
  next.add(currentStepId.value)
  visitedStepIds.value = next
  currentStepId.value  = stepId
  clearFormWarning()
  await nextTick()
  await hydrateActiveForm(stepId)

  const alreadyVisited = stepId === 'intro' || messages.value.some((m) => m.role === 'divider' && m.stepId === stepId)
  if (alreadyVisited) {
    scrollToStepStart(stepId, true)
    return
  }

  const step = PROCESS_STEPS.find((s) => s.id === stepId)
  if (step) {
    messages.value.push({ role: 'divider', stepId, label: step.label?.replace('\n', ' ') ?? stepId })
  }

  if (PRESENTATION_STEP_IDS.includes(stepId)) {
    scrollToStepStart(stepId, true)
    void maybeSendPresentationEntryGuide(stepId)
    return
  }

  const guide = STEP_GUIDE_MESSAGES[stepId]
  if (guide) {
    const guideMsgs   = Array.isArray(guide) ? guide : [guide]
    scrollToStepStart(stepId, true)
    const consentIdx  = guideMsgs.findIndex((m) => m.action?.type === 'consent')
    const preConsent  = consentIdx >= 0 ? guideMsgs.slice(0, consentIdx + 1) : guideMsgs
    const postConsent = consentIdx >= 0 ? guideMsgs.slice(consentIdx + 1) : []
    void streamAiMessages(preConsent)
    if (postConsent.length > 0) pendingConsentMessages.value = postConsent
  } else {
    scrollToStepStart(stepId, true)
  }
}

const warningModal = reactive({ show: false, targetStepId: null, targetLabel: '' })

function handleStepClick(step) {
  if (isInteractionBusy.value || step.depth === 0) return
  const targetIdx          = STEP_IDS.indexOf(step.id)
  const currentIdx         = STEP_IDS.indexOf(currentStepId.value)
  const isNextStep         = targetIdx === currentIdx + 1
  const isCurrentCompleted = completedStepIds.value.has(currentStepId.value)
  if (targetIdx > currentIdx && !isPresentationPhase.value && !(isNextStep && isCurrentCompleted)) {
    warningModal.show         = true
    warningModal.targetStepId = step.id
    warningModal.targetLabel  = step.label.replace('\n', ' ')
  } else {
    navigateToStep(step.id)
  }
}

function closeWarning() {
  warningModal.show         = false
  warningModal.targetStepId = null
}

function confirmStepJump() {
  if (warningModal.targetStepId) {
    const targetIdx = STEP_IDS.indexOf(warningModal.targetStepId)
    const currentIdx = STEP_IDS.indexOf(currentStepId.value)
    const skipped = new Set(skippedStepIds.value)
    PROCESS_STEPS.slice(currentIdx + 1, targetIdx).forEach((step) => {
      if (step.depth > 0 && step.phase === 'collection' && !completedStepIds.value.has(step.id)) {
        skipped.add(step.id)
      }
    })
    skippedStepIds.value = skipped
    void navigateToStep(warningModal.targetStepId)
  }
  closeWarning()
}

// ─── 채팅 ─────────────────────────────────────────────────
const messages               = ref([])
const pendingConsentMessages = ref([])
const draftMessage           = ref('')
const chatListRef            = ref(null)
const chatSpacerRef          = ref(null)
const chatInputRef           = ref(null)
const isTyping               = ref(false)
const isAnimatingResponse    = ref(false)
const isStreamingResponse    = ref(false)
const sessionLoading         = ref(false)
const sessionError           = ref('')
let animationGeneration = 0
let animationQueue = Promise.resolve()
const animationTimers = new Set()
const hasOpenErrorReview = computed(() => messages.value.some(
  (message) => message.action?.type === 'error-review',
))

const isInteractionBusy = computed(() => (
  sessionLoading.value
  || isTyping.value
  || isAnimatingResponse.value
  || isStreamingResponse.value
  || consultationStore.streaming
  || formSaving.value
  || reviewLoading.value
  || hasOpenErrorReview.value
))

function scrollToBottom(smooth = false, downOnly = false) {
  nextTick(() => {
    if (!chatListRef.value) return
    const spacerH = chatSpacerRef.value?.offsetHeight ?? 0
    const top     = chatListRef.value.scrollHeight - spacerH - chatListRef.value.clientHeight
    if (downOnly && top <= chatListRef.value.scrollTop) return
    chatListRef.value.scrollTo({ top, behavior: smooth ? 'smooth' : 'auto' })
  })
}

function scrollToStepStart(stepId, smooth = false) {
  nextTick(() => {
    if (!chatListRef.value) return
    if (stepId === 'intro') {
      chatListRef.value.scrollTo({ top: 0, behavior: smooth ? 'smooth' : 'auto' })
      return
    }
    if (chatSpacerRef.value) {
      chatSpacerRef.value.style.minHeight = chatListRef.value.clientHeight + 'px'
    }
    const target       = chatListRef.value.querySelector(`[data-step-id="${stepId}"]`)
    if (!target) return
    const containerTop = chatListRef.value.getBoundingClientRect().top
    const targetTop    = target.getBoundingClientRect().top
    chatListRef.value.scrollBy({ top: targetTop - containerTop, behavior: smooth ? 'smooth' : 'auto' })
  })
}

function animationDelay(milliseconds, generation) {
  return new Promise((resolve) => {
    const record = { timer: null, resolve }
    record.timer = setTimeout(() => {
      animationTimers.delete(record)
      resolve(generation === animationGeneration)
    }, milliseconds)
    animationTimers.add(record)
  })
}

function cancelMessageAnimations() {
  animationGeneration += 1
  for (const record of animationTimers) {
    clearTimeout(record.timer)
    record.resolve(false)
  }
  animationTimers.clear()
  animationQueue = Promise.resolve()
  isTyping.value = false
  isAnimatingResponse.value = false
}

function streamAiMessages(bubbles, { typingDelay = 220, onDone } = {}) {
  const queue = Array.isArray(bubbles) ? bubbles : []
  const generation = animationGeneration
  const run = async () => {
    if (!queue.length || generation !== animationGeneration) return []
    isAnimatingResponse.value = true
    const emitted = []
    try {
      for (const bubble of queue) {
        isTyping.value = true
        if (!await animationDelay(typingDelay, generation)) return emitted
        isTyping.value = false

        const fullText = addSentenceLineBreaks(bubble.text)
        const characters = Array.from(fullText)
        const msg = reactive({
          role: 'ai',
          text: fullText,
          displayText: '',
          streaming: true,
          ...(bubble.action ? { action: bubble.action } : {}),
        })
        messages.value.push(msg)
        emitted.push(msg)

        for (let index = 0; index < characters.length; index += 2) {
          if (generation !== animationGeneration) return emitted
          msg.displayText = characters.slice(0, index + 2).join('')
          scrollToBottom(false, true)
          const lastCharacter = characters[index + 1] ?? characters[index]
          const delay = '.!?\n'.includes(lastCharacter) ? 45 : 12
          if (!await animationDelay(delay, generation)) return emitted
        }
        msg.displayText = fullText
        msg.streaming = false
        if (bubble.autoAction) executeAutoAction(bubble.autoAction)
      }
      onDone?.()
      return emitted
    } finally {
      if (generation === animationGeneration) {
        isTyping.value = false
        isAnimatingResponse.value = false
      }
    }
  }
  const queued = animationQueue.then(run, run)
  animationQueue = queued.catch(() => {})
  return queued
}

function resetConversationState() {
  cancelMessageAnimations()
  currentStepId.value = 'intro'
  visitedStepIds.value = new Set()
  completedStepIds.value = new Set()
  skippedStepIds.value = new Set()
  messages.value = []
  pendingConsentMessages.value = []
  draftMessage.value = ''
  resizeChatInput()
  basicInfoData.value = null
  isTyping.value = false
  isAnimatingResponse.value = false
  isStreamingResponse.value = false
  leftWidth.value = 71.4
  rightWidth.value = 28.6
  reportStore.clearActiveReport()
  isRestoredSession.value = false
  Object.keys(formDrafts).forEach((key) => delete formDrafts[key])
  emptyConfirmedStepIds.value = new Set()
  clearFormWarning()
}

function showInitialMessages() {
  messages.value = []
  void streamAiMessages(initialMessages)
}

function toChatMessage(message, { interactive = true } = {}) {
  const bubble = {
    role: message.role === 'assistant' ? 'ai' : message.role,
    text: message.content,
  }
  const action = message.extracted_data?.action
  if (
    interactive && action?.type === 'form_change'
    && typeof action.action_id === 'string'
    && Array.isArray(action.changes)
    && action.changes.length > 0
  ) {
    bubble.action = {
      ...action,
      type: 'form-update',
      messageId: message.id,
    }
  }
  const review = message.extracted_data?.review
  if (interactive && review?.type === 'error_review' && Array.isArray(review.items)) {
    const visibleItems = visibleErrorReviewItems(review.items)
    bubble.action = {
      type: 'error-review',
      items: visibleItems.map((item) => ({ ...item, decision: 'pending' })),
    }
  }
  return bubble
}

function restoreStoredMessages() {
  messages.value = consultationStore.chatMessages
    .filter((message) => !(
      message.role === 'user'
      && message.extracted_data?.system_event === 'presentation_entry'
    ))
    .map((message) => toChatMessage(message, { interactive: false }))
}

async function startNewConsultation() {
  sessionError.value = ''
  sessionLoading.value = true
  try {
    resetConversationState()
    await consultationStore.initSession()
    showInitialMessages()
  } catch {
    sessionError.value = '새 상담 세션을 만들지 못했습니다. 잠시 후 다시 시도해주세요.'
  } finally {
    sessionLoading.value = false
  }
}

async function continueConsultation() {
  sessionError.value = ''
  sessionLoading.value = true
  try {
    resetConversationState()
    const sessionId = await consultationStore.continueLatestSession()
    if (!sessionId) {
      sessionError.value = '이어갈 기존 상담이 없습니다. 새 상담을 시작해주세요.'
      return
    }

    await consultationStore.loadMessages()
    const hasStoredMessages = consultationStore.chatMessages.length > 0
    if (hasStoredMessages) {
      restoreStoredMessages()
    }
    const resumeStep = await restoreConsultationProcess(consultationStore.activeSession)
    if (!hasStoredMessages) {
      if (resumeStep === 'intro') showInitialMessages()
      else void streamAiMessages([{ text: '저장된 상담 내용을 불러왔습니다. 마지막으로 완료하지 못한 단계부터 이어서 진행하겠습니다.' }])
    }
  } catch {
    sessionError.value = '기존 상담을 불러오지 못했습니다. 잠시 후 다시 시도해주세요.'
  } finally {
    sessionLoading.value = false
  }
}

async function streamAssistantMessage(text, options) {
  for await (const { event, data } of consultationStore.streamMessageV2(text, options)) {
    if (event === 'start') continue
    if (event === 'error') {
      const error = new Error(data.error.message)
      error.code = data.error.code
      error.retryable = data.error.retryable
      throw error
    }
    if (event === 'done') {
      const finalMessage = toChatMessage(data.message)
      const [rendered] = await streamAiMessages([finalMessage], { typingDelay: 120 })
      return rendered ?? finalMessage
    }
  }
  throw new Error('AI 스트림이 완료 이벤트 없이 종료되었습니다.')
}

const presentationGuideConsumed = new Set()
const presentationGuideInFlight = new Set()

function presentationGuideStorageKey(stepId, reportVersion) {
  return presentationEntryReceiptKey({
    sessionId: consultationStore.sessionId,
    stepId,
    reportVersion,
  })
}

function wasPresentationGuideConsumed(key) {
  return hasPresentationEntryReceipt(presentationGuideConsumed, sessionStorage, key)
}

function rememberPresentationGuide(key) {
  savePresentationEntryReceipt(presentationGuideConsumed, sessionStorage, key)
}

async function maybeSendPresentationEntryGuide(stepId) {
  const sessionId = consultationStore.sessionId
  if (!sessionId || !PRESENTATION_STEP_IDS.includes(stepId)) return

  if (
    !reportStore.reports
    || (stepId === 'retirement-simulation' && !reportStore.simulation && !reportStore.simulationError)
  ) {
    await reportStore.calculatePresentation(sessionId)
  }
  if (currentStepId.value !== stepId) return

  const reportVersion = reportStore.presentationReportVersion(stepId)
  const storageKey = presentationGuideStorageKey(stepId, reportVersion)
  if (wasPresentationGuideConsumed(storageKey) || presentationGuideInFlight.has(storageKey)) return

  presentationGuideInFlight.add(storageKey)
  isTyping.value = true
  isStreamingResponse.value = true
  try {
    const idempotencyKey = await createPresentationEntryIdempotencyKey({
      sessionId,
      stepId,
      reportVersion,
    })
    await streamAssistantMessage('이 보고서의 핵심을 먼저 안내해 주세요.', {
      stepId,
      form: null,
      requestMode: 'presentation_entry',
      idempotencyKey,
    })
    rememberPresentationGuide(storageKey)
  } catch (error) {
    if (error?.name !== 'AbortError' && currentStepId.value === stepId) {
      const fallback = STEP_GUIDE_MESSAGES[stepId]
      if (fallback) {
        await streamAiMessages(Array.isArray(fallback) ? fallback : [fallback], {
          typingDelay: 100,
        })
      }
    }
  } finally {
    presentationGuideInFlight.delete(storageKey)
    isTyping.value = false
    isStreamingResponse.value = false
  }
}

async function sendMessage() {
  const text = draftMessage.value.trim()
  if (!text || isInteractionBusy.value || !hasActiveSession.value) return
  sessionError.value = ''
  messages.value.push({ role: 'user', text })
  draftMessage.value = ''
  resizeChatInput()
  scrollToBottom()
  isTyping.value = true
  isStreamingResponse.value = true

  try {
    await streamAssistantMessage(text, {
      stepId: currentStepId.value,
      form: activeFormRef.value?.form ?? null,
    })
  } catch (error) {
    if (error?.name === 'AbortError') return
    await streamAiMessages([{
      text: error?.message ?? '메시지 전송 중 오류가 발생했습니다. 다시 시도해주세요.',
    }], { typingDelay: 100 })
  } finally {
    isTyping.value = false
    isStreamingResponse.value = false
  }
}

function handleKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    sendMessage()
  }
}

function resizeChatInput() {
  nextTick(() => {
    const input = chatInputRef.value
    if (!input) return
    input.style.height = 'auto'
    input.style.height = `${input.scrollHeight}px`
  })
}

async function executeAction(action) {
  if (isInteractionBusy.value) return
  if (action.type === 'next-step') await navigateToStep(action.stepId)
}

function dismissAction(msg) {
  const idx = messages.value.indexOf(msg)
  if (idx !== -1) messages.value[idx] = { ...messages.value[idx], action: undefined }
}

function decisionStatusLabel(status) {
  if (status === 'approved') return '승인됨'
  if (status === 'rejected') return '거절됨'
  return '확인 필요'
}

function decideFormChange(msg, change, decision) {
  const action = msg.action
  if (change.decision_status !== 'pending') return
  if (
    decision === 'approved'
    && action.target === 'draft_form'
    && (action.step_id !== currentStepId.value || !activeFormRef.value?.form)
  ) {
    sessionError.value = '제안이 만들어진 입력 단계에서 승인해주세요.'
    return
  }
  try {
    decideFormChangeLocally(action, change, decision, (approvedChange) => {
      applyCanonicalChanges(action.step_id, activeFormRef.value.form, [approvedChange])
    })
  } catch {
    sessionError.value = '현재 폼에 제안 값을 반영하지 못했습니다. 입력 단계를 확인해주세요.'
  }
}

function canApproveFormChange(action) {
  return action.target !== 'draft_form'
    || (action.step_id === currentStepId.value && Boolean(activeFormRef.value?.form))
}

function formatActionValue(value, unit = '') {
  const formatted = value != null && typeof value === 'object'
    ? JSON.stringify(value)
    : String(value ?? '없음')
  return unit && formatted !== '없음' ? `${formatted} ${unit}` : formatted
}

function hasPendingReviewItems(action) {
  return action?.items?.some((item) => item.decision === 'pending') ?? false
}

const approveFormChange = (msg, change) => decideFormChange(msg, change, 'approved')
const rejectFormChange = (msg, change) => decideFormChange(msg, change, 'rejected')

function handleConsent() {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    if (messages.value[i].action?.type === 'consent') {
      messages.value[i] = { ...messages.value[i], action: undefined }
      break
    }
  }
  const msgs = pendingConsentMessages.value
  pendingConsentMessages.value = []
  if (!msgs.length) return
  void streamAiMessages(msgs)
}

// ─── 폼 ──────────────────────────────────────────────────
const STEP_FORM_MAP = {
  'basic-info':             { title: '기본 정보',       component: BasicInfoForm },
  'financial-mindset':      { title: '경제 관념',       component: FinancialMindsetForm },
  'financial-satisfaction': { title: '재무 만족도',     component: FinancialSatisfactionForm },
  'financial-info':         { title: '재무 정보 안내',  component: FinancialInfoForm },
  'financial-assets':       { title: '금융자산 현황',   component: FinancialAssetsForm },
  'pension-assets':         { title: '연금자산 현황',   component: PensionAssetsForm },
  'real-estate':            { title: '부동산자산 현황', component: RealEstateForm },
  'liabilities':            { title: '부채 현황 정리',  component: LiabilitiesForm },
  'income':                 { title: '소득 현황 조사',  component: IncomeForm },
  'expenses':               { title: '지출 현황 분석',  component: ExpensesForm },
  'financial-goals':        { title: '재무목표',        component: FinancialGoalsForm },
}

const activeFormComponent = computed(() => STEP_FORM_MAP[currentStepId.value]?.component)
const activeFormTitle     = computed(() => STEP_FORM_MAP[currentStepId.value]?.title ?? '데이터 입력')
const activeFormRef       = ref(null)
const basicInfoData       = ref(null)
const formSaving          = ref(false)
const reviewLoading       = ref(false)
const restoringForm       = ref(false)
const formDrafts          = reactive({})
const emptyConfirmedStepIds = ref(new Set())
const formWarning         = reactive({
  code: null,
  canConfirmEmpty: false,
  confirmationLabel: null,
  errors: [],
})
const isRestoredSession   = ref(false)
const isFinancialInfoStep = computed(() => currentStepId.value === 'financial-info')
const financialGoalProps  = computed(() => ({
  currentAge: Number(basicInfoData.value?.age) || 30,
  retirementAge: Number(basicInfoData.value?.retirement_age) || 60,
  endAge: Number(basicInfoData.value?.end_age) || 90,
}))
const activeFormProps = computed(() =>
  currentStepId.value === 'financial-goals' ? financialGoalProps.value : {}
)
const submitButtonLabel   = computed(() => {
  if (isFinancialInfoStep.value) return '다음 단계로 이동'
  if (formSaving.value) return '저장 중...'
  return '작성 완료'
})

function clearFormWarning() {
  formWarning.code = null
  formWarning.canConfirmEmpty = false
  formWarning.confirmationLabel = null
  formWarning.errors = []
}

function captureActiveFormDraft() {
  const form = activeFormRef.value?.form
  if (!form || !STEP_FORM_MAP[currentStepId.value]) return
  formDrafts[currentStepId.value] = cloneFormState(form)
}

async function hydrateActiveForm(stepId) {
  const form = activeFormRef.value?.form
  if (!form || !STEP_FORM_MAP[stepId] || stepId === 'financial-info') return
  let stored = formDrafts[stepId]
  if (!stored && isRestoredSession.value) {
    restoringForm.value = true
    try {
      stored = await consultationStore.loadFormData(stepId)
      if (stored) formDrafts[stepId] = cloneFormState(stored)
    } catch {
      sessionError.value = '저장된 입력값 일부를 불러오지 못했습니다. 현재 단계의 값을 확인해주세요.'
    } finally {
      restoringForm.value = false
    }
  }
  if (stored) replaceFormState(form, stored)
}

async function restoreConsultationProcess(session) {
  isRestoredSession.value = true
  const completed = completedStepsForSession(session)
  completedStepIds.value = completed
  visitedStepIds.value = new Set(completed)
  let resumeStep = resolveResumeStep(session)
  if (resumeStep === 'income') {
    try {
      const liabilities = await consultationStore.loadFormData('liabilities')
      if (liabilities?.assets?.length) formDrafts.liabilities = cloneFormState(liabilities)
      else resumeStep = 'liabilities'
    } catch {
      resumeStep = 'liabilities'
    }
  }
  currentStepId.value = resumeStep
  const step = PROCESS_STEPS.find((item) => item.id === resumeStep)
  if (step && resumeStep !== 'intro') {
    messages.value.push({
      role: 'divider',
      stepId: resumeStep,
      label: `${step.label.replace('\n', ' ')} · 이어서 진행`,
    })
  }
  await nextTick()
  await hydrateActiveForm(resumeStep)
  scrollToStepStart(resumeStep)
  return resumeStep
}

function applyReviewSuggestion(item) {
  if (item.suggested_value == null || !activeFormRef.value?.form) return
  try {
    const result = applyErrorReviewSuggestion(
      currentStepId.value,
      activeFormRef.value.form,
      mapCurrentForm(currentStepId.value, activeFormRef.value.form),
      item,
    )
    if (result === 'stale') {
      sessionError.value = '검토 이후 입력값이 바뀌어 후보를 적용하지 않았습니다. 다시 검토해주세요.'
    }
  } catch {
    sessionError.value = '검토 제안을 현재 폼에 반영하지 못했습니다. 값을 직접 확인해주세요.'
  }
}

function cancelReviewSuggestion(item) {
  cancelErrorReviewSuggestion(item)
}

async function requestErrorReview(nextStepId = null) {
  if (
    reviewLoading.value
    || consultationStore.isAiRequestActive
    || !activeFormRef.value?.form
  ) return false
  reviewLoading.value = true
  try {
    const stored = await consultationStore.reviewForm(
      currentStepId.value,
      activeFormRef.value.form,
    )
    reviewLoading.value = false
    const bubble = toChatMessage(stored)
    const items = bubble.action?.type === 'error-review' ? bubble.action.items : []
    if (!items.length) return false
    bubble.action.saveAfterReview = true
    bubble.action.nextStepId = nextStepId
    await streamAiMessages([bubble], { typingDelay: 120 })
    return true
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    sessionError.value = 'AI 이상값 검토를 완료하지 못해 결정론적 검증 결과로 저장을 계속합니다.'
    return false
  } finally {
    reviewLoading.value = false
  }
}

async function finishErrorReview(msg) {
  if (hasPendingReviewItems(msg.action)) return
  const { saveAfterReview, nextStepId } = msg.action
  dismissAction(msg)
  if (saveAfterReview) await persistFormAndAdvance(nextStepId)
}

function executeAutoAction(action) {
  if (action.type === 'fill-field' && activeFormRef.value?.form) {
    activeFormRef.value.form[action.field] = action.value
  }
}

async function submitForm() {
  if (isInteractionBusy.value) return
  sessionError.value = ''
  const currentIdx = STEP_IDS.indexOf(currentStepId.value)
  const nextStepId = currentStepId.value === 'financial-goals'
    ? 'balance-sheet'
    : currentIdx >= 0 && currentIdx < STEP_IDS.length - 1 ? STEP_IDS[currentIdx + 1] : null
  if (!nextStepId) return

  if (isFinancialInfoStep.value) {
    completedStepIds.value = new Set(completedStepIds.value).add(currentStepId.value)
    dismissNextStepAction()
    navigateToStep(nextStepId)
    return
  }

  const validation = validateConsultationForm(
    currentStepId.value,
    activeFormRef.value?.form,
    { allowEmpty: emptyConfirmedStepIds.value.has(currentStepId.value) },
  )
  if (!validation.valid) {
    formWarning.code = validation.code
    formWarning.canConfirmEmpty = validation.canConfirmEmpty
    formWarning.confirmationLabel = validation.confirmationLabel ?? null
    formWarning.errors = validation.errors
    return
  }
  clearFormWarning()

  try {
    await continueSaveAfterOptionalReview(
      () => requestErrorReview(nextStepId),
      () => persistFormAndAdvance(nextStepId),
    )
  } catch (error) {
    if (error?.name !== 'AbortError') {
      sessionError.value = error?.message ?? '입력값을 저장하지 못했습니다.'
    }
  }
}

async function confirmEmptyForm() {
  if (isInteractionBusy.value) return
  if (currentStepId.value === 'financial-assets') {
    const currentIdx = STEP_IDS.indexOf(currentStepId.value)
    const nextStepId = currentIdx >= 0 && currentIdx < STEP_IDS.length - 1
      ? STEP_IDS[currentIdx + 1]
      : null
    if (!nextStepId) return
    const saved = await persistFormAndAdvance(nextStepId, { confirmNoFinancialAssets: true })
    if (saved) clearFormWarning()
    return
  }

  const confirmed = new Set(emptyConfirmedStepIds.value)
  confirmed.add(currentStepId.value)
  emptyConfirmedStepIds.value = confirmed
  clearFormWarning()
  await submitForm()
}

async function persistFormAndAdvance(nextStepId, { confirmNoFinancialAssets = false } = {}) {
  formSaving.value = true
  const savedStepId = currentStepId.value
  try {
    const response = confirmNoFinancialAssets
      ? await consultationStore.confirmNoFinancialAssets()
      : await consultationStore.saveFormData(savedStepId, activeFormRef.value?.form ?? {})
    if (savedStepId === 'basic-info') {
      basicInfoData.value = response?.data?.data ?? null
    }
  } catch (error) {
    sessionError.value = error?.response?.data?.error?.message
      ?? error?.message
      ?? '입력값을 저장하지 못했습니다. 내용을 확인한 뒤 다시 시도해주세요.'
    return false
  } finally {
    formSaving.value = false
  }

  try {
    captureActiveFormDraft()
  } catch {
    sessionError.value = '입력값은 서버에 저장됐지만 화면 임시본을 만들지 못했습니다. 다음 단계에서 이전 입력을 다시 확인해주세요.'
  }

  completedStepIds.value = new Set(completedStepIds.value).add(savedStepId)
  const skipped = new Set(skippedStepIds.value)
  skipped.delete(savedStepId)
  skippedStepIds.value = skipped
  dismissNextStepAction()

  if (savedStepId === 'financial-goals') {
    void reportStore.calculatePresentation(consultationStore.sessionId, { regenerateReports: true })
    void streamAiMessages([{
      text: '모든 정보 입력이 완료되었습니다. 고생 많으셨어요!\n고객님의 데이터를 바탕으로 재무 종합 진단과 은퇴 시뮬레이션 계산을 시작했습니다.',
      action: { type: 'next-step', stepId: nextStepId, label: '진단 결과 보기' },
    }])
    return true
  }

  void streamAiMessages([{
    text: confirmNoFinancialAssets
      ? '보유 금융자산이 없는 것으로 확인했습니다. 다음 단계로 이동할 준비가 완료되었습니다.'
      : '입력하신 정보가 저장되었습니다. 다음 단계로 이동할 준비가 완료되었습니다.',
    action: { type: 'next-step', stepId: nextStepId, label: '다음 단계로 이동 →' },
  }])
  return true
}

// ─── 프레젠테이션 슬라이드 ────────────────────────────────
const PRESENTATION_SLIDE_MAP = {
  'balance-sheet':         BalanceSheetSlide,
  'cash-flow':             CashFlowSlide,
  'financial-indicators':  FinancialIndicatorsSlide,
  'financial-goal-result': FinancialGoalSlide,
  'retirement-fund':       RetirementFundSlide,
  'retirement-readiness':  RetirementReadinessSlide,
  'retirement-simulation': RetirementSimulationSlide,
  'pension-schedule':      PensionScheduleSlide,
  'lifetime-cashflow':     LifetimeCashflowSlide,
  'required-savings':      RequiredSavingsSlide,
}

const activePresentationSlide = computed(
  () => PRESENTATION_SLIDE_MAP[currentStepId.value] ?? BalanceSheetSlide,
)
const activePresentationTitle = computed(() => {
  const step = PROCESS_STEPS.find((s) => s.id === currentStepId.value)
  return step?.label?.replace('\n', ' ') ?? '재무 진단'
})

// ─── Lifecycle ────────────────────────────────────────────
watch(currentStepId, async (stepId) => {
  if (stepId !== 'financial-goals' || basicInfoData.value) return
  try {
    basicInfoData.value = await consultationStore.loadBasicInfo()
  } catch {
    basicInfoData.value = null
  }
})

watch(isPresentationPhase, async (val) => {
  if (!val || !consultationStore.sessionId) return
  if (!reportStore.loading && !reportStore.simulationLoading && (!reportStore.reports || !reportStore.simulation)) {
    await reportStore.calculatePresentation(consultationStore.sessionId)
  }
  if (PRESENTATION_STEP_IDS.includes(currentStepId.value)) {
    void maybeSendPresentationEntryGuide(currentStepId.value)
  }
})

watch(
  () => (
    PRESENTATION_STEP_IDS.includes(currentStepId.value)
      ? reportStore.presentationReportVersion(currentStepId.value)
      : null
  ),
  (nextVersion, previousVersion) => {
    if (!nextVersion || !previousVersion || nextVersion === previousVersion) return
    void maybeSendPresentationEntryGuide(currentStepId.value)
  },
)

async function restoreMountedConsultation() {
  sessionLoading.value = true
  sessionError.value = ''
  try {
    const session = await consultationStore.loadActiveSession()
    await consultationStore.loadMessages()
    const hasStoredMessages = consultationStore.chatMessages.length > 0
    if (hasStoredMessages) restoreStoredMessages()
    const resumeStep = await restoreConsultationProcess(session)
    if (!hasStoredMessages) {
      if (resumeStep === 'intro') showInitialMessages()
      else void streamAiMessages([{ text: '저장된 상담 내용을 불러왔습니다. 마지막으로 완료하지 못한 단계부터 이어서 진행하겠습니다.' }])
    }
  } catch {
    sessionError.value = '상담 진행 상태를 복원하지 못했습니다. 상담 이어하기를 다시 시도해주세요.'
  } finally {
    sessionLoading.value = false
  }
}

onMounted(() => {
  window.addEventListener('mousemove', onResizeMove)
  window.addEventListener('mouseup', stopResize)
  if (hasActiveSession.value && messages.value.length === 0) {
    void restoreMountedConsultation()
  }
})

onBeforeUnmount(() => {
  cancelMessageAnimations()
  consultationStore.cancelActiveStream()
  window.removeEventListener('mousemove', onResizeMove)
  window.removeEventListener('mouseup', stopResize)
  document.body.style.cursor = ''
})
</script>

<template>
  <section class="ai-page">
    <div class="ai-layout">
      <!-- ── 좌측: 프로세스 사이드바 ── -->
      <ProcessSidebar
        :current-step-id="currentStepId"
        :visited-step-ids="visitedStepIds"
        :completed-step-ids="completedStepIds"
        :skipped-step-ids="skippedStepIds"
        :disabled="isInteractionBusy"
        @step-click="handleStepClick"
      />

      <!-- ── 우측: 메인 영역 ── -->
      <div
        class="main-area"
        ref="mainAreaRef"
        :class="{ 'presentation-inline-mode': isPresentationPhase }"
      >
        <article class="chat-panel" :style="isIntroPhase ? { flex: '1' } : { flexBasis: `${isPresentationPhase ? rightWidth : leftWidth}%` }">
            <div class="chat-list" ref="chatListRef" role="log" aria-live="polite" aria-relevant="additions text">
              <div v-if="!hasActiveSession" class="session-start">
                <div class="session-start-card">
                  <p class="session-eyebrow">AI 재무상담</p>
                  <h1>상담을 어떻게 시작할까요?</h1>
                  <p class="session-desc">새 상담은 새로운 상담 세션을 만들고, 기존 상담 이어하기는 저장된 상담 목록에서 최신 상담을 불러옵니다.</p>
                  <div class="session-actions">
                    <button class="primary-button" type="button" :disabled="sessionLoading" @click="startNewConsultation">
                      {{ sessionLoading ? '준비 중...' : '새 상담 시작' }}
                    </button>
                    <button class="secondary-button" type="button" :disabled="sessionLoading" @click="continueConsultation">
                      기존 상담 이어하기
                    </button>
                  </div>
                  <p v-if="sessionError" class="session-error" role="alert">{{ sessionError }}</p>
                </div>
              </div>
              <template v-for="(msg, i) in messages" :key="i">
                <div v-if="msg.role === 'divider'" class="step-divider" :data-step-id="msg.stepId">
                  <span class="divider-line"></span>
                  <span class="divider-label">{{ msg.label }}</span>
                  <span class="divider-line"></span>
                </div>
                <article v-else class="bubble" :class="msg.role === 'ai' ? 'ai-bubble' : 'user-bubble'">
                  <!-- eslint-disable-next-line vue/no-v-html -->
                  <div
                    v-if="msg.role === 'ai'"
                    class="message-markdown"
                    :class="{ streaming: msg.streaming }"
                    v-html="renderChatMarkdown(msg.displayText ?? msg.text)"
                  ></div>
                  <p v-else>{{ msg.text }}</p>
                  <template v-if="msg.action && !msg.streaming">
                    <div v-if="msg.action.type === 'consent'" class="consent-card">
                      <p class="consent-title">개인정보 수집·이용 동의</p>
                      <p class="consent-detail">동의를 거부하실 수 있으나 재무 진단 서비스 이용이 불가합니다.<br><br>· 수집 항목: 연령, 직업, 가족관계, 투자 성향, 투자 가치관, 자산 현황, 소비 지출 내역<br>· 이용 목적: AI 재무 진단 답변 생성, 통계 자료 제공<br><strong>· 보유 기간: 서비스 회원 탈퇴 시까지</strong></p>
                      <button class="action-btn" type="button" @click="handleConsent">개인정보 수집 및 이용에 동의합니다.</button>
                    </div>
                    <div v-else-if="msg.action.type === 'form-update'" class="update-card">
                      <p class="update-desc">아래 변경을 항목별로 확인해주세요.</p>
                      <div
                        v-for="change in msg.action.changes"
                        :key="change.change_id"
                        class="update-change"
                      >
                        <div class="update-change-copy">
                          <strong>{{ change.label ?? `${change.section}.${change.path}` }}</strong>
                          <span v-if="change.current_value !== undefined">현재 값: {{ formatActionValue(change.current_value, change.unit) }}</span>
                          <span>제안 값: {{ formatActionValue(change.value, change.unit) }}</span>
                          <small v-if="change.reason">{{ change.reason }}</small>
                          <small v-if="change.assumption_level">{{ change.assumption_level }} · {{ change.source?.title ?? '승인 정책' }} · {{ change.source?.reference_period ?? '' }}<template v-if="change.policy_version"> · {{ change.policy_version }}</template></small>
                        </div>
                        <div v-if="change.decision_status === 'pending'" class="update-actions">
                          <button
                            class="action-btn action-btn--secondary"
                            type="button"
                            @click="rejectFormChange(msg, change)"
                          >거절</button>
                          <button
                            class="action-btn"
                            type="button"
                            :disabled="!canApproveFormChange(msg.action)"
                            @click="approveFormChange(msg, change)"
                          >승인</button>
                          <small v-if="!canApproveFormChange(msg.action)" class="update-unavailable">
                            {{ msg.action.step_id }} 입력 단계에서 승인할 수 있습니다.
                          </small>
                        </div>
                        <span v-else class="update-decision" :class="`update-decision--${change.decision_status}`">
                          {{ decisionStatusLabel(change.decision_status) }}
                        </span>
                      </div>
                    </div>
                    <div v-else-if="msg.action.type === 'error-review'" class="update-card">
                      <p class="update-desc">의심되는 값만 확인해 주세요. 제안은 현재 draft에만 바로 반영됩니다.</p>
                      <div v-for="item in msg.action.items" :key="`${item.section}.${item.path}.${item.item_id ?? item.client_item_id ?? ''}`" class="update-change">
                        <div class="update-change-copy">
                          <strong>{{ `${item.section}.${item.path}` }}</strong>
                          <span>현재 값: {{ formatActionValue(item.current_value, item.unit) }}</span>
                          <small>{{ item.reason }}</small>
                          <span v-if="item.suggested_value != null">수정 후보: {{ formatActionValue(item.suggested_value, item.unit) }}</span>
                        </div>
                        <div v-if="item.decision === 'pending'" class="update-actions">
                          <button class="action-btn action-btn--secondary" type="button" @click="cancelReviewSuggestion(item)">현재 값 유지</button>
                          <button v-if="item.suggested_value != null" class="action-btn" type="button" @click="applyReviewSuggestion(item)">후보 적용</button>
                        </div>
                        <span v-else class="update-decision">{{ item.decision === 'applied' ? '적용됨' : '유지함' }}</span>
                      </div>
                      <button class="action-btn" type="button" :disabled="hasPendingReviewItems(msg.action)" @click="finishErrorReview(msg)">
                        {{ hasPendingReviewItems(msg.action) ? '모든 항목을 결정해주세요' : '검토 완료하고 저장' }}
                      </button>
                    </div>
                    <button v-else class="action-btn" type="button" @click="executeAction(msg.action)">
                      {{ msg.action.label }}
                    </button>
                  </template>
                </article>
              </template>
              <article v-if="isTyping" class="bubble ai-bubble">
                <strong>AI</strong>
                <p class="typing-dots"><span></span><span></span><span></span></p>
              </article>
              <div ref="chatSpacerRef" class="chat-spacer"></div>
            </div>
            <div v-if="reviewLoading" class="chat-operation-status" role="status" aria-live="polite">
              <span class="chat-operation-dot" aria-hidden="true"></span>
              <span>입력값의 이상 여부를 검토하고 있습니다.</span>
            </div>
            <p v-if="hasActiveSession && sessionError" class="chat-error" role="alert">{{ sessionError }}</p>
            <div v-if="hasActiveSession" class="composer">
              <textarea
                ref="chatInputRef"
                v-model="draftMessage"
                class="chat-input"
                rows="1"
                :placeholder="isPresentationPhase ? 'AI에게 질문해보세요.' : 'AI에게 질문해보세요. 예: 은퇴 계획 세워줘'"
                :disabled="isInteractionBusy"
                @input="resizeChatInput"
                @keydown="handleKeydown"
              ></textarea>
              <button class="primary-button" type="button" :disabled="isInteractionBusy" @click="sendMessage">전송</button>
            </div>
          </article>

          <template v-if="!isIntroPhase">
            <button class="splitter" type="button" aria-label="영역 너비 조절" @mousedown="startResize">
              <span></span>
            </button>

            <aside class="input-panel" :style="{ flexBasis: `${isPresentationPhase ? leftWidth : rightWidth}%` }">

              <!-- ── 재무 진단 인라인 뷰 ── -->
              <template v-if="isPresentationPhase">
                <div class="panel-header">
                  <h2>재무 진단</h2>
                </div>
                <div class="slide-body">
                  <div class="slide-doc-header">
                    <span class="doc-ref">PRESENTATION · {{ currentStepId.toUpperCase().replace(/-/g, '') }}</span>
                    <h3 class="doc-title">{{ activePresentationTitle }}</h3>
                    <div class="doc-rule"></div>
                  </div>
                  <div class="slide-component-wrap">
                    <component :is="activePresentationSlide" />
                  </div>
                </div>
              </template>

              <!-- ── 정보 수집 폼 ── -->
              <template v-else>
                <div class="panel-header">
                  <h2>데이터 입력</h2>
                </div>
                <div class="form-body">
                  <div class="doc-header">
                    <h3 class="doc-title">{{ activeFormTitle }}</h3>
                    <div class="doc-rule"></div>
                  </div>
                  <div v-if="formWarning.errors.length" class="form-warning" role="alert">
                    <strong>입력 내용을 확인해주세요.</strong>
                    <ul>
                      <li v-for="error in formWarning.errors" :key="error">{{ error }}</li>
                    </ul>
                    <button
                      v-if="formWarning.canConfirmEmpty"
                      class="form-warning-confirm"
                      type="button"
                      :disabled="isInteractionBusy"
                      @click="confirmEmptyForm"
                    >{{ formWarning.confirmationLabel ?? '해당 없음으로 진행' }}</button>
                  </div>
                  <component :is="activeFormComponent" ref="activeFormRef" v-bind="activeFormProps" />
                </div>
                <div class="form-footer">
                  <button class="submit-form-btn" type="button" :disabled="isInteractionBusy || restoringForm" @click="submitForm">
                    {{ reviewLoading ? '이상값 검토 중...' : restoringForm ? '불러오는 중...' : submitButtonLabel }}
                  </button>
                </div>
              </template>

            </aside>
          </template>

      </div>
    </div>

    <StepSkipWarningModal
      :show="warningModal.show"
      :target-label="warningModal.targetLabel"
      @close="closeWarning"
      @confirm="confirmStepJump"
    />
  </section>
</template>

<style scoped src="./AiChatView.css">
</style>
