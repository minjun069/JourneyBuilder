export class SSEProtocolError extends Error {
  constructor(message) {
    super(message)
    this.name = 'SSEProtocolError'
  }
}

function parseFrame(frame) {
  let event = 'message'
  const dataLines = []

  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(':')) continue
    const separator = line.indexOf(':')
    const field = separator === -1 ? line : line.slice(0, separator)
    let value = separator === -1 ? '' : line.slice(separator + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') event = value
    if (field === 'data') dataLines.push(value)
  }

  if (!dataLines.length) return null

  let data
  try {
    data = JSON.parse(dataLines.join('\n'))
  } catch {
    throw new SSEProtocolError('SSE data가 올바른 JSON이 아닙니다.')
  }
  if (!data || Array.isArray(data) || typeof data !== 'object') {
    throw new SSEProtocolError('SSE data는 JSON 객체여야 합니다.')
  }
  return { event, data }
}

export function createSSEParser() {
  let buffer = ''

  function feed(chunk) {
    buffer += chunk
    const events = []
    let separator = buffer.match(/\r?\n\r?\n/)
    while (separator) {
      const frame = buffer.slice(0, separator.index)
      buffer = buffer.slice(separator.index + separator[0].length)
      const parsed = parseFrame(frame)
      if (parsed) events.push(parsed)
      separator = buffer.match(/\r?\n\r?\n/)
    }
    return events
  }

  function finish() {
    const frame = buffer
    buffer = ''
    if (!frame.trim()) return []
    const parsed = parseFrame(frame)
    return parsed ? [parsed] : []
  }

  return { feed, finish }
}

export async function* readSSEStream(body) {
  if (!body) throw new SSEProtocolError('SSE 응답 본문이 없습니다.')

  const reader = body.getReader()
  const decoder = new TextDecoder()
  const parser = createSSEParser()
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      for (const event of parser.feed(decoder.decode(value, { stream: true }))) {
        yield event
      }
    }
    for (const event of parser.feed(decoder.decode())) yield event
    for (const event of parser.finish()) yield event
  } finally {
    reader.releaseLock()
  }
}


export async function* validateSSEEvents(events) {
  const allowedEvents = new Set(['start', 'delta', 'done', 'error'])
  let requestId = null
  let started = false

  for await (const event of events) {
    if (!allowedEvents.has(event.event)) {
      throw new SSEProtocolError(`지원하지 않는 SSE 이벤트입니다: ${event.event}`)
    }
    if (event.event === 'start') {
      if (started || typeof event.data.request_id !== 'string') {
        throw new SSEProtocolError('SSE start 이벤트가 올바르지 않습니다.')
      }
      started = true
      requestId = event.data.request_id
    } else if (!started) {
      throw new SSEProtocolError('SSE start 이벤트가 누락되었습니다.')
    }
    if (requestId && event.data.request_id !== requestId) {
      throw new SSEProtocolError('SSE request_id가 요청 도중 변경되었습니다.')
    }
    yield event
    if (event.event === 'done' || event.event === 'error') return
  }
  throw new SSEProtocolError('SSE 연결이 완료 이벤트 없이 종료되었습니다.')
}


export async function* validateV2SSEEvents(events) {
  for await (const event of validateSSEEvents(events)) {
    if (event.data.contract_version !== '2.0') {
      throw new SSEProtocolError('SSE contract_version이 올바르지 않습니다.')
    }
    if (event.event === 'delta' && typeof event.data.content !== 'string') {
      throw new SSEProtocolError('SSE delta content가 올바르지 않습니다.')
    }
    if (event.event === 'done') {
      const message = event.data.message
      if (
        event.data.status !== 'completed'
        || message == null
        || typeof message !== 'object'
        || !Number.isInteger(message.id)
        || message.role !== 'assistant'
        || typeof message.content !== 'string'
      ) {
        throw new SSEProtocolError('SSE 완료 메시지 형식이 올바르지 않습니다.')
      }
    }
    if (event.event === 'error') {
      const error = event.data.error
      if (
        event.data.status !== 'failed'
        || error == null
        || typeof error !== 'object'
        || typeof error.code !== 'string'
        || typeof error.message !== 'string'
        || typeof error.retryable !== 'boolean'
      ) {
        throw new SSEProtocolError('SSE 오류 형식이 올바르지 않습니다.')
      }
    }
    yield event
  }
}
