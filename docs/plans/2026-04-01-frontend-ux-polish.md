# Frontend UX Polish Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver 6 targeted UX improvements to the Paramind desktop frontend, prioritized by user impact with maximum parallel execution.

**Architecture:** All changes are contained within `paramind/apps/desktop/renderer/` — no backend, no build step, no framework. Pure HTML/CSS/JS. The renderer loads `chat_state.js` (pure functions, unit-testable) and `chat.js` (DOM rendering, tested via the Playwright harness at `test/backend/test_renderer_harness.spec.mjs`).

**Tech Stack:** Vanilla JS · marked.js 9.x (CDN, ~15kb gz) · DOMPurify 3.x (CDN, XSS sanitization) · Playwright (existing test harness)

---

## Parallelization Map

```
Group 1 — fully independent, run in parallel:
  Task 1: Markdown rendering       (index.html + chat.js)
  Task 2: Textarea auto-resize     (index.html + chat.js)
  Task 3: @AI mode toggle          (index.html + chat.js)

Group 2 — run after Group 1 merges (touch adjacent scroll/stream code):
  Task 4: Scroll-to-new indicator  (index.html + chat.js)
  Task 5: Streaming typing bubble  (index.html + chat.js)

Group 3 — fully standalone, any time:
  Task 6: Route SVG visualization  (index.html + chat.js)
```

---

## Task 1: Markdown Rendering

**Why first:** AI responses contain raw `**bold**`, `` `code` ``, lists. Without rendering, every AI message looks broken. Highest user-visible impact.

**Files:**
- Modify: `paramind/apps/desktop/renderer/index.html` — add CDN scripts, add `.message-card` prose styles
- Modify: `paramind/apps/desktop/renderer/chat.js:981` — `renderBaseMessageCard`, change content from `escapeHtml` to parsed markdown
- Modify: `paramind/apps/desktop/renderer/chat.js:749` — `renderDraftCard`, same treatment
- Modify: `paramind/apps/desktop/renderer/chat.js:1229,1232,1333` — streaming content updates
- Test: `test/backend/test_renderer_harness.spec.mjs`

**Step 1: Add marked.js + DOMPurify to index.html**

In `index.html`, add before the closing `</head>` tag (after the existing `<style>` block, before `</head>`):

```html
<script src="https://cdn.jsdelivr.net/npm/marked@9/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js"></script>
```

Note: Electron's CSP (`script-src 'self' 'unsafe-inline'`) blocks CDN scripts. You must add `https://cdn.jsdelivr.net` to `script-src`:

```html
content="default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; connect-src 'self' http://127.0.0.1:* http://localhost:* ws://127.0.0.1:* ws://localhost:*"
```

**Step 2: Add prose styles to index.html CSS**

Add inside the `<style>` block, after `.message-card { ... }` (~line 394):

```css
/* Markdown prose inside message cards */
.message-card.prose p { margin: 0 0 8px; }
.message-card.prose p:last-child { margin-bottom: 0; }
.message-card.prose code {
  font-family: var(--font-mono);
  font-size: 12.5px;
  background: rgba(255,255,255,.06);
  padding: 1px 5px;
  border-radius: 4px;
  word-break: break-all;
}
.message-card.prose pre {
  background: rgba(0,0,0,.35);
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  padding: 12px 14px;
  overflow-x: auto;
  margin: 8px 0;
}
.message-card.prose pre code {
  background: none;
  padding: 0;
  font-size: 12.5px;
}
.message-card.prose ul, .message-card.prose ol {
  padding-left: 20px;
  margin: 6px 0;
}
.message-card.prose li { margin-bottom: 3px; }
.message-card.prose strong { color: var(--text); font-weight: 600; }
.message-card.prose blockquote {
  border-left: 3px solid var(--accent-glow);
  padding-left: 12px;
  color: var(--muted);
  margin: 8px 0;
}
.message-card.prose h1,.message-card.prose h2,.message-card.prose h3 {
  font-family: var(--font-display);
  margin: 12px 0 6px;
  letter-spacing: -.02em;
}
```

**Step 3: Add renderMarkdown helper to chat.js**

Add after the `escapeHtml` function (~line 76 in `chat.js`):

```js
function renderMarkdown(content) {
  if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
    return escapeHtml(content)
  }
  const raw = marked.parse(String(content || ''), { breaks: true, gfm: true })
  return DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } })
}
```

**Step 4: Apply renderMarkdown in renderBaseMessageCard**

In `chat.js:981`, change:
```js
// BEFORE
<div class="message-card" data-message-content="${escapeHtml(message.id)}">${escapeHtml(content)}</div>
// AFTER
<div class="message-card prose" data-message-content="${escapeHtml(message.id)}">${renderMarkdown(content)}</div>
```

**Step 5: Apply renderMarkdown in renderDraftCard**

In `chat.js:749`, change:
```js
// BEFORE
: `<div class="message-card draft-card" data-message-content="${escapeHtml(message.id)}">${escapeHtml(message.content || '生成中…')}</div>`
// AFTER
: `<div class="message-card draft-card prose" data-message-content="${escapeHtml(message.id)}">${renderMarkdown(message.content || '生成中…')}</div>`
```

**Step 6: Fix streaming content updates**

Streaming uses `.textContent` assignment (safe, no XSS risk) at lines ~1229, ~1232, ~1333. These need to switch to `.innerHTML` + DOMPurify when streaming is complete, but during streaming, keep raw text for performance.

In `chat.js`, find the two places that do `contentNode.textContent = message.content || ''` inside streaming paths (~1229, ~1232), change to:

```js
// Keep textContent during active stream (performance)
contentNode.textContent = message.content || ''
```

Leave these as-is — they're fine. The final render after streaming ends will call `patchMessageNode` → `renderTimelineCard` → `renderBaseMessageCard` → `renderMarkdown`, which does the proper parse.

For the token-append path (~1333):
```js
// BEFORE
contentNode.textContent = message.content || `${contentNode.textContent || ''}${token}`
// AFTER — no change needed, streaming stays as text; final patch handles markdown
contentNode.textContent = message.content || `${contentNode.textContent || ''}${token}`
```

No change needed on streaming paths. The final `patchMessageNode` call handles re-render.

**Step 7: Write failing test**

Add to `test/backend/test_renderer_harness.spec.mjs`:

```js
test('renders markdown in AI messages', async ({ page }) => {
  await page.goto(baseUrl + '/dev_harness.html?harness=1')
  await page.waitForLoadState('networkidle')
  // Send a fixture that injects a markdown message
  await page.evaluate(() => {
    window.__harness?.injectMessage?.({
      id: 'md-test-1',
      role: 'assistant',
      sender_id: 'assistant',
      sender_name: 'AI',
      content: '**bold text** and `inline code`',
      status: 'completed',
    })
  })
  const bold = page.locator('.message-card.prose strong')
  await expect(bold).toHaveText('bold text')
  const code = page.locator('.message-card.prose code')
  await expect(code).toHaveText('inline code')
})
```

**Step 8: Run test to verify it fails**

```bash
cd /Users/acropolis/Github_Project/Paramind/paramind/apps/desktop
npx playwright test test_renderer_harness.spec.mjs --grep "renders markdown" -v
```

Expected: FAIL (no `.prose` class, no `<strong>` in DOM)

**Step 9: Run test to verify it passes after implementation**

```bash
npx playwright test test_renderer_harness.spec.mjs --grep "renders markdown" -v
```

Expected: PASS

**Step 10: Commit**

```bash
git add paramind/apps/desktop/renderer/index.html paramind/apps/desktop/renderer/chat.js test/backend/test_renderer_harness.spec.mjs
git commit -m "feat(frontend): add markdown rendering with DOMPurify sanitization"
```

---

## Task 2: Textarea Auto-Resize

**Why:** Fixed 96px textarea with internal scroll is a jarring UX regression. 3 lines of CSS + 5 lines of JS.

**Files:**
- Modify: `paramind/apps/desktop/renderer/index.html` — remove `resize: vertical`, adjust `min-height`
- Modify: `paramind/apps/desktop/renderer/chat.js` — add input listener
- Test: `test/backend/test_renderer_harness.spec.mjs`

**Step 1: Update composer textarea styles in index.html**

Find `.composer textarea` (~line 463), change:
```css
/* BEFORE */
min-height: 96px;
resize: vertical;

/* AFTER */
min-height: 48px;
max-height: 320px;
resize: none;
overflow-y: auto;
```

**Step 2: Add auto-resize listener in chat.js**

Find where the composer form listener is bound. Search for `composerForm` — it's around line 1480+. After the form submit handler binding, add:

```js
// Auto-resize textarea
const composerInput = $('composerInput')
if (composerInput) {
  composerInput.addEventListener('input', () => {
    composerInput.style.height = 'auto'
    composerInput.style.height = `${Math.min(composerInput.scrollHeight, 320)}px`
  })
}
```

Also reset height after send. Find where `composerInput.value = ''` is called after successful send (~search for `composerInput.value`), and add after it:

```js
composerInput.style.height = 'auto'
```

**Step 3: Write failing test**

```js
test('textarea expands on multi-line input', async ({ page }) => {
  await page.goto(baseUrl + '/dev_harness.html?harness=1')
  const textarea = page.locator('#composerInput')
  const initialHeight = await textarea.evaluate(el => el.getBoundingClientRect().height)
  await textarea.fill('line1\nline2\nline3\nline4\nline5')
  await textarea.dispatchEvent('input')
  const expandedHeight = await textarea.evaluate(el => el.getBoundingClientRect().height)
  expect(expandedHeight).toBeGreaterThan(initialHeight)
})
```

**Step 4: Run → FAIL → implement → run → PASS**

```bash
npx playwright test test_renderer_harness.spec.mjs --grep "textarea expands" -v
```

**Step 5: Commit**

```bash
git add paramind/apps/desktop/renderer/index.html paramind/apps/desktop/renderer/chat.js test/backend/test_renderer_harness.spec.mjs
git commit -m "feat(frontend): auto-resize composer textarea"
```

---

## Task 3: @AI Mode Toggle

**Why:** `@AI` prefix buried in placeholder text — users don't discover it. A visible toggle makes the feature prominent.

**Files:**
- Modify: `paramind/apps/desktop/renderer/index.html` — add toggle button + CSS
- Modify: `paramind/apps/desktop/renderer/chat.js` — toggle state, modify send logic

**Step 1: Add toggle button HTML in index.html**

In the `<form class="composer">` section, add a toggle before the `<textarea>`:

```html
<form class="composer" id="composerForm">
  <div class="composer-mode">
    <button type="button" id="modeToggleBtn" class="mode-btn mode-text active" data-mode="text">
      <svg class="ico ico-sm"><use href="#ico-message-square"/></svg>
      <span>普通消息</span>
    </button>
    <button type="button" id="modeToggleAiBtn" class="mode-btn mode-ai" data-mode="ai">
      <svg class="ico ico-sm"><use href="#ico-zap"/></svg>
      <span>@AI 草稿</span>
    </button>
  </div>
  <textarea id="composerInput" placeholder="输入消息…"></textarea>
  ...
```

**Step 2: Add toggle CSS to index.html**

```css
.composer-mode {
  display: flex;
  gap: 6px;
  margin-bottom: 10px;
}
.mode-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  font-size: 12px;
  border-radius: 8px;
  border: 1px solid var(--line-strong);
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  transition: all .15s ease;
}
.mode-btn.active.mode-text {
  background: var(--accent-dim);
  border-color: var(--accent-glow);
  color: var(--accent);
}
.mode-btn.active.mode-ai {
  background: var(--purple-dim);
  border-color: rgba(123,97,255,.4);
  color: var(--purple);
}
.mode-btn:not(.active):hover { background: rgba(255,255,255,.04); }
```

**Step 3: Add mode state and toggle handler in chat.js**

Add to the state object (~line 19):
```js
composerMode: 'text', // 'text' | 'ai'
```

Add toggle handler in the initialization section (near where other button listeners are bound):

```js
$('modeToggleBtn')?.addEventListener('click', () => {
  state.composerMode = 'text'
  $('modeToggleBtn').classList.add('active')
  $('modeToggleAiBtn').classList.remove('active')
  $('composerInput').placeholder = '输入消息…'
})
$('modeToggleAiBtn')?.addEventListener('click', () => {
  state.composerMode = 'ai'
  $('modeToggleAiBtn').classList.add('active')
  $('modeToggleBtn').classList.remove('active')
  $('composerInput').placeholder = '输入内容，将在本地生成 AI 草稿'
})
```

**Step 4: Modify send logic to use mode**

In the form submit handler, find where `isAiRequestContent(content)` is checked. The current logic reads the `@AI` prefix from the text. Change it to also check `state.composerMode`:

```js
// BEFORE (approximate):
const isAiRequest = isAiRequestContent(content)
// AFTER:
const isAiRequest = state.composerMode === 'ai' || isAiRequestContent(content)
// Strip @AI prefix if mode-based (user didn't type @AI manually):
const sendContent = (state.composerMode === 'ai' && !isAiRequestContent(content))
  ? `@AI ${content}`
  : content
```

Use `sendContent` instead of `content` when building the API payload.

**Step 5: Write failing test**

```js
test('@AI mode toggle changes placeholder and state', async ({ page }) => {
  await page.goto(baseUrl + '/dev_harness.html?harness=1')
  const aiBtn = page.locator('#modeToggleAiBtn')
  await aiBtn.click()
  const placeholder = await page.locator('#composerInput').getAttribute('placeholder')
  expect(placeholder).toContain('AI 草稿')
  await expect(aiBtn).toHaveClass(/active/)
})
```

**Step 6: Run → FAIL → implement → run → PASS**

```bash
npx playwright test test_renderer_harness.spec.mjs --grep "mode toggle" -v
```

**Step 7: Commit**

```bash
git add paramind/apps/desktop/renderer/index.html paramind/apps/desktop/renderer/chat.js test/backend/test_renderer_harness.spec.mjs
git commit -m "feat(frontend): add @AI mode toggle button in composer"
```

---

## Task 4: New Message Scroll Indicator

**Why:** `userScrolledUpByConversation` is tracked but never shown to the user. When scrolled up, new messages arrive silently.

**Files:**
- Modify: `paramind/apps/desktop/renderer/index.html` — add indicator HTML + CSS
- Modify: `paramind/apps/desktop/renderer/chat.js:1177–1200` area — trigger show/hide

**Step 1: Add indicator HTML in index.html**

Inside `<main class="main">`, after the `<div class="messages" id="messageList">` closing tag:

```html
<button type="button" id="scrollIndicator" class="scroll-indicator" hidden>
  <svg class="ico"><use href="#ico-activity"/></svg>
  <span id="scrollIndicatorCount">1</span> 条新消息
</button>
```

**Step 2: Add indicator CSS**

```css
.scroll-indicator {
  position: absolute;
  bottom: 160px;
  right: 40px;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 9px 16px;
  border-radius: 999px;
  background: var(--accent);
  color: var(--bg);
  border: none;
  font-size: 12.5px;
  font-family: var(--font-mono);
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 4px 20px rgba(0,212,170,.4);
  animation: slide-up .2s ease;
  z-index: 10;
}
.scroll-indicator[hidden] { display: none !important; }
.scroll-indicator:hover { transform: translateY(-2px); box-shadow: 0 6px 28px rgba(0,212,170,.5); }
```

The `.main` needs `position: relative` — add to `.main` CSS:
```css
.main {
  ...
  position: relative;
}
```

**Step 3: Add indicator state and update logic in chat.js**

Add to state:
```js
unreadWhileScrolledUp: new Map(), // conversationId → count
```

Add helper:
```js
function updateScrollIndicator() {
  const conversationId = state.activeConversationId
  const scrolledUp = state.userScrolledUpByConversation.get(conversationId)
  const unread = state.unreadWhileScrolledUp.get(conversationId) || 0
  const indicator = $('scrollIndicator')
  if (!indicator) return
  if (scrolledUp && unread > 0) {
    $('scrollIndicatorCount').textContent = unread
    indicator.hidden = false
  } else {
    indicator.hidden = true
    if (!scrolledUp) state.unreadWhileScrolledUp.set(conversationId, 0)
  }
}
```

In `patchMessageNode` (line ~1188), after the new message is appended, if the pane is currently active:
```js
// After appending new node:
if (conversationId === state.activeConversationId) {
  const scrolledUp = state.userScrolledUpByConversation.get(conversationId)
  if (scrolledUp) {
    const count = (state.unreadWhileScrolledUp.get(conversationId) || 0) + 1
    state.unreadWhileScrolledUp.set(conversationId, count)
    updateScrollIndicator()
  }
}
```

Add click handler in init:
```js
$('scrollIndicator')?.addEventListener('click', () => {
  const list = $('messageList')
  list.scrollTop = list.scrollHeight
  state.unreadWhileScrolledUp.set(state.activeConversationId, 0)
  updateScrollIndicator()
})
```

In the scroll event listener (~line 1485), after updating `userScrolledUpByConversation`, call:
```js
updateScrollIndicator()
```

**Step 4: Write failing test**

```js
test('scroll indicator appears when scrolled up and new message arrives', async ({ page }) => {
  await page.goto(baseUrl + '/dev_harness.html?harness=1')
  // Scroll to top
  await page.locator('#messageList').evaluate(el => { el.scrollTop = 0 })
  // Inject a new message via harness
  await page.evaluate(() => window.__harness?.injectMessage?.({
    id: 'scroll-test-1', role: 'assistant', sender_id: 'assistant',
    sender_name: 'AI', content: 'hello', status: 'completed',
  }))
  await expect(page.locator('#scrollIndicator')).toBeVisible()
})
```

**Step 5: Run → FAIL → implement → run → PASS**

```bash
npx playwright test test_renderer_harness.spec.mjs --grep "scroll indicator" -v
```

**Step 6: Commit**

```bash
git add paramind/apps/desktop/renderer/index.html paramind/apps/desktop/renderer/chat.js test/backend/test_renderer_harness.spec.mjs
git commit -m "feat(frontend): add scroll-to-new-messages indicator"
```

---

## Task 5: Streaming Typing Bubble

**Why:** Before the first token arrives, there's a blank card with no visual indication AI is working. This creates uncertainty for users.

**Files:**
- Modify: `paramind/apps/desktop/renderer/index.html` — typing indicator CSS
- Modify: `paramind/apps/desktop/renderer/chat.js:1305–1340` — streaming initiation

**Step 1: Add typing indicator CSS in index.html**

```css
@keyframes typing-dot {
  0%, 80%, 100% { transform: translateY(0); opacity: .4; }
  40% { transform: translateY(-5px); opacity: 1; }
}
.typing-indicator {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 0;
  height: 20px;
}
.typing-indicator span {
  width: 6px; height: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: typing-dot 1.2s ease-in-out infinite;
}
.typing-indicator span:nth-child(2) { animation-delay: .15s; }
.typing-indicator span:nth-child(3) { animation-delay: .3s; }
```

**Step 2: Modify streaming initiation in chat.js**

Find the section around line 1305–1335 where a new streaming message node is created. The current flow:
1. Creates message node via `renderTimelineCard` (renders empty/blank)
2. Appends to pane
3. Sets `contentNode.dataset.streamingStarted = '1'`

Change: when `streamingStarted` is not yet set and content is empty, render a typing indicator placeholder instead of the blank card content:

```js
// In the streaming content update path (~line 1228-1232):
if (message.metadata?.local_draft === true && message.status === 'streaming') {
  if (!contentNode.dataset.streamingStarted && !message.content) {
    contentNode.innerHTML = '<span class="typing-indicator"><span></span><span></span><span></span></span>'
  } else {
    contentNode.dataset.streamingStarted = '1'
    contentNode.textContent = message.content || ''
  }
} else if (!contentNode.dataset.streamingStarted) {
  if (!message.content) {
    contentNode.innerHTML = '<span class="typing-indicator"><span></span><span></span><span></span></span>'
  } else {
    contentNode.textContent = message.content || ''
  }
}
```

Also in the token-append path (~line 1333), clear the typing indicator on first token:
```js
// BEFORE:
contentNode.textContent = message.content || `${contentNode.textContent || ''}${token}`
// AFTER:
if (!contentNode.dataset.streamingStarted) {
  contentNode.dataset.streamingStarted = '1'
  contentNode.textContent = ''
}
contentNode.textContent = message.content || `${contentNode.textContent || ''}${token}`
```

**Step 3: Write failing test**

```js
test('shows typing indicator during streaming before first token', async ({ page }) => {
  await page.goto(baseUrl + '/dev_harness.html?harness=1')
  await page.evaluate(() => window.__harness?.injectMessage?.({
    id: 'stream-test-1', role: 'assistant', sender_id: 'assistant',
    sender_name: 'AI', content: '', status: 'streaming',
  }))
  await expect(page.locator('.typing-indicator')).toBeVisible()
})
```

**Step 4: Run → FAIL → implement → run → PASS**

```bash
npx playwright test test_renderer_harness.spec.mjs --grep "typing indicator" -v
```

**Step 5: Commit**

```bash
git add paramind/apps/desktop/renderer/index.html paramind/apps/desktop/renderer/chat.js test/backend/test_renderer_harness.spec.mjs
git commit -m "feat(frontend): add typing indicator during AI streaming"
```

---

## Task 6: Route SVG Visualization

**Why:** Inference route is Paramind's core differentiator. The current list-of-cards is invisible. A mini animated graph makes demos memorable.

**Files:**
- Modify: `paramind/apps/desktop/renderer/index.html` — SVG container + CSS
- Modify: `paramind/apps/desktop/renderer/chat.js:1652–1673` — `renderRoute()` rewrite

**Step 1: Add SVG route container CSS in index.html**

Replace the current `.route-node` styles (lines ~503–531) with:

```css
.route-viz {
  padding: 14px 16px;
}
.route-svg { width: 100%; height: auto; overflow: visible; }
.route-viz-node {
  cursor: default;
}
.route-viz-node circle {
  transition: r .2s ease;
}
.route-viz-node:hover circle { r: 9; }
.route-flow-line {
  stroke-dasharray: 6 4;
  animation: route-flow 1.2s linear infinite;
}
@keyframes route-flow {
  from { stroke-dashoffset: 0; }
  to { stroke-dashoffset: -20; }
}
.route-flow-line.idle { animation: none; stroke-dasharray: none; opacity: .3; }
```

**Step 2: Rewrite renderRoute() in chat.js**

Replace the entire `renderRoute` function (lines 1652–1673):

```js
function renderRoute() {
  const route = state.lastRouteByConversation.get(state.activeConversationId) || []
  const signature = JSON.stringify(route.map((n) => `${n.id}:${n.layers}:${n.latency_ms}`))
  if (signature === state.renderedRouteSignature) return
  state.renderedRouteSignature = signature

  const routeText = $('routeText')
  if (route.length) {
    routeText.textContent = route.map((node) => `${node.id}:${node.layers}`).join(' → ')
  } else {
    routeText.textContent = 'No route yet'
  }

  const container = $('routeList')
  if (!route.length) {
    container.innerHTML = '<div class="empty" style="font-size:12px;">AI route will appear here after generation starts.</div>'
    return
  }

  const isActive = !!state.activeJobId
  const nodeCount = route.length
  const svgW = 220
  const nodeSpacing = Math.min(60, Math.floor((svgW - 40) / Math.max(nodeCount - 1, 1)))
  const svgH = 80

  const nodes = route.map((node, i) => ({
    ...node,
    x: 20 + i * nodeSpacing,
    y: 40,
    color: node.local ? 'var(--accent)' : 'var(--purple)',
    glow: node.local ? 'rgba(0,212,170,.5)' : 'rgba(123,97,255,.5)',
  }))

  const lines = nodes.slice(0, -1).map((n, i) => `
    <line
      class="route-flow-line${isActive ? '' : ' idle'}"
      x1="${n.x + 8}" y1="${n.y}"
      x2="${nodes[i + 1].x - 8}" y2="${nodes[i + 1].y}"
      stroke="${isActive ? 'var(--accent)' : 'var(--muted)'}"
      stroke-width="1.5"
      fill="none"
    />
  `).join('')

  const nodesSvg = nodes.map((n) => `
    <g class="route-viz-node" transform="translate(${n.x},${n.y})">
      <circle r="7" fill="${n.color}" opacity=".18"/>
      <circle r="5" fill="${n.color}" ${n.local && isActive ? `style="filter:drop-shadow(0 0 6px ${n.glow})"` : ''}/>
      <text y="22" text-anchor="middle" fill="var(--muted)" font-size="8.5" font-family="var(--font-mono)">
        ${escapeHtml((n.display_name || n.id).slice(0, 6))}
      </text>
      <text y="31" text-anchor="middle" fill="var(--muted)" font-size="7.5" font-family="var(--font-mono)" opacity=".6">
        ${escapeHtml(n.layers || '')}
      </text>
    </g>
  `).join('')

  container.innerHTML = `
    <div class="route-viz">
      <svg class="route-svg" viewBox="0 0 ${svgW} ${svgH}">
        ${lines}
        ${nodesSvg}
      </svg>
    </div>
  `
}
```

**Step 3: Write failing test**

```js
test('route visualization renders SVG when route is set', async ({ page }) => {
  await page.goto(baseUrl + '/dev_harness.html?harness=1')
  await page.evaluate(() => window.__harness?.setRoute?.([
    { id: 'node-a', layers: '0-12', local: true, latency_ms: 0 },
    { id: 'node-b', layers: '12-24', local: false, latency_ms: 42 },
  ]))
  const svg = page.locator('#routeList svg')
  await expect(svg).toBeVisible()
  const circles = page.locator('#routeList circle')
  await expect(circles).toHaveCount(4) // 2 nodes × 2 circles each
})
```

**Step 4: Check harness supports `setRoute`**

Check `dev_harness.js` — if `setRoute` is not exposed on `window.__harness`, add it:

```js
// In dev_harness.js, in the harness API object:
setRoute(nodes) {
  if (typeof state !== 'undefined') {
    state.lastRouteByConversation.set(state.activeConversationId, nodes)
    renderRoute()
  }
}
```

**Step 5: Run → FAIL → implement → run → PASS**

```bash
npx playwright test test_renderer_harness.spec.mjs --grep "route visualization" -v
```

**Step 6: Commit**

```bash
git add paramind/apps/desktop/renderer/index.html paramind/apps/desktop/renderer/chat.js paramind/apps/desktop/renderer/dev_harness.js test/backend/test_renderer_harness.spec.mjs
git commit -m "feat(frontend): SVG inference route visualization with animated data flow"
```

---

## Final Integration Test

After all tasks complete:

```bash
cd /Users/acropolis/Github_Project/Paramind/paramind/apps/desktop
npm run test:frontend-fast
```

Expected: all tests pass.

```bash
cd /Users/acropolis/Github_Project/Paramind
make test-frontend-fast
```

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 0 | — | — |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |

**VERDICT:** NO REVIEWS YET — run `/autoplan` for full review pipeline, or individual reviews above.
