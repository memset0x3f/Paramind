# Frontend Card & Message State Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the desktop renderer product-usable by replacing the current ad-hoc timeline rendering with clear card/state handling for user, peer, AI draft, published AI, and DM request flows.

**Architecture:** Keep the existing FastAPI + SQLite + localhost coordinator backend intact and refactor only the renderer state/view layer first. The renderer should move from generic `role`-based rendering to explicit card kinds and status-aware view models so that DM flows, `@AI` draft flows, and delivery/read states are rendered predictably without mixing diagnostic noise into the timeline.

**Tech Stack:** Electron renderer (plain HTML/CSS/JS), FastAPI SSE backend, SQLite-backed event stream, Node test runner, pytest for backend verification.

---

### Task 1: Add a failing renderer-state test for card classification

**Files:**
- Modify: `test/backend/test_chat_state.mjs`
- Reference: `paramind/apps/desktop/renderer/chat_state.js`

**Step 1: Write the failing test**

Add a test that feeds `chat_state.js` a mix of:
- normal user message
- peer message
- local AI draft (`metadata.local_draft = true`)
- published AI message (`role = assistant`, `sender_name = AI`, `metadata.published_from_draft = true`)
- DM request synthetic event

The test should assert that the reducer/classifier produces distinct card kinds for:
- `message.user`
- `message.peer`
- `message.ai-draft`
- `message.ai`
- `message.request`

Use explicit assertions instead of snapshotting the whole state.

**Step 2: Run test to verify it fails**

Run:
```bash
node --test test/backend/test_chat_state.mjs
```

Expected: FAIL because `chat_state.js` does not yet expose a stable card-kind classifier.

**Step 3: Write minimal implementation**

In `paramind/apps/desktop/renderer/chat_state.js`, add a helper like:

```js
function getCardKind(message) {
  if (message.metadata?.local_draft) return 'message.ai-draft'
  if (message.kind === 'dm.request') return 'message.request'
  if (message.role === 'assistant' && message.sender_name === 'AI') return 'message.ai'
  if (message.role === 'peer') return 'message.peer'
  if (message.role === 'user') return 'message.user'
  return 'message.system-chat'
}
```

Export it on `chatStateApi`.

**Step 4: Run test to verify it passes**

Run:
```bash
node --test test/backend/test_chat_state.mjs
```

Expected: PASS.

**Step 5: Commit**

Per repo rule, do **not** commit unless the user explicitly asks.

---

### Task 2: Refactor timeline rendering to use card kinds instead of implicit role branches

**Files:**
- Modify: `paramind/apps/desktop/renderer/chat.js`
- Reference: `paramind/apps/desktop/renderer/chat_state.js`
- Test: `test/backend/test_chat_state.mjs`

**Step 1: Write the failing test**

Extend `test_chat_state.mjs` with a small test that asserts published AI messages are **not** treated as drafts and that DM requests map to request cards.

**Step 2: Run test to verify it fails**

Run:
```bash
node --test test/backend/test_chat_state.mjs
```

Expected: FAIL until the renderer uses card kinds consistently.

**Step 3: Write minimal implementation**

In `paramind/apps/desktop/renderer/chat.js`:
- Add a small `getRenderableCard(message)` helper that calls `CHAT_STATE.getCardKind(message)`.
- Replace the current `renderTimelineCard()` branch tree with explicit renderers:
  - `renderUserMessageCard(message)`
  - `renderPeerMessageCard(message)`
  - `renderPublishedAIMessageCard(message)`
  - `renderDraftCard(message, conversationId)`
  - `renderRequestConversation(request)` or `renderRequestCard(message)`
  - `renderSystemMessageCard(message)`
- Keep markup small; do not introduce a framework.

Representative shape:

```js
function renderTimelineCard(message, conversationId) {
  switch (CHAT_STATE.getCardKind(message)) {
    case 'message.user':
      return renderUserMessageCard(message)
    case 'message.peer':
      return renderPeerMessageCard(message)
    case 'message.ai':
      return renderPublishedAIMessageCard(message, conversationId)
    case 'message.ai-draft':
      return renderDraftCard(message, conversationId)
    default:
      return renderSystemMessageCard(message)
  }
}
```

**Step 4: Run test to verify it passes**

Run:
```bash
node --check paramind/apps/desktop/renderer/chat.js
node --test test/backend/test_chat_state.mjs
```

Expected: syntax check passes; tests pass.

**Step 5: Commit**

Do not commit unless user explicitly asks.

---

### Task 3: Surface delivery/read state clearly for normal and AI messages

**Files:**
- Modify: `paramind/apps/desktop/renderer/chat.js`
- Modify: `paramind/apps/desktop/renderer/index.html`
- Reference: `paramind/apps/desktop/python/app/services.py`

**Step 1: Write the failing test**

Add a renderer-state test that verifies a message with status `read` stays visually distinct from `sent`, and that a draft remains labeled `streaming/completed` locally.

**Step 2: Run test to verify it fails**

Run:
```bash
node --test test/backend/test_chat_state.mjs
```

Expected: FAIL because status display is not yet normalized by card type.

**Step 3: Write minimal implementation**

In `paramind/apps/desktop/renderer/chat.js`:
- Add a status label helper:

```js
function formatStatus(message) {
  if (message.metadata?.local_draft) return message.status || 'pending'
  if (message.role === 'assistant' && message.sender_name === 'AI') return message.status || 'completed'
  return message.status || 'sent'
}
```

- Use it in all message card headers.
- In `paramind/apps/desktop/renderer/index.html`, add minimal CSS hooks for `sent`, `delivered`, `read`, `failed`, `streaming` badges; do not redesign the page.

**Step 4: Run test to verify it passes**

Run:
```bash
node --check paramind/apps/desktop/renderer/chat.js
node --test test/backend/test_chat_state.mjs
```

Expected: PASS.

**Step 5: Commit**

Do not commit unless user explicitly asks.

---

### Task 4: Make DM request cards and peer actions mutually consistent

**Files:**
- Modify: `paramind/apps/desktop/renderer/chat.js`
- Reference: `paramind/apps/desktop/python/app/services.py`
- Reference: `test/backend/test_api.py`

**Step 1: Write the failing test**

Add a small frontend-state test that covers this sequence:
- no DM -> peer button says request DM
- pending outbound request -> button says waiting
- pending inbound request -> button says respond
- accepted DM exists -> button says open DM

If a dedicated peer-button test feels too coupled, add a pure helper test in `test_chat_state.mjs` for peer action selection.

**Step 2: Run test to verify it fails**

Run:
```bash
node --test test/backend/test_chat_state.mjs
```

Expected: FAIL until peer action selection is extracted and stabilized.

**Step 3: Write minimal implementation**

In `paramind/apps/desktop/renderer/chat.js`:
- Extract `getPeerCardAction(peer)` into a pure helper or move a minimal pure version into `chat_state.js`.
- Ensure request cards and peer list use the same source of truth.
- Keep DM title logic derived from counterpart peer id, not stored title.

**Step 4: Run test to verify it passes**

Run:
```bash
node --check paramind/apps/desktop/renderer/chat.js
node --test test/backend/test_chat_state.mjs
```

Expected: PASS.

**Step 5: Commit**

Do not commit unless user explicitly asks.

---

### Task 5: Add a backend regression test for published AI messages syncing as formal assistant messages

**Files:**
- Modify: `test/backend/test_api.py`
- Reference: `paramind/apps/desktop/python/app/services.py`

**Step 1: Write the failing test**

Add to the existing AI lifecycle test or a new one:
- two instances, A and B
- A sends `@AI ...`
- A creates draft and publishes it
- B receives a formal assistant message with:
  - `role == assistant`
  - `sender_name == 'AI'`
  - `metadata.published_from_draft == true`
- B never sees the local draft

**Step 2: Run test to verify it fails**

Run:
```bash
cd . && uv run --project paramind/apps/desktop --with pytest python -m pytest test/backend/test_api.py -q --tb=short -k published_ai
```

Expected: FAIL if any sync edge case remains.

**Step 3: Write minimal implementation**

Only if needed. Prefer fixing `services.py` metadata propagation or broadcast filtering in the smallest possible way.

**Step 4: Run test to verify it passes**

Run:
```bash
cd . && uv run --project paramind/apps/desktop --with pytest python -m pytest test/backend/test_api.py -q --tb=short -k published_ai
```

Expected: PASS.

**Step 5: Commit**

Do not commit unless user explicitly asks.

---

### Task 6: Run focused end-to-end verification for the renderer/product flow

**Files:**
- No new code required unless failures are found
- Verify against:
  - `paramind/apps/desktop/renderer/chat.js`
  - `paramind/apps/desktop/renderer/chat_state.js`
  - `paramind/apps/desktop/python/app/services.py`
  - `inference/ShardLoader.py`

**Step 1: Run renderer tests**

```bash
node --test test/backend/test_chat_state.mjs
```

Expected: PASS.

**Step 2: Run backend AI lifecycle test**

```bash
cd . && uv run --project paramind/apps/desktop --with pytest python -m pytest test/backend/test_api.py -q --tb=short -k 'ai_draft_lifecycle or dm_request_accept_flow or message_ack'
```

Expected: PASS.

**Step 3: Run inference cache regression test**

```bash
cd . && uv run --project paramind/apps/desktop --with pytest python -m pytest test/inference/test_shard_loader.py -q --tb=short -k current_transformers_kwarg_names
```

Expected: PASS.

**Step 4: Run compile check**

```bash
cd . && uv run --project paramind/apps/desktop python -m compileall inference paramind/apps/desktop/python
node --check paramind/apps/desktop/renderer/chat.js
node --check paramind/apps/desktop/renderer/chat_state.js
```

Expected: all checks succeed.

**Step 5: Manual smoke**

Open one or two Electron instances and verify:
- ordinary message sends normally
- `@AI` appears as user request message
- AI draft streams locally and finishes
- editing/copying/deleting draft works
- sending draft creates a formal `AI` message
- DM pending/accept/open states are coherent

**Step 6: Commit**

Do not commit unless the user explicitly asks.

