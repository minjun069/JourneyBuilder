import assert from 'node:assert/strict'
import test from 'node:test'

import { addSentenceLineBreaks, renderChatMarkdown } from './chatMarkdown.js'

test('마침표 뒤는 줄바꿈하지만 소수점·목록 번호·코드는 보존한다', () => {
  assert.equal(
    addSentenceLineBreaks('첫 문장입니다. 다음 문장입니다.\n1. 항목\n금리는 3.5%입니다.\n`value. next`'),
    '첫 문장입니다.\n다음 문장입니다.\n1. 항목\n금리는 3.5%입니다.\n`value. next`',
  )
})

test('AI Markdown 부분집합을 렌더링한다', () => {
  const html = renderChatMarkdown('# 제목\n\n- **중요**\n- [문서](https://example.com)\n\n`코드`')
  assert.match(html, /<h1>제목<\/h1>/)
  assert.match(html, /<ul><li><strong>중요<\/strong><\/li>/)
  assert.match(html, /href="https:\/\/example.com"/)
  assert.match(html, /<code>코드<\/code>/)
})

test('원본 HTML과 위험한 링크는 실행 가능한 HTML로 남기지 않는다', () => {
  const html = renderChatMarkdown('<img src=x onerror=alert(1)> [실행](javascript:alert(1))')
  assert.doesNotMatch(html, /<img/)
  assert.doesNotMatch(html, /href=/)
  assert.match(html, /&lt;img/)
})
