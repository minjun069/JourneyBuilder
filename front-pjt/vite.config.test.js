import assert from 'node:assert/strict'
import test from 'node:test'

import { createVitePlugins } from './vite.config.js'

function pluginNames(command, mode) {
  return createVitePlugins(command, mode).flat(Infinity).map((plugin) => plugin.name)
}

test('Vue DevTools는 개발 서버에서만 활성화한다', () => {
  assert.ok(pluginNames('serve', 'development').includes('vite-plugin-vue-devtools'))
  assert.ok(!pluginNames('serve', 'production').includes('vite-plugin-vue-devtools'))
  assert.ok(!pluginNames('build', 'production').includes('vite-plugin-vue-devtools'))
})

test('Presentation 계산 데이터 endpoint는 개발 서버에만 등록한다', () => {
  assert.ok(pluginNames('serve', 'development').includes('presentation-lab-data'))
  assert.ok(!pluginNames('serve', 'production').includes('presentation-lab-data'))
  assert.ok(!pluginNames('build', 'production').includes('presentation-lab-data'))
  assert.ok(!pluginNames('build', 'development').includes('presentation-lab-data'))
})
