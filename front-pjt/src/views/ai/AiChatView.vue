<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useAiStore } from '@/stores/ai'
import { applyCanonicalChanges } from '@/utils/formChanges'
import { PROCESS_STEPS, STEP_IDS } from './data/processSteps.js'
import { initialMessages, STEP_GUIDE_MESSAGES } from './data/stepGuideMessages.js'
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
import PensionScheduleSlide from '@/components/presentation/PensionScheduleSlide.vue'
import LifetimeCashflowSlide from '@/components/presentation/LifetimeCashflowSlide.vue'
import RequiredSavingsSlide from '@/components/presentation/RequiredSavingsSlide.vue'
import ProcessSidebar from './ProcessSidebar.vue'
import StepSkipWarningModal from './StepSkipWarningModal.vue'

const aiStore = useAiStore()
const hasActiveSession = computed(() => !!aiStore.sessionId)

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

function navigateToStep(stepId) {
  if (currentStepId.value === stepId) return
  dismissNextStepAction()
  const next = new Set(visitedStepIds.value)
  next.add(currentStepId.value)
  visitedStepIds.value = next
  currentStepId.value  = stepId

  const alreadyVisited = stepId === 'intro' || messages.value.some((m) => m.role === 'divider' && m.stepId === stepId)
  if (alreadyVisited) {
    scrollToStepStart(stepId, true)
    return
  }

  const step = PROCESS_STEPS.find((s) => s.id === stepId)
  if (step) {
    messages.value.push({ role: 'divider', stepId, label: step.label?.replace('\n', ' ') ?? stepId })
  }

  const guide = STEP_GUIDE_MESSAGES[stepId]
  if (guide) {
    const guideMsgs   = Array.isArray(guide) ? guide : [guide]
    scrollToStepStart(stepId, true)
    const consentIdx  = guideMsgs.findIndex((m) => m.action?.type === 'consent')
    const preConsent  = consentIdx >= 0 ? guideMsgs.slice(0, consentIdx + 1) : guideMsgs
    const postConsent = consentIdx >= 0 ? guideMsgs.slice(consentIdx + 1) : []
    streamAiMessages(preConsent)
    if (postConsent.length > 0) pendingConsentMessages.value = postConsent
  } else {
    scrollToStepStart(stepId, true)
  }
}

const warningModal = reactive({ show: false, targetStepId: null, targetLabel: '' })

function handleStepClick(step) {
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
  if (warningModal.targetStepId) navigateToStep(warningModal.targetStepId)
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
const isStreamingResponse    = ref(false)
const sessionLoading         = ref(false)
const sessionError           = ref('')

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

function streamAiMessages(bubbles, { typingDelay = 300, onDone } = {}) {
  if (!bubbles.length) {
    isTyping.value = false
    onDone?.()
    return
  }
  const [bubble, ...rest] = bubbles
  isTyping.value = true
  setTimeout(() => {
    isTyping.value = false
    messages.value.push({
      role: 'ai',
      text: bubble.text,
      displayText: '',
      streaming: true,
      ...(bubble.action ? { action: bubble.action } : {}),
    })
    const msg   = messages.value[messages.value.length - 1]
    let charIdx = 0
    function typeNext() {
      if (charIdx >= bubble.text.length) {
        msg.streaming = false
        if (bubble.autoAction) executeAutoAction(bubble.autoAction)
        streamAiMessages(rest, { typingDelay, onDone })
        return
      }
      const char = bubble.text[charIdx++]
      msg.displayText = bubble.text.slice(0, charIdx)
      scrollToBottom(false, true)
      let delay = 18 + Math.random() * 22
      if ('.!?'.includes(char))  delay += 180 + Math.random() * 120
      else if (char === ',')     delay += 80  + Math.random() * 60
      else if (char === '\n')    delay += 200 + Math.random() * 100
      setTimeout(typeNext, delay)
    }
    typeNext()
  }, typingDelay)
}

function resetConversationState() {
  currentStepId.value = 'intro'
  visitedStepIds.value = new Set()
  completedStepIds.value = new Set()
  messages.value = []
  pendingConsentMessages.value = []
  draftMessage.value = ''
  resizeChatInput()
  basicInfoData.value = null
  formWarningShown.value = false
  isTyping.value = false
  isStreamingResponse.value = false
  leftWidth.value = 71.4
  rightWidth.value = 28.6
  aiStore.presentationData = null
}

function showInitialMessages() {
  messages.value = []
  streamAiMessages(initialMessages)
}

function restoreStoredMessages() {
  messages.value = aiStore.chatMessages.map((msg) => ({
    role: msg.role === 'assistant' ? 'ai' : msg.role,
    text: msg.content,
  }))
}

async function startNewConsultation() {
  sessionError.value = ''
  sessionLoading.value = true
  try {
    resetConversationState()
    await aiStore.initSession()
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
    const sessionId = await aiStore.continueLatestSession()
    if (!sessionId) {
      sessionError.value = '이어갈 기존 상담이 없습니다. 새 상담을 시작해주세요.'
      return
    }

    await aiStore.loadMessages()
    if (aiStore.chatMessages.length > 0) {
      restoreStoredMessages()
    } else {
      showInitialMessages()
    }
  } catch {
    sessionError.value = '기존 상담을 불러오지 못했습니다. 잠시 후 다시 시도해주세요.'
  } finally {
    sessionLoading.value = false
  }
}

async function sendMessage() {
  const text = draftMessage.value.trim()
  if (!text || isTyping.value || isStreamingResponse.value || !hasActiveSession.value) return
  messages.value.push({ role: 'user', text })
  draftMessage.value = ''
  resizeChatInput()
  scrollToBottom()
  isTyping.value = true
  isStreamingResponse.value = true
  let streamedMessage = null

  function ensureStreamedMessage() {
    if (streamedMessage) return streamedMessage
    isTyping.value = false
    messages.value.push({
      role: 'ai',
      text: '',
      displayText: '',
      streaming: true,
    })
    streamedMessage = messages.value[messages.value.length - 1]
    return streamedMessage
  }

  try {
    for await (const { event, data } of aiStore.streamMessage(text, {
      stepId: currentStepId.value,
      form: activeFormRef.value?.form ?? null,
    })) {
      if (event === 'start') continue

      if (event === 'delta') {
        if (typeof data.content !== 'string') {
          throw new Error('AI 스트림 응답 형식이 올바르지 않습니다.')
        }
        const message = ensureStreamedMessage()
        message.text += data.content
        message.displayText = message.text
        scrollToBottom(false, true)
        continue
      }

      if (event === 'error') {
        throw new Error(data.message ?? 'AI 응답 생성에 실패했습니다.')
      }

      if (event === 'done') {
        const message = ensureStreamedMessage()
        const responseText = data.output?.response
        if (typeof responseText === 'string' && responseText) {
          message.text = responseText
          message.displayText = responseText
        }
        const action = data.output?.action
        if (action?.type === 'form_change' && Array.isArray(action.changes) && action.changes.length) {
          message.action = {
            ...action,
            type: 'form-update',
            messageId: data.message_id,
            fields: action.changes
              .map((change) => change.label ?? `${change.section}.${change.path}`)
              .join(', '),
          }
        }
        message.streaming = false
        scrollToBottom(false, true)
        return
      }
    }

    throw new Error('AI 스트림이 완료 이벤트 없이 종료되었습니다.')
  } catch (error) {
    if (error?.name === 'AbortError') return
    if (streamedMessage) streamedMessage.streaming = false
    messages.value.push({
      role: 'ai',
      text: error?.message ?? '메시지 전송 중 오류가 발생했습니다. 다시 시도해주세요.',
    })
    scrollToBottom(false, true)
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

function executeAction(action) {
  if (action.type === 'next-step') navigateToStep(action.stepId)
}

function dismissAction(msg) {
  const idx = messages.value.indexOf(msg)
  if (idx !== -1) messages.value[idx] = { ...messages.value[idx], action: undefined }
}

async function approveFormUpdate(msg) {
  const action = msg.action
  if (action.target === 'draft_form') {
    if (action.step_id !== currentStepId.value || !activeFormRef.value?.form) return
    try {
      applyCanonicalChanges(action.step_id, activeFormRef.value.form, action.changes)
      dismissAction(msg)
    } catch {
      sessionError.value = '현재 폼에서 AI 제안 대상을 찾지 못했습니다. 다시 요청해주세요.'
    }
    return
  }
  try {
    await aiStore.approveAction(action.messageId)
    dismissAction(msg)
  } catch {
    streamAiMessages([{ text: 'AI 입력 제안을 저장하지 못했습니다. 다시 시도해주세요.' }], { typingDelay: 0 })
  }
}

async function rejectFormUpdate(msg) {
  const action = msg.action
  try {
    if (action.target !== 'draft_form') {
      await aiStore.rejectAction(action.messageId)
    }
    dismissAction(msg)
  } catch {
    streamAiMessages([{ text: 'AI 입력 제안을 거절 처리하지 못했습니다. 다시 시도해주세요.' }], { typingDelay: 0 })
  }
}

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
  streamAiMessages(msgs)
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
const formWarningShown    = ref(false)
const formSaving          = ref(false)
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
  if (formWarningShown.value) return '수정 완료'
  return '작성 완료'
})

function executeAutoAction(action) {
  if (action.type === 'fill-field' && activeFormRef.value?.form) {
    activeFormRef.value.form[action.field] = action.value
  }
}

async function submitForm() {
  const currentIdx = STEP_IDS.indexOf(currentStepId.value)
  const nextStepId = currentIdx >= 0 && currentIdx < STEP_IDS.length - 1 ? STEP_IDS[currentIdx + 1] : null
  if (!nextStepId) return

  if (isFinancialInfoStep.value) {
    completedStepIds.value.add(currentStepId.value)
    dismissNextStepAction()
    navigateToStep(nextStepId)
    return
  }

  const earnings = activeFormRef.value?.form?.earnings
  if (currentStepId.value === 'basic-info' && Number(earnings) === 10 && !formWarningShown.value) {
    completedStepIds.value.add(currentStepId.value)
    dismissNextStepAction()
    streamAiMessages([{
      text: `소득이 월 세후 ${earnings}만원으로 낮게 작성된 것 같아요. 잘못 입력하셨다면 수정해주시고, 맞다면 아래의 버튼을 눌러주세요.`,
      action: { type: 'next-step', stepId: nextStepId, label: '맞습니다, 다음 단계로 이동 →' },
    }], { onDone: () => { formWarningShown.value = true } })
    return
  }
  formWarningShown.value = false

  formSaving.value = true
  try {
    const response = await aiStore.saveFormData(currentStepId.value, activeFormRef.value?.form ?? {})
    if (currentStepId.value === 'basic-info') {
      basicInfoData.value = response?.data?.data ?? null
    }
  } finally {
    formSaving.value = false
  }

  completedStepIds.value.add(currentStepId.value)
  dismissNextStepAction()

  if (currentStepId.value === 'financial-goals') {
    streamAiMessages([{
      text: '모든 정보 입력이 완료되었습니다. 고생 많으셨어요!\n아래 버튼을 누르시면 제가 고객님의 데이터를 바탕으로 본격적인 재무 종합 진단을 시작하겠습니다.',
      action: { type: 'next-step', stepId: nextStepId, label: '재무 진단 시작' },
    }])
    return
  }

  streamAiMessages([{
    text: '입력하신 정보가 저장되었습니다. 다음 단계로 이동할 준비가 완료되었습니다.',
    action: { type: 'next-step', stepId: nextStepId, label: '다음 단계로 이동 →' },
  }])
}

// ─── 프레젠테이션 슬라이드 ────────────────────────────────
const PRESENTATION_SLIDE_MAP = {
  'balance-sheet':         BalanceSheetSlide,
  'cash-flow':             CashFlowSlide,
  'financial-indicators':  FinancialIndicatorsSlide,
  'financial-goal-result': FinancialGoalSlide,
  'retirement-fund':       RetirementFundSlide,
  'retirement-readiness':  RetirementReadinessSlide,
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
    basicInfoData.value = await aiStore.loadBasicInfo()
  } catch {
    basicInfoData.value = null
  }
})

watch(isPresentationPhase, (val) => {
  if (val && !aiStore.presentationData) aiStore.fetchPresentationData()
})

onMounted(() => {
  window.addEventListener('mousemove', onResizeMove)
  window.addEventListener('mouseup', stopResize)
  if (hasActiveSession.value && messages.value.length === 0) {
    showInitialMessages()
  }
})

onBeforeUnmount(() => {
  aiStore.cancelActiveStream()
  window.removeEventListener('mousemove', onResizeMove)
  window.removeEventListener('mouseup', stopResize)
  document.body.style.cursor = ''
})
</script>

<template>
  <section class="ai-page">
    <div class="ai-layout">
      <!-- ── 좌측: 프로세스 사이드바 ── -->
      <ProcessSidebar :current-step-id="currentStepId" :visited-step-ids="visitedStepIds" @step-click="handleStepClick" />

      <!-- ── 우측: 메인 영역 ── -->
      <div
        class="main-area"
        ref="mainAreaRef"
        :class="{ 'presentation-inline-mode': isPresentationPhase }"
      >
        <article class="chat-panel" :style="isIntroPhase ? { flex: '1' } : { flexBasis: `${isPresentationPhase ? rightWidth : leftWidth}%` }">
            <div class="chat-list" ref="chatListRef">
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
                  <p v-if="sessionError" class="session-error">{{ sessionError }}</p>
                </div>
              </div>
              <template v-else v-for="(msg, i) in messages" :key="i">
                <div v-if="msg.role === 'divider'" class="step-divider" :data-step-id="msg.stepId">
                  <span class="divider-line"></span>
                  <span class="divider-label">{{ msg.label }}</span>
                  <span class="divider-line"></span>
                </div>
                <article v-else class="bubble" :class="msg.role === 'ai' ? 'ai-bubble' : 'user-bubble'">
                  <p :class="{ streaming: msg.streaming }">{{ msg.displayText ?? msg.text }}</p>
                  <template v-if="msg.action && !msg.streaming">
                    <div v-if="msg.action.type === 'consent'" class="consent-card">
                      <p class="consent-title">개인정보 수집·이용 동의</p>
                      <p class="consent-detail">동의를 거부하실 수 있으나 재무 진단 서비스 이용이 불가합니다.<br><br>· 수집 항목: 연령, 직업, 가족관계, 투자 성향, 투자 가치관, 자산 현황, 소비 지출 내역<br>· 이용 목적: AI 재무 진단 답변 생성, 통계 자료 제공<br><strong>· 보유 기간: 서비스 회원 탈퇴 시까지</strong></p>
                      <button class="action-btn" type="button" @click="handleConsent">개인정보 수집 및 이용에 동의합니다.</button>
                    </div>
                    <div v-else-if="msg.action.type === 'form-update'" class="update-card">
                      <p class="update-desc"><strong>{{ msg.action.fields }}</strong>에 대해 수정을 제안합니다. 승인하시겠습니까?</p>
                      <div class="update-actions">
                        <button class="action-btn action-btn--secondary" type="button" @click="rejectFormUpdate(msg)">거절</button>
                        <button class="action-btn" type="button" @click="approveFormUpdate(msg)">승인</button>
                      </div>
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
            <div v-if="hasActiveSession" class="composer">
              <textarea
                ref="chatInputRef"
                v-model="draftMessage"
                class="chat-input"
                rows="1"
                :placeholder="isPresentationPhase ? 'AI에게 질문해보세요.' : 'AI에게 질문해보세요. 예: 은퇴 계획 세워줘'"
                :disabled="isTyping || isStreamingResponse"
                @input="resizeChatInput"
                @keydown="handleKeydown"
              ></textarea>
              <button class="primary-button" type="button" :disabled="isTyping || isStreamingResponse" @click="sendMessage">전송</button>
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
                  <component :is="activeFormComponent" ref="activeFormRef" v-bind="activeFormProps" />
                </div>
                <div class="form-footer">
                  <button class="submit-form-btn" :class="{ 'submit-form-btn--warned': formWarningShown }" type="button" :disabled="formSaving" @click="submitForm">{{ submitButtonLabel }}</button>
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
