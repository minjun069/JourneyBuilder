<script setup>
defineProps({
  show:        { type: Boolean, required: true },
  targetLabel: { type: String,  required: true },
})

defineEmits(['close', 'confirm'])
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="modal-backdrop" @click.self="$emit('close')">
      <div class="warning-modal" role="dialog" aria-modal="true" aria-labelledby="step-skip-title">
        <div class="modal-icon">⚠</div>
        <h3 id="step-skip-title" class="modal-title">미완료 단계 건너뛰기</h3>
        <p class="modal-desc">
          <strong>{{ targetLabel }}</strong> 단계로 이동하면, 아직 처리되지 않은 이전 단계들은 건너뜀으로 표시됩니다.
        </p>
        <p class="modal-sub">일부 진단 결과가 부정확할 수 있습니다. 계속 진행하시겠습니까?</p>
        <div class="modal-actions">
          <button class="modal-btn cancel" @click="$emit('close')">취소</button>
          <button class="modal-btn confirm" @click="$emit('confirm')">건너뛰고 이동</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(15, 20, 40, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}

.warning-modal {
  background: #fff;
  border-radius: 20px;
  padding: 32px 28px;
  max-width: 400px;
  width: 90%;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.18);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.modal-icon { font-size: 28px; line-height: 1; }

.modal-title {
  font-size: 18px;
  font-weight: 800;
  color: #1a1f3c;
}

.modal-desc {
  font-size: 14px;
  color: #31374a;
  line-height: 1.55;
}

.modal-sub {
  font-size: 12px;
  color: #6b7280;
}

.modal-actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  margin-top: 8px;
}

.modal-btn {
  border-radius: 10px;
  padding: 10px 16px;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  transition: transform 0.12s;
  border: none;
}

.modal-btn:hover { transform: translateY(-1px); }

.modal-btn.cancel {
  background: #f3f4f6;
  color: #4b5563;
}

.modal-btn.confirm {
  background: linear-gradient(135deg, #5e5ce6, #7b7cf5);
  color: #fff;
  box-shadow: 0 6px 14px rgba(94, 92, 230, 0.28);
}
</style>
