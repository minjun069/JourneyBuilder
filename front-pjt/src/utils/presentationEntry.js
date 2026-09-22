export function presentationEntryReceiptKey({ sessionId, stepId, reportVersion }) {
  if (sessionId == null || !stepId || !reportVersion) {
    throw new Error('Presentation 안내 receipt 식별자가 필요합니다.')
  }
  return ['presentation-entry-v2', sessionId, stepId, reportVersion].join(':')
}

export function hasPresentationEntryReceipt(memory, storage, key) {
  if (memory.has(key)) return true
  try {
    return storage?.getItem(key) === 'done'
  } catch {
    return false
  }
}

export function savePresentationEntryReceipt(memory, storage, key) {
  memory.add(key)
  try {
    storage?.setItem(key, 'done')
  } catch {
    // in-memory receipt remains authoritative for this page lifecycle.
  }
}

export function isSuccessfulPresentationEntryMessage(message) {
  return message?.extracted_data?.presentation_entry_status === 'succeeded'
}
