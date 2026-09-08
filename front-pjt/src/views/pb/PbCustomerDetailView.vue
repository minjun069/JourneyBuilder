<template>
  <div class="customer-detail">
    <button type="button" class="btn-back" @click="router.back()">← 고객 리스트</button>

    <p v-if="loading" class="placeholder">불러오는 중…</p>
    <p v-else-if="error" class="placeholder error">{{ error }}</p>

    <template v-else-if="detail">
      <header class="profile-head">
        <div>
          <h2 class="name">{{ detail.profile.name }}</h2>
          <p class="sub">
            {{ detail.basic_info?.age ?? '—' }}세 ·
            {{ detail.basic_info?.job ?? '직업 미입력' }} ·
            니즈: {{ detail.basic_info?.need ?? '—' }}
          </p>
          <div class="tags" v-if="detail.basic_info?.tendency?.length">
            <span v-for="tag in detail.basic_info.tendency" :key="tag" class="tag">{{ tag }}</span>
          </div>
        </div>
        <span class="badge">{{ detail.session?.status ?? '세션 없음' }}</span>
      </header>

      <p class="placeholder">
        요약(FR-04)·데이터(FR-05) 상세 화면은 Phase 4에서 구현합니다.
        (순자산 {{ formatEok(detail.derived?.net_worth) }}, 위험지표
        {{ detail.derived?.risk_count ?? '—' }}개)
      </p>
    </template>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getCustomer } from '@/api/admin'
import { formatEok } from '@/utils/money'

const route = useRoute()
const router = useRouter()

const detail = ref(null)
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    const body = await getCustomer(route.params.id)
    detail.value = body.data
  } catch (err) {
    error.value = err.response?.status === 404
      ? '고객을 찾을 수 없습니다.'
      : '고객 정보를 불러오지 못했습니다.'
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.btn-back {
  border: 0;
  background: transparent;
  color: #5e5ce6;
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
  padding: 0;
  margin-bottom: 16px;
}

.profile-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  background: #fff;
  border: 1px solid #ebebf5;
  border-radius: 14px;
  padding: 22px 26px;
  margin-bottom: 16px;
}

.name { font-size: 22px; font-weight: 700; color: #1a1a2e; margin: 0 0 6px; }
.sub { font-size: 14px; color: #666; margin: 0 0 10px; }

.tags { display: flex; gap: 6px; flex-wrap: wrap; }
.tag {
  font-size: 12px;
  color: #5e5ce6;
  background: #eeeefc;
  border-radius: 999px;
  padding: 3px 10px;
}

.badge {
  font-size: 13px;
  font-weight: 600;
  color: #5e5ce6;
  background: #f0f0fd;
  border-radius: 999px;
  padding: 5px 14px;
}

.placeholder { color: #888; font-size: 14px; }
.placeholder.error { color: #d43a3a; }
</style>
