import assert from 'node:assert/strict'
import test from 'node:test'

import {
  SSEProtocolError,
  createSSEParser,
  readSSEStream,
  validateSSEEvents,
  validateV2SSEEvents,
} from './sse.js'


test('분할된 SSE 프레임을 순서대로 조립한다', () => {
  const parser = createSSEParser()

  assert.deepEqual(parser.feed('event: start\ndata: {"request_id":"req-1"}\n'), [])
  assert.deepEqual(parser.feed('\nevent: done\ndata: {"content":"안녕"}\n\n'), [
    { event: 'start', data: { request_id: 'req-1' } },
    { event: 'done', data: { content: '안녕' } },
  ])
  assert.deepEqual(parser.finish(), [])
})


test('UTF-8 문자가 바이트 경계에서 나뉘어도 보존한다', async () => {
  const bytes = new TextEncoder().encode('event: done\ndata: {"content":"은퇴"}\n\n')
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(bytes.slice(0, bytes.length - 2))
      controller.enqueue(bytes.slice(bytes.length - 2))
      controller.close()
    },
  })

  const events = []
  for await (const event of readSSEStream(body)) events.push(event)

  assert.deepEqual(events, [{ event: 'done', data: { content: '은퇴' } }])
})


test('JSON 객체가 아닌 SSE data는 거절한다', () => {
  const parser = createSSEParser()

  assert.throws(
    () => parser.feed('event: done\ndata: ["invalid"]\n\n'),
    SSEProtocolError,
  )
})


test('start와 동일한 request_id로 끝나는 이벤트 흐름만 허용한다', async () => {
  async function* source() {
    yield { event: 'start', data: { request_id: 'req-1' } }
    yield { event: 'done', data: { request_id: 'req-1', output: {} } }
    yield { event: 'error', data: { request_id: 'req-1', error: {} } }
  }

  const events = []
  for await (const event of validateSSEEvents(source())) events.push(event)

  assert.deepEqual(events.map(({ event }) => event), ['start', 'done'])
})


test('request_id가 변경되면 스트림을 거절한다', async () => {
  async function* source() {
    yield { event: 'start', data: { request_id: 'req-1' } }
    yield { event: 'done', data: { request_id: 'req-2', output: {} } }
  }

  await assert.rejects(async () => {
    for await (const event of validateSSEEvents(source())) void event
  }, SSEProtocolError)
})


test('delta 이벤트는 지원하지 않는다', async () => {
  async function* source() {
    yield { event: 'start', data: { request_id: 'req-1' } }
    yield { event: 'delta', data: { request_id: 'req-1', content: '부분 답변' } }
  }

  await assert.rejects(async () => {
    for await (const event of validateSSEEvents(source())) void event
  }, SSEProtocolError)
})


test('진행 단계와 heartbeat는 단조 증가 sequence만 허용한다', async () => {
  async function* source() {
    yield { event: 'start', data: { request_id: 'req-1' } }
    yield {
      event: 'progress',
      data: {
        request_id: 'req-1',
        contract_version: '2.0',
        event_seq: 1,
        phase: 'planning',
        status: 'active',
      },
    }
    yield {
      event: 'heartbeat',
      data: { request_id: 'req-1', contract_version: '2.0', event_seq: 2 },
    }
    yield { event: 'done', data: { request_id: 'req-1', output: {} } }
  }

  const events = []
  for await (const event of validateSSEEvents(source())) events.push(event)

  assert.deepEqual(
    events.map(({ event }) => event),
    ['start', 'progress', 'heartbeat', 'done'],
  )
})


test('역순 또는 콘텐츠가 포함된 진행 이벤트는 거절한다', async () => {
  async function* reversed() {
    yield { event: 'start', data: { request_id: 'req-1' } }
    yield {
      event: 'heartbeat',
      data: { request_id: 'req-1', contract_version: '2.0', event_seq: 2 },
    }
    yield {
      event: 'progress',
      data: {
        request_id: 'req-1',
        contract_version: '2.0',
        event_seq: 1,
        phase: 'planning',
        status: 'active',
      },
    }
  }
  await assert.rejects(async () => {
    for await (const event of validateSSEEvents(reversed())) void event
  }, SSEProtocolError)

  async function* contentBearing() {
    yield { event: 'start', data: { request_id: 'req-1' } }
    yield {
      event: 'progress',
      data: {
        request_id: 'req-1',
        contract_version: '2.0',
        event_seq: 1,
        phase: 'planning',
        status: 'active',
        content: '노출하면 안 되는 내용',
      },
    }
  }
  await assert.rejects(async () => {
    for await (const event of validateSSEEvents(contentBearing())) void event
  }, SSEProtocolError)
})


test('v2 SSE 완료 이벤트는 저장된 assistant message를 검증한다', async () => {
  async function* source() {
    yield {
      event: 'start',
      data: { contract_version: '2.0', request_id: 'req-v2' },
    }
    yield {
      event: 'done',
      data: {
        contract_version: '2.0',
        request_id: 'req-v2',
        status: 'completed',
        message: {
          id: 42,
          role: 'assistant',
          content: '답변',
          extracted_data: { action: null },
        },
      },
    }
  }

  const events = []
  for await (const event of validateV2SSEEvents(source())) events.push(event)

  assert.deepEqual(events.map(({ event }) => event), ['start', 'done'])
})


test('v2 SSE는 contract version과 terminal error 구조를 강제한다', async () => {
  async function* invalidVersion() {
    yield { event: 'start', data: { contract_version: '1.0', request_id: 'req-v2' } }
  }
  await assert.rejects(async () => {
    for await (const event of validateV2SSEEvents(invalidVersion())) void event
  }, SSEProtocolError)

  async function* invalidError() {
    yield { event: 'start', data: { contract_version: '2.0', request_id: 'req-v2' } }
    yield {
      event: 'error',
      data: {
        contract_version: '2.0',
        request_id: 'req-v2',
        status: 'failed',
        error: { code: 'INTERNAL_ERROR', message: '실패' },
      },
    }
  }
  await assert.rejects(async () => {
    for await (const event of validateV2SSEEvents(invalidError())) void event
  }, SSEProtocolError)
})
