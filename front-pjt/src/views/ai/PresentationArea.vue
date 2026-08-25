<script setup>
import { computed } from 'vue'
import BalanceSheetSlide from '@/components/presentation/BalanceSheetSlide.vue'
import CashFlowSlide from '@/components/presentation/CashFlowSlide.vue'
import FinancialIndicatorsSlide from '@/components/presentation/FinancialIndicatorsSlide.vue'
import FinancialGoalSlide from '@/components/presentation/FinancialGoalSlide.vue'
import RetirementFundSlide from '@/components/presentation/RetirementFundSlide.vue'
import RetirementReadinessSlide from '@/components/presentation/RetirementReadinessSlide.vue'
import PensionScheduleSlide from '@/components/presentation/PensionScheduleSlide.vue'
import LifetimeCashflowSlide from '@/components/presentation/LifetimeCashflowSlide.vue'
import RequiredSavingsSlide from '@/components/presentation/RequiredSavingsSlide.vue'

const props = defineProps({
  currentStepId: { type: String, required: true },
})

const SLIDE_MAP = {
  'balance-sheet':        BalanceSheetSlide,
  'cash-flow':            CashFlowSlide,
  'financial-indicators': FinancialIndicatorsSlide,
  'financial-goal-result':FinancialGoalSlide,
  'retirement-fund':      RetirementFundSlide,
  'retirement-readiness': RetirementReadinessSlide,
  'pension-schedule':     PensionScheduleSlide,
  'lifetime-cashflow':    LifetimeCashflowSlide,
  'required-savings':     RequiredSavingsSlide,
}

const activeSlide = computed(() => SLIDE_MAP[props.currentStepId] ?? BalanceSheetSlide)
</script>

<template>
  <div class="pres-outer">
    <div class="pres-box">
      <component :is="activeSlide" />
    </div>
  </div>
</template>

<style scoped>
.pres-outer {
  flex: 1;
  background: #000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.pres-box {
  aspect-ratio: 21 / 9;
  align-self: stretch;
  max-width: 100%;
  background: #fff;
  overflow: hidden;
}
</style>
