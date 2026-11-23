import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'

export function presentationLabPlugin() {
  return {
    name: 'presentation-lab-data',
    apply: 'serve',
    configureServer(server) {
      const dataPath = resolve(server.config.root, '.dev/presentation.json')
      server.middlewares.use('/__dev/presentation-data', async (request, response) => {
        response.setHeader('Content-Type', 'application/json; charset=utf-8')
        response.setHeader('Cache-Control', 'no-store')
        if (request.method !== 'GET') {
          response.statusCode = 405
          response.end(JSON.stringify({ message: 'GET only' }))
          return
        }
        try {
          response.end(await readFile(dataPath, 'utf8'))
        } catch {
          response.statusCode = 503
          response.end(JSON.stringify({ message: '계산 데이터를 준비해 주세요: npm run prepare:presentation' }))
        }
      })
    },
  }
}
