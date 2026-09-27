// Keep this boundary aligned with core/mindset_text.py via the shared fixture.
const textKeys = ['summary', 'response', 'values', 'past_experience', 'current_situation',
  'current_challenge', 'invest_philosophy', 'future_goals']
const quote = '["\x27`*]*'
const control = new RegExp(`(?<!\\w)${quote}(sufficient|turn_kind)${quote}\\s*[:=]`)
const valuePattern = new RegExp(`(?<!\\w)${quote}(?:sufficient${quote}\\s*[:=]\\s*${quote}(?:true|false)|turn_kind${quote}\\s*[:=]\\s*${quote}(?:answer|clarification|skip))\\b${quote}\\s*[,;]?`, 'gi')
const label = new RegExp(`^\\s*(?:[-*]\\s+)?${quote}(${textKeys.join('|')})${quote}\\s*:\\s*`)
const remainingLabel = new RegExp(`(?<!\\w)${quote}(?:${textKeys.join('|')})${quote}\\s*:`)
const invalid = () => { throw new Error('경제 관념 요약을 확인하지 못했습니다. 다시 시도해주세요.') }

export function cleanMindsetText(value, preferred = 'summary', depth = 0) {
  if (typeof value !== 'string' || depth > 3) return invalid()
  const text = value.trim()
  const fenced = text.match(/^```(?:json|yaml|text)?\s*\n?([\s\S]*?)\n?```$/)
  const candidate = fenced ? fenced[1].trim() : text
  if (candidate.startsWith('{')) {
    let payload
    try { payload = JSON.parse(candidate) } catch { /* Handle recognized plain text below. */ }
    const allowed = [...textKeys, 'sufficient', 'turn_kind']
    if (payload && typeof payload === 'object' && !Array.isArray(payload)
      && Object.keys(payload).some((key) => allowed.includes(key))) {
      if (Object.keys(payload).some((key) => !allowed.includes(key))) return invalid()
      const keys = textKeys.filter((key) => key in payload && key !== 'response')
      const key = preferred in payload ? preferred : keys.length === 1 ? keys[0] : null
      if (!key || typeof payload[key] !== 'string') return invalid()
      return cleanMindsetText(payload[key], preferred, depth + 1)
    }
  }
  let cleaned = candidate.replace(valuePattern, '').replace(label, '')
  if (control.test(cleaned) || remainingLabel.test(cleaned)) return invalid()
  if (cleaned === candidate) return value
  cleaned = cleaned.trim().replace(/[,;]+$/, '').trim()
  if (cleaned.length >= 2 && cleaned[0] === cleaned.at(-1) && '"\x27`'.includes(cleaned[0])) {
    cleaned = cleaned.slice(1, -1)
  }
  if (!cleaned || cleaned.startsWith('{') || cleaned.endsWith('}')) return invalid()
  return cleanMindsetText(cleaned, preferred, depth + 1)
}
