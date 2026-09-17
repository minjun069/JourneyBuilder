import assert from 'node:assert/strict'
import test from 'node:test'

import {
  hasPresentationEntryReceipt,
  presentationEntryReceiptKey,
  savePresentationEntryReceipt,
} from './presentationEntry.js'


test('동일 보고서 버전은 세션 동안 한 번만 안내한다', () => {
  const memory = new Set()
  const stored = new Map()
  const storage = {
    getItem: key => stored.get(key) ?? null,
    setItem: (key, value) => stored.set(key, value),
  }
  const key = presentationEntryReceiptKey({
    sessionId: 12,
    stepId: 'lifetime-cashflow',
    reportVersion: 'report:7:v2',
  })

  assert.equal(hasPresentationEntryReceipt(memory, storage, key), false)
  savePresentationEntryReceipt(memory, storage, key)
  assert.equal(hasPresentationEntryReceipt(memory, storage, key), true)
})

test('보고서 버전이 바뀌면 다시 안내할 수 있다', () => {
  const memory = new Set()
  const storage = { getItem: () => null, setItem: () => {} }
  const base = { sessionId: 12, stepId: 'cash-flow' }
  const previous = presentationEntryReceiptKey({ ...base, reportVersion: 'report:8:v1' })
  const current = presentationEntryReceiptKey({ ...base, reportVersion: 'report:8:v2' })

  savePresentationEntryReceipt(memory, storage, previous)
  assert.equal(hasPresentationEntryReceipt(memory, storage, previous), true)
  assert.equal(hasPresentationEntryReceipt(memory, storage, current), false)
})
