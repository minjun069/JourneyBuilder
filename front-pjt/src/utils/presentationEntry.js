import { createSecureUuid } from './secureUuid.js'

export function createPresentationEntryVisit({ sessionId, stepId, visitId = createSecureUuid() }) {
  const inFlight = new Set()
  const completed = new Set()
  return {
    sessionId,
    stepId,
    visitId,
    begin(reportVersion) {
      if (!reportVersion || inFlight.has(reportVersion) || completed.has(reportVersion)) return false
      inFlight.add(reportVersion)
      return true
    },
    finish(reportVersion, message) {
      inFlight.delete(reportVersion)
      if (isSuccessfulPresentationEntryMessage(message)) completed.add(reportVersion)
    },
  }
}

export function isSuccessfulPresentationEntryMessage(message) {
  return message?.extracted_data?.presentation_entry_status === 'succeeded'
}
