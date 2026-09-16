const SAFE_LINK_PATTERN = /^(https?:\/\/|mailto:|\/[^/]|#)/i

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

function formatInline(source) {
  const tokens = []
  const stash = (html) => {
    const index = tokens.push(html) - 1
    return `\uE000${index}\uE001`
  }

  let text = String(source ?? '').replace(/`([^`\n]+)`/g, (_, code) => (
    stash(`<code>${escapeHtml(code)}</code>`)
  ))
  text = text.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (_, label, href) => {
    if (!SAFE_LINK_PATTERN.test(href)) return escapeHtml(label)
    return stash(
      `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`,
    )
  })
  text = escapeHtml(text)
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/__([^_\n]+)__/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replaceAll('\n', '<br>')

  return text.replace(/\uE000(\d+)\uE001/g, (_, index) => tokens[Number(index)] ?? '')
}

/**
 * AI 답변의 마침표 뒤 공백을 줄바꿈으로 정규화한다.
 * 코드 블록, 인라인 코드, 소수점/목록 번호는 변형하지 않는다.
 */
export function addSentenceLineBreaks(markdown) {
  let inFence = false
  return String(markdown ?? '')
    .replaceAll('\r\n', '\n')
    .split('\n')
    .map((line) => {
      if (/^\s*```/.test(line)) {
        inFence = !inFence
        return line
      }
      if (inFence) return line

      return line.split(/(`[^`]*`)/g).map((part) => {
        if (part.startsWith('`') && part.endsWith('`')) return part
        return part.replace(/([^\d.\s])\.[ \t]+/g, '$1.\n')
      }).join('')
    })
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
}

/**
 * 외부 HTML 입력은 모두 이스케이프하고 채팅에 필요한 Markdown 부분집합만 생성한다.
 */
export function renderChatMarkdown(markdown) {
  const lines = addSentenceLineBreaks(markdown).split('\n')
  const blocks = []
  let paragraph = []
  let list = null
  let code = null

  const flushParagraph = () => {
    if (!paragraph.length) return
    blocks.push(`<p>${formatInline(paragraph.join('\n'))}</p>`)
    paragraph = []
  }
  const flushList = () => {
    if (!list) return
    blocks.push(`<${list.tag}>${list.items.map((item) => `<li>${formatInline(item)}</li>`).join('')}</${list.tag}>`)
    list = null
  }

  for (const line of lines) {
    const fence = line.match(/^\s*```([^\s`]*)\s*$/)
    if (fence) {
      flushParagraph()
      flushList()
      if (code) {
        const language = code.language ? ` class="language-${escapeHtml(code.language)}"` : ''
        blocks.push(`<pre><code${language}>${escapeHtml(code.lines.join('\n'))}</code></pre>`)
        code = null
      } else {
        code = { language: fence[1], lines: [] }
      }
      continue
    }
    if (code) {
      code.lines.push(line)
      continue
    }
    if (!line.trim()) {
      flushParagraph()
      flushList()
      continue
    }

    const heading = line.match(/^(#{1,3})\s+(.+)$/)
    if (heading) {
      flushParagraph()
      flushList()
      const level = heading[1].length
      blocks.push(`<h${level}>${formatInline(heading[2])}</h${level}>`)
      continue
    }

    const quote = line.match(/^>\s?(.+)$/)
    if (quote) {
      flushParagraph()
      flushList()
      blocks.push(`<blockquote>${formatInline(quote[1])}</blockquote>`)
      continue
    }

    const unordered = line.match(/^\s*[-*+]\s+(.+)$/)
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/)
    if (unordered || ordered) {
      flushParagraph()
      const tag = unordered ? 'ul' : 'ol'
      if (list?.tag !== tag) {
        flushList()
        list = { tag, items: [] }
      }
      list.items.push((unordered ?? ordered)[1])
      continue
    }

    flushList()
    paragraph.push(line)
  }

  if (code) {
    blocks.push(`<pre><code>${escapeHtml(code.lines.join('\n'))}</code></pre>`)
  }
  flushParagraph()
  flushList()
  return blocks.join('')
}
