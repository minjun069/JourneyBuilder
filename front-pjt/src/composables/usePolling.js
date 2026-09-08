import { onMounted, onUnmounted } from 'vue'

// FR-09: PB 화면 공통 주기 갱신. 집계가 요청 시 계산이므로 재조회 = 실시간 반영.
// 탭이 숨겨져 있는 동안은 건너뛰고, 탭 복귀 시 즉시 한 번 갱신한다.
export function usePolling(loadFn, intervalMs = 60_000) {
  let timer = null

  function tick() {
    if (!document.hidden) loadFn()
  }

  function onVisibility() {
    if (!document.hidden) loadFn()
  }

  onMounted(() => {
    timer = setInterval(tick, intervalMs)
    document.addEventListener('visibilitychange', onVisibility)
  })

  onUnmounted(() => {
    clearInterval(timer)
    document.removeEventListener('visibilitychange', onVisibility)
  })
}
