# ParaMind UI Redesign — Design Document

**Date:** 2026-03-24
**Status:** Approved
**Scope:** `paramind/apps/desktop/renderer/index.html` + `chat.js` + `backend.py` (SSE format only)

---

## Concept & Vision

> **"Network monitor meets normal chat"** — A distributed inference system that feels like a group chat, not an AI product. Node topology is decorative language; interaction is natural WeChat/Telegram-style messaging.

AI is a normal member of the group, not a special "assistant" with product branding. The interface should feel like infrastructure in the background, not technology on display.

---

## Design Language

### Aesthetic Direction
Infrastructure/observability aesthetic — dark, precise, cool-toned. Think network topology maps meets GitHub Dark. Every element earns its place; no decorative AI branding.

### Color Palette

| Token | Hex | Usage |
|---|---|---|
| `--bg` | `#080b10` | Deep space background |
| `--panel` | `#0d1117` | Panel surfaces (GitHub Dark) |
| `--panel-2` | `#161b22` | Nested panels |
| `--accent` | `#58a6ff` | Link blue — nodes, connections |
| `--accent-2` | `#3fb950` | Node green — online/active state |
| `--line` | `rgba(255,255,255,.09)` | Borders, dividers |
| `--bubble` | `#21262d` | Normal message bubble |
| `--ai-bubble` | `#1c2d3e` | AI bubble (barely perceptible blue-grey shift) |
| `--text` | `#e6edf3` | Primary text |
| `--muted` | `#8b949e` | Secondary text |

### Typography

- **Display/headings:** `JetBrains Mono` — monospace, technical, for names/node IDs/topology labels
- **Body:** `IBM Plex Sans` — clear, modern, for message content and UI

### Motion Philosophy

**B — Tech pulse:** Nodes breathe (opacity 0.5→1.0, 1.5s ease-in-out infinite) during generation; data flow animation via CSS `background-position` marching dots (left-to-right, 12px dash beat).

- Token typewriter: `▋` cursor blink at end, 300ms interval
- Draft card appears: 150ms slide-up + fade-in
- Draft card resolves: 150ms fade-out, smooth
- Pulse stops on `done` event, topology becomes static dots

### Spatial System
- Border radius: `14px` (messages), `10px` (inputs, buttons), `12px` (cards)
- Spacing unit: `8px` base grid
- Sidebar width: `320px` default, resizable 72–520px

---

## Layout & Structure

### Left Sidebar
- User identity: avatar dot + name + "online" indicator
- Conversation list: group/DM sections, unread badges
- Online members grid: small avatar dots (same visual language as topology nodes)
- "Active nodes" label replaces "online members" section label

### Main Chat Area
- Topbar: room name + member count + search toggle
- Status bar: model, mode, device, TTFT, TPS (replaces mock text)
- Message list: standard bubbles, AI messages have `--ai-bubble` bg
- **Draft card:** floats at bottom of chat like "someone is typing", not a separate fixed panel

### AI Draft Card (Core Differentiator)
- Appears as a floating message at the bottom of chat stream
- Looks like a normal outgoing message being typed (semi-transparent, blinking cursor)
- Top border separator (`1px --line`)
- Meta row: `[avatar] AI ···· [pulsing dot] · generating...`
- Nodes show as: `node1 ·───⟩ node2` in JetBrains Mono 10px `--accent`
- Typewriter cursor `▋` blinks at text end
- On `done`: text solidifies, pulse stops, 150ms fade-out, card disappears
- No "copy/publish/retry/delete" button bar — primary action is implicit publish after brief display

### AI Message (After Publish)
- Bubble identical to normal message (same radius, shadow, spacing)
- AI bubble barely perceptible: `--ai-bubble` vs `--bubble`, ~5% lightness shift
- Meta has tiny topology: `node1 ·───⟩ node2` (JetBrains Mono 10px `--accent`)
- Single-node mode: just a small `·` dot

---

## Features & Interactions

### Core Flow
1. User types `@AI 问题时` in composer, sends
2. Message appears in chat (normal bubble)
3. Draft card floats up at chat bottom with pulse animation
4. Tokens stream in with typewriter effect
5. On complete: brief display (~800ms), then auto-publish to chat
6. Draft fades out, AI message sits in chat with subtle topology marker

### SSE Event Format
```
event: route
data: {"nodes":[{"id":"node-A","layers":"0-12"},{"id":"node-B","layers":"12-24"}]}

event: token
data: {"token":"He","ttft":0.234}

event: done
data: {"tokens":42,"elapsed":3.21,"tps":13.1}
```

### Interactions
- **Click online member** → opens DM conversation
- **Hover AI message** → tooltip shows full route path (node IDs + layer ranges)
- **In-chat search** → highlights matching messages, `Esc` clears
- **Resize panels** → all splitters persisted to localStorage

### Error Handling
- Backend unavailable → fallback mock reply, no error shown
- Inference failure → draft shows error text in muted color, auto-dismisses after 3s

---

## Component Inventory

### Message Bubble
- States: normal, AI (barely different), generating (draft)
- Normal: `--bubble` bg, `--line` border, `var(--shadow)`
- AI: `--ai-bubble` bg (very subtle)
- Generating: 60% opacity, typewriter cursor

### Draft Card
- States: generating (pulse + typewriter), complete (solid), error
- Appears with 150ms slide-up fade-in
- Resolves with 150ms fade-out

### Topology Node Indicator
- Small inline SVG/CSS: `●───⟩●` where dashes animate when generating
- JetBrains Mono 10px `--accent` color
- Breathing pulse on active node dot (CSS animation)

### Member Avatar
- 34px circle with initial letter
- Online: green dot indicator (same as topology node color)
- Consistent with topology node visual language

### Status Bar
- Fixed below topbar
- Shows: model ID, mode (local/distributed), device, TTFT, TPS
- Monospace, 11px, muted color

---

## Technical Approach

### Frontend Stack
- Vanilla JS (no framework) — same as existing
- Google Fonts CDN: JetBrains Mono, IBM Plex Sans
- CSS custom properties for theming
- localStorage for splitter persistence

### Backend SSE Contract
- `POST /api/infer` returns `text/event-stream`
- Events: `route`, `token`, `done`
- Mock mode (test mode) returns synthetic SSE for UI testing without model

### File Changes
- `paramind/apps/desktop/renderer/index.html` — fonts, CSS vars, draft card HTML slot
- `paramind/apps/desktop/renderer/chat.js` — state machine, SSE handler, topology render, typewriter
- `paramind/apps/desktop/python/backend.py` — SSE endpoint with `event:` format (minimal change)

### Mock Fallback
When backend unreachable, `fallbackMockReply` generates a simple mock response without changing visual behavior (same draft → publish flow).
