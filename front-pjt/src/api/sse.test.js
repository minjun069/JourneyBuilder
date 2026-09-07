import assert from 'node:assert/strict'
import test from 'node:test'

import {
  SSEProtocolError,
  createSSEParser,
  readSSEStream,
  validateSSEEvents,
} from './sse.js'


test('분할된 SSE 프레임을 순서대로 조립한다', () => {
  const parser = createSSEParser()

  assert.deepEqual(parser.feed('event: start\ndata: {"request_id":"req-1"}\n'), [])
  assert.deepEqual(parser.feed('\nevent: delta\ndata: {"content":"안녕"}\n\n'), [
    { event: 'start', data: { request_id: 'req-1' } },
    { event: 'delta', data: { content: '안녕' } },
  ])
  assert.deepEqual(parser.finish(), [])
})


test('UTF-8 문자가 바이트 경계에서 나뉘어도 보존한다', async () => {
  const bytes = new TextEncoder().encode('event: delta\ndata: {"content":"은퇴"}\n\n')
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(bytes.slice(0, bytes.length - 2))
      controller.enqueue(bytes.slice(bytes.length - 2))
      controller.close()
    },
  })

  const events = []
  for await (const event of readSSEStream(body)) events.push(event)

  assert.deepEqual(events, [{ event: 'delta', data: { content: '은퇴' } }])
})


test('JSON 객체가 아닌 SSE data는 거절한다', () => {
  const parser = createSSEParser()

  assert.throws(
    () => parser.feed('event: delta\ndata: ["invalid"]\n\n'),
    SSEProtocolError,
  )
})


test('start와 동일한 request_id로 끝나는 이벤트 흐름만 허용한다', async () => {
  async function* source() {
    yield { event: 'start', data: { request_id: 'req-1' } }
    yield { event: 'delta', data: { request_id: 'req-1', content: '답변' } }
    yield { event: 'done', data: { request_id: 'req-1', output: {} } }
    yield { event: 'delta', data: { request_id: 'req-1', content: '무시' } }
  }

  const events = []
  for await (const event of validateSSEEvents(source())) events.push(event)

  assert.deepEqual(events.map(({ event }) => event), ['start', 'delta', 'done'])
})


test('request_id가 변경되면 스트림을 거절한다', async () => {
  async function* source() {
    yield { event: 'start', data: { request_id: 'req-1' } }
    yield { event: 'delta', data: { request_id: 'req-2', content: '잘못된 답변' } }
  }

  await assert.rejects(async () => {
    for await (const event of validateSSEEvents(source())) void event
  }, SSEProtocolError)
})
