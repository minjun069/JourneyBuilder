import { createServer } from 'node:http'

const port = Number(process.env.F05_FIXTURE_PORT ?? 8001)
const bearerToken = process.env.AI_SERVICE_BEARER_TOKEN
const controlToken = process.env.F05_CONTROL_TOKEN
let preparedOutput = null

if (!bearerToken || !controlToken) {
  throw new Error('AI_SERVICE_BEARER_TOKEN과 F05_CONTROL_TOKEN이 필요합니다.')
}

function json(response, status, body) {
  response.writeHead(status, { 'content-type': 'application/json; charset=utf-8' })
  response.end(JSON.stringify(body))
}

async function readJson(request) {
  const chunks = []
  for await (const chunk of request) chunks.push(chunk)
  return JSON.parse(Buffer.concat(chunks).toString('utf8'))
}

function authorized(request, expected) {
  return request.headers.authorization === `Bearer ${expected}`
}

function writeEvent(response, event, data) {
  response.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
}

const server = createServer(async (request, response) => {
  if (request.method === 'GET' && request.url === '/health') {
    return json(response, 200, { status: 'ok' })
  }

  if (request.method === 'POST' && request.url === '/__e2e/fixture') {
    if (!authorized(request, controlToken)) return json(response, 401, { error: 'unauthorized' })
    try {
      const body = await readJson(request)
      if (!body?.output?.response || !body?.output?.action) {
        return json(response, 422, { error: 'output is required' })
      }
      preparedOutput = structuredClone(body.output)
      return json(response, 202, { status: 'prepared' })
    } catch {
      return json(response, 400, { error: 'invalid json' })
    }
  }

  if (request.method === 'POST' && request.url === '/v2/chat/stream') {
    if (!authorized(request, bearerToken)) return json(response, 401, { error: 'unauthorized' })
    if (!preparedOutput) return json(response, 409, { error: 'fixture not prepared' })

    let payload
    try {
      payload = await readJson(request)
    } catch {
      return json(response, 400, { error: 'invalid json' })
    }

    const output = preparedOutput
    preparedOutput = null
    const identity = {
      contract_version: '2.0',
      request_id: payload.request_id,
      session_id: payload.session_id,
      thread_id: payload.thread_id,
    }

    response.writeHead(200, {
      'content-type': 'text/event-stream',
      'cache-control': 'no-cache',
      connection: 'keep-alive',
    })
    writeEvent(response, 'start', identity)
    await new Promise((resolve) => setTimeout(resolve, 200))
    writeEvent(response, 'delta', { ...identity, content: output.response })
    writeEvent(response, 'done', {
      ...identity,
      status: 'completed',
      completed_at: new Date().toISOString(),
      output,
    })
    response.end()
    return undefined
  }

  return json(response, 404, { error: 'not found' })
})

server.listen(port, '0.0.0.0', () => {
  process.stdout.write(`F05 fixture AI listening on ${port}\n`)
})
