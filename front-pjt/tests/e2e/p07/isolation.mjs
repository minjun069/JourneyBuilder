import { randomUUID } from 'node:crypto'

function safeToken(value) {
  const token = value.toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 24)
  if (token.length < 8) throw new Error('격리 token은 영숫자 8자 이상이어야 합니다.')
  return token
}

export function createIsolation(seed = randomUUID()) {
  const token = safeToken(seed)
  const runId = `p07-${token}`
  const isolation = {
    runId,
    database: {
      schema: `p07_${token}`,
      ownerTag: runId,
    },
    redis: {
      namespace: `p07:${token}:`,
    },
    browser: {
      contextId: runId,
      storagePrefix: `p07:${token}:`,
    },
    identity: {
      userEmail: `p07+${token}@example.test`,
      sessionMarker: runId,
      threadId: `thread-${runId}`,
      requestPrefix: `ai-req-${runId}`,
      idempotencyPrefix: `idem-${runId}`,
    },
    cleanup: [
      { resource: 'browser', operation: 'close-context', target: runId },
      { resource: 'redis', operation: 'scan-delete-prefix', target: `p07:${token}:` },
      { resource: 'database', operation: 'drop-owned-schema', target: `p07_${token}`, ownerTag: runId },
    ],
    parallelSafe: true,
  }
  return validateIsolation(isolation)
}

export function validateIsolation(isolation) {
  if (!/^p07_[a-z0-9]{8,24}$/.test(isolation.database.schema)) {
    throw new Error('P07 전용 DB schema가 필요합니다.')
  }
  if (!/^p07:[a-z0-9]{8,24}:$/.test(isolation.redis.namespace)) {
    throw new Error('P07 전용 Redis namespace가 필요합니다.')
  }
  if (isolation.database.schema === 'public' || ['*', ''].includes(isolation.redis.namespace)) {
    throw new Error('공유 DB/Redis 전체를 cleanup 대상으로 사용할 수 없습니다.')
  }
  if (!isolation.cleanup.every((step) => step.target.includes(isolation.runId.slice(4)))) {
    throw new Error('cleanup 대상은 현재 run의 격리 token을 포함해야 합니다.')
  }
  if (!isolation.parallelSafe) throw new Error('DB schema와 Redis namespace 격리가 모두 필요합니다.')
  return isolation
}
