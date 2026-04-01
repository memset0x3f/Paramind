import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import playwrightTest from '../../paramind/apps/desktop/node_modules/@playwright/test/index.js'
const { test, expect } = playwrightTest

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const rendererDir = path.resolve(__dirname, '../../paramind/apps/desktop/renderer')
const baseUrl = 'http://127.0.0.1:4173'
let serverProcess = null

function waitForServer(url, timeoutMs = 5000) {
  const start = Date.now()
  return new Promise((resolve, reject) => {
    function attempt() {
      const request = http.get(url, (response) => {
        response.resume()
        resolve()
      })
      request.on('error', () => {
        if (Date.now() - start > timeoutMs) {
          reject(new Error(`Timed out waiting for ${url}`))
          return
        }
        setTimeout(attempt, 100)
      })
    }
    attempt()
  })
}

test.beforeAll(async () => {
  serverProcess = spawn('python3', ['-m', 'http.server', '4173'], {
    cwd: rendererDir,
    stdio: 'ignore',
  })
  await waitForServer(`${baseUrl}/dev_harness.html`)
})

test.afterAll(() => {
  serverProcess?.kill('SIGTERM')
})

async function openHarness(page, fixture) {
  const suffix = fixture ? `?fixture=${encodeURIComponent(fixture)}` : ''
  await page.goto(`${baseUrl}/dev_harness.html${suffix}`)
}

test('sidebar updates on global lifecycle events without booting Electron', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=bootstrap_two_peers`)
  await page.getByRole('button', { name: '请求私聊' }).click()
  await expect(page.locator('#conversationList')).toContainText('私聊请求')
})

test('accepted dm fixture resolves to an active dm selection', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=accepted_dm`)
  const activeConversationId = await page.evaluate(() => window.ParaMindHarness.getSidebarCards()[0].id)
  const selectedConversation = await page.evaluate(() => {
    const cards = window.ParaMindHarness.getSidebarCards()
    return cards.find((item) => item.kind === 'conversation')?.id || null
  })
  expect(activeConversationId).toBe('dm:peer-a-peer-b')
  expect(selectedConversation).toBe('dm:peer-a-peer-b')
  await expect(page.locator('#peerList')).toContainText('退出 DM')
})

test('leaving an active dm updates local shell state immediately without waiting for a stream event', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=accepted_dm`)
  await expect(page.locator('#roomTitle')).toContainText('Peer B')
  await expect(page.locator('#peerList')).toContainText('退出 DM')

  await page.getByRole('button', { name: /退出 DM/ }).click()

  await expect(page.locator('#roomTitle')).toContainText('General')
  await expect(page.locator('#peerList')).toContainText('请求私聊')
  await expect(page.locator('#conversationList')).not.toContainText('Peer B')
})

test('accepting a pending dm request leaves the request view and opens the dm without refresh', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=pending_dm_request`)
  await page.locator('#conversationList [data-conversation-id="request:req-1"]').click()
  await expect(page.locator('#roomTitle')).toContainText('私聊请求')

  await page.evaluate(() => {
    window.ParaMindHarness.emitGlobalEvent({
      type: 'dm.accepted',
      payload: {
        request: {
          id: 'req-1',
          requester_id: 'peer-a',
          target_peer_id: 'peer-b',
          direction: 'outbound',
          status: 'accepted',
          created_at: '2026-03-31T09:01:00Z',
          updated_at: '2026-03-31T09:02:00Z',
        },
        conversation: {
          id: 'dm:peer-a-peer-b',
          title: 'Peer B',
          kind: 'dm',
          updated_at: '2026-03-31T09:02:00Z',
          participant_ids: ['peer-a', 'peer-b'],
          last_message: null,
        },
        relationship: {
          peer_id: 'peer-b',
          status: 'active_dm',
          conversation_id: 'dm:peer-a-peer-b',
          request_id: null,
        },
      },
    })
  })

  await expect(page.locator('#conversationList')).not.toContainText('DM request')
  await expect(page.locator('#roomTitle')).toContainText('Peer B')
  await expect(page.locator('#roomMeta')).toContainText('dm · 2 participants')
  await expect(page.locator('#peerList')).toContainText('退出 DM')
})

test('rejecting a pending dm request leaves the request view and restores peer action state', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=pending_dm_request`)
  await page.locator('#conversationList [data-conversation-id="request:req-1"]').click()
  await expect(page.locator('#roomTitle')).toContainText('私聊请求')

  await page.evaluate(() => {
    window.ParaMindHarness.emitGlobalEvent({
      type: 'dm.rejected',
      payload: {
        request: {
          id: 'req-1',
          requester_id: 'peer-a',
          target_peer_id: 'peer-b',
          direction: 'outbound',
          status: 'rejected',
          created_at: '2026-03-31T09:01:00Z',
          updated_at: '2026-03-31T09:02:00Z',
        },
      },
    })
  })

  await expect(page.locator('#conversationList')).not.toContainText('DM request')
  await expect(page.locator('#roomTitle')).toContainText('General')
  await expect(page.locator('#peerList')).toContainText('请求私聊')
})

test('active group conversation metadata updates when participant membership changes', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=bootstrap_two_peers`)
  await expect(page.locator('#roomMeta')).toContainText('group · 2 participants')

  await page.evaluate(() => {
    window.ParaMindHarness.emitGlobalEvent({
      type: 'conversation.updated',
      payload: {
        conversation: {
          id: 'general',
          title: 'General',
          kind: 'group',
          updated_at: '2026-03-31T09:05:00Z',
          participant_ids: ['peer-a', 'peer-b', 'peer-c'],
          last_message: null,
        },
      },
    })
  })

  await expect(page.locator('#roomMeta')).toContainText('group · 3 participants')
})

test('group accepted lifecycle event updates active group participant metadata without refresh', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=bootstrap_two_peers`)
  await expect(page.locator('#roomMeta')).toContainText('group · 2 participants')

  await page.evaluate(() => {
    window.ParaMindHarness.emitGlobalEvent({
      type: 'group.accepted',
      payload: {
        invitation: {
          id: 'invite-1',
          conversation_id: 'general',
          inviter_id: 'peer-a',
          target_peer_id: 'peer-c',
          title: 'General',
          status: 'accepted',
          direction: 'outbound',
          created_at: '2026-03-31T09:01:00Z',
          updated_at: '2026-03-31T09:06:00Z',
        },
        conversation: {
          id: 'general',
          title: 'General',
          kind: 'group',
          updated_at: '2026-03-31T09:06:00Z',
          participant_ids: ['peer-a', 'peer-b', 'peer-c'],
          last_message: null,
        },
      },
    })
  })

  await expect(page.locator('#roomMeta')).toContainText('group · 3 participants')
})

test('general participant count tracks peer join and leave events without refresh', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=bootstrap_two_peers`)
  await expect(page.locator('#roomMeta')).toContainText('group · 2 participants')

  await page.evaluate(() => {
    window.ParaMindHarness.emitGlobalEvent({
      type: 'peer.joined',
      payload: {
        peer: {
          id: 'peer-c',
          display_name: 'Peer C',
          status: 'online',
          backend_port: 5003,
        },
      },
    })
  })

  await expect(page.locator('#roomMeta')).toContainText('group · 3 participants')

  await page.evaluate(() => {
    window.ParaMindHarness.emitGlobalEvent({
      type: 'peer.left',
      payload: {
        peer: {
          id: 'peer-c',
          display_name: 'Peer C',
          status: 'offline',
          backend_port: 5003,
        },
      },
      entity_id: 'peer-c',
    })
  })

  await expect(page.locator('#roomMeta')).toContainText('group · 2 participants')
})

test('conversation preview updates do not force a shell metadata refresh', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=bootstrap_two_peers`)
  await expect(page.locator('#roomTitle')).toHaveText('General')
  await expect(page.locator('#roomMeta')).toContainText('group · 2 participants')

  const reuse = await page.evaluate(() => {
    const card = document.querySelector('#conversationList .conversation[data-conversation-id="general"]')
    const subtitle = card?.querySelector('.subtitle')
    const title = card?.querySelector('.title')

    window.ParaMindHarness.emitGlobalEvent({
      type: 'conversation.updated',
      payload: {
        conversation: {
          id: 'general',
          title: 'General',
          kind: 'group',
          updated_at: '2026-03-31T09:06:00Z',
          participant_ids: ['peer-a', 'peer-b'],
          last_message: {
            id: 'msg-general-2',
            conversation_id: 'general',
            role: 'peer',
            sender_name: 'Peer B',
            content: 'Preview changed only',
            status: 'sent',
            created_at: '2026-03-31T09:06:00Z',
            updated_at: '2026-03-31T09:06:00Z',
          },
        },
      },
    })

    const nextCard = document.querySelector('#conversationList .conversation[data-conversation-id="general"]')
    return {
      sameCard: card === nextCard,
      sameSubtitleNode: subtitle === nextCard?.querySelector('.subtitle'),
      sameTitleNode: title === nextCard?.querySelector('.title'),
    }
  })

  assert.equal(reuse.sameCard, true)
  assert.equal(reuse.sameSubtitleNode, true)
  assert.equal(reuse.sameTitleNode, true)
  await expect(page.locator('#conversationList')).toContainText('Preview changed only')
  await expect(page.locator('#roomTitle')).toHaveText('General')
  await expect(page.locator('#roomMeta')).toContainText('group · 2 participants')
})

test('streaming draft updates reuse the same message node and do not churn shell metadata', async ({ page }) => {
  await openHarness(page, 'ai_streaming_draft')
  const roomMeta = page.locator('#roomMeta')
  await expect(roomMeta).toContainText('group · 2 participants')

  const reusedNode = await page.evaluate(() => {
    const messageId = 'draft-1'
    const selector = `[data-message-id="${messageId}"]`
    const beforeNode = document.querySelector(selector)
    const beforeMeta = document.querySelector('#roomMeta')?.textContent || ''

    window.ParaMindHarness.emitConversationEvent({
      id: 999,
      type: 'message.updated',
      conversation_id: 'general',
      entity_id: messageId,
      payload: {
        message: {
          id: messageId,
          conversation_id: 'general',
          sender_id: 'assistant',
          sender_name: 'AI',
          role: 'assistant',
          status: 'streaming',
          content: '你好！很',
          metadata: { local_draft: true },
          created_at: '2026-03-31T14:04:27.000Z',
          updated_at: '2026-03-31T14:04:28.000Z',
        },
      },
    })

    const afterNode = document.querySelector(selector)
    const afterMeta = document.querySelector('#roomMeta')?.textContent || ''
    return {
      sameNode: beforeNode === afterNode,
      sameMeta: beforeMeta === afterMeta,
    }
  })

  assert.equal(reusedNode.sameNode, true)
  assert.equal(reusedNode.sameMeta, true)
  await expect(page.locator('[data-message-id="draft-1"] [data-message-content="draft-1"]')).toContainText('你好！很')
})

test('ai streaming fixture exposes draft state in the browser harness', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=ai_streaming_draft`)
  await expect.poll(async () => page.evaluate(() => {
    const draft = window.ParaMindHarness.getMessages('general').find((item) => item.id === 'draft-1')
    return draft?.content || ''
  })).toBe('Hello')
})

test('ai draft cards edit inline and save without prompt dialogs', async ({ page }) => {
  await openHarness(page, 'ai_streaming_draft')
  const draftId = 'draft-1'
  await expect(page.locator(`[data-message-id="${draftId}"] [data-message-content="${draftId}"]`)).toContainText('Hello')
  await page.evaluate((id) => {
    window.ParaMindHarness.emitConversationEvent({
      type: 'message.updated',
      conversation_id: 'general',
      entity_id: id,
      payload: {
        message: {
          id,
          conversation_id: 'general',
          sender_id: 'assistant',
          sender_name: 'AI',
          role: 'assistant',
          status: 'completed',
          content: '你好，原始草稿',
          metadata: { local_draft: true },
          created_at: '2026-03-31T09:02:00Z',
          updated_at: '2026-03-31T09:03:00Z',
        },
      },
    })
  }, draftId)
  await expect(page.locator(`[data-message-id="${draftId}"] [data-message-content="${draftId}"]`)).toContainText('你好，原始草稿')

  const dialogs = []
  page.on('dialog', async (dialog) => {
    dialogs.push(dialog.message())
    await dialog.dismiss()
  })

  await page.locator(`[data-message-id="${draftId}"] .draft-edit-btn`).click()

  await expect(page.locator(`[data-message-id="${draftId}"] textarea`)).toBeVisible()
  await expect.poll(() => dialogs.length).toBe(0)

  const editor = page.locator(`[data-message-id="${draftId}"] textarea`)
  await editor.fill('你好，已编辑草稿')
  await page.locator(`[data-message-id="${draftId}"] .draft-save-btn`).click()

  await expect(page.locator(`[data-message-id="${draftId}"] [data-message-content="${draftId}"]`)).toContainText('你好，已编辑草稿')
})

test('local ai drafts do not change the left preview until publish', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=bootstrap_two_peers`)
  await expect(page.locator('#conversationList .conversation .subtitle').first()).toHaveText('Welcome to ParaMind')

  const draftId = await page.evaluate(() => window.ParaMindHarness.createAssistantDraft('general').id)
  await page.evaluate((id) => window.ParaMindHarness.appendAssistantToken('general', id, 'Draft only'), draftId)

  await expect.poll(async () => page.evaluate(() => window.ParaMindHarness.getSidebarCards()[0].preview)).toBe('Welcome to ParaMind')

  await page.evaluate(() => window.ParaMindHarness.publishAssistantMessage('general', {
    id: 'ai-published-1',
    conversation_id: 'general',
    role: 'assistant',
    sender_name: 'AI',
    content: 'Published AI answer',
    status: 'completed',
    metadata: { published_from_draft: true },
    created_at: '2026-03-31T09:03:00Z',
    updated_at: '2026-03-31T09:03:00Z',
  }))

  await expect.poll(async () => page.evaluate(() => window.ParaMindHarness.getSidebarCards()[0].preview)).toBe('Published AI answer')
})

test('existing timeline nodes are reordered when a message update changes chronological position', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=bootstrap_two_peers`)
  await expect(page.locator('#messageList')).toContainText('Welcome to ParaMind')

  const getVisibleMessageIds = () => page.evaluate(() => {
    const visiblePane = Array.from(document.querySelectorAll('#messageList .timeline-pane'))
      .find((node) => !node.hidden)
    return Array.from(visiblePane?.querySelectorAll('[data-message-id]') || []).map((node) => node.dataset.messageId)
  })

  await expect.poll(async () => await getVisibleMessageIds()).toContain('msg-general-1')

  await page.evaluate(() => {
    window.ParaMindHarness.emitConversationEvent({
      type: 'message.created',
      conversation_id: 'general',
      entity_id: 'msg-late',
      payload: {
        message: {
          id: 'msg-late',
          conversation_id: 'general',
          role: 'peer',
          sender_name: 'Peer B',
          content: 'late arrival',
          status: 'sent',
          metadata: {},
          created_at: '2026-03-31T09:06:00Z',
          updated_at: '2026-03-31T09:06:00Z',
        },
      },
    })

    window.ParaMindHarness.emitConversationEvent({
      type: 'message.created',
      conversation_id: 'general',
      entity_id: 'msg-latest',
      payload: {
        message: {
          id: 'msg-latest',
          conversation_id: 'general',
          role: 'peer',
          sender_name: 'Peer B',
          content: 'latest arrival',
          status: 'sent',
          metadata: {},
          created_at: '2026-03-31T09:07:00Z',
          updated_at: '2026-03-31T09:07:00Z',
        },
      },
    })
  })

  await expect.poll(async () => await getVisibleMessageIds()).toEqual(['msg-general-1', 'msg-late', 'msg-latest'])

  await page.evaluate(() => {
    window.ParaMindHarness.emitConversationEvent({
      type: 'message.updated',
      conversation_id: 'general',
      entity_id: 'msg-latest',
      payload: {
        message: {
          id: 'msg-latest',
          conversation_id: 'general',
          role: 'peer',
          sender_name: 'Peer B',
          content: 'latest arrival',
          status: 'sent',
          metadata: {},
          created_at: '2026-03-31T09:05:00Z',
          updated_at: '2026-03-31T09:05:00Z',
        },
      },
    })
  })

  await expect.poll(async () => await getVisibleMessageIds()).toEqual(['msg-general-1', 'msg-latest', 'msg-late'])
})

test('conversation and message cards do not use mount animations that cause visible flicker on updates', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=bootstrap_two_peers`)
  const animations = await page.evaluate(() => {
    const conversation = document.querySelector('#conversationList .conversation')
    const message = document.querySelector('#messageList .message')
    return {
      conversationAnimationName: conversation ? getComputedStyle(conversation).animationName : null,
      messageAnimationName: message ? getComputedStyle(message).animationName : null,
    }
  })

  expect(animations.conversationAnimationName).toBe('none')
  expect(animations.messageAnimationName).toBe('none')
})

test('creating a group opens a peer picker and enters the created group for the creator', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=bootstrap_two_peers`)

  await page.getByRole('button', { name: '新建群聊' }).click()
  await expect(page.locator('#groupComposerModal')).toBeVisible()

  await page.locator('#groupComposerTitle').fill('Project Alpha')
  await page.getByLabel('Peer B').check()
  await page.getByRole('button', { name: '创建群聊' }).click()

  await expect(page.locator('#roomTitle')).toHaveText('Project Alpha')
  await expect(page.locator('#roomMeta')).toContainText('group · 1 participants')
  await expect(page.locator('#conversationList')).toContainText('Project Alpha')
})

test('accepting a group invitation injects full history and enters the group without refresh', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=bootstrap_two_peers`)

  await page.evaluate(() => {
    window.ParaMindHarness.setGroupAcceptResponse('invite-1', {
      invitation: {
        id: 'invite-1',
        conversation_id: 'group-project-alpha',
        inviter_id: 'peer-a',
        target_peer_id: 'peer-b',
        title: 'Project Alpha',
        participant_ids: ['peer-a', 'peer-b'],
        status: 'accepted',
        direction: 'inbound',
        created_at: '2026-04-01T10:00:00Z',
        updated_at: '2026-04-01T10:02:00Z',
      },
      conversation: {
        id: 'group-project-alpha',
        title: 'Project Alpha',
        kind: 'group',
        updated_at: '2026-04-01T10:02:00Z',
        participant_ids: ['peer-a', 'peer-b'],
        last_message: {
          id: 'group-msg-2',
          conversation_id: 'group-project-alpha',
          role: 'peer',
          sender_name: 'Peer A',
          content: 'History line 2',
          status: 'sent',
          created_at: '2026-04-01T10:01:00Z',
          updated_at: '2026-04-01T10:01:00Z',
        },
      },
      messages: [
        {
          id: 'group-msg-1',
          conversation_id: 'group-project-alpha',
          role: 'peer',
          sender_name: 'Peer A',
          content: 'History line 1',
          status: 'sent',
          metadata: {},
          created_at: '2026-04-01T10:00:30Z',
          updated_at: '2026-04-01T10:00:30Z',
        },
        {
          id: 'group-msg-2',
          conversation_id: 'group-project-alpha',
          role: 'peer',
          sender_name: 'Peer A',
          content: 'History line 2',
          status: 'sent',
          metadata: {},
          created_at: '2026-04-01T10:01:00Z',
          updated_at: '2026-04-01T10:01:00Z',
        },
      ],
      latest_conversation_event_id: 42,
    })

    window.ParaMindHarness.emitGlobalEvent({
      type: 'group.invited',
      payload: {
        invitation: {
          id: 'invite-1',
          conversation_id: 'group-project-alpha',
          inviter_id: 'peer-a',
          target_peer_id: 'peer-a',
          title: 'Project Alpha',
          participant_ids: ['peer-a', 'peer-b'],
          status: 'pending',
          direction: 'inbound',
          created_at: '2026-04-01T10:00:00Z',
          updated_at: '2026-04-01T10:00:00Z',
        },
      },
    })
  })

  await page.locator('#conversationList [data-conversation-id="invite:invite-1"]').click()
  await expect(page.locator('#roomTitle')).toContainText('群邀请')

  await page.getByRole('button', { name: '接受' }).click()

  await expect(page.locator('#roomTitle')).toHaveText('Project Alpha')
  await expect(page.locator('#roomMeta')).toContainText('group · 2 participants')
  await expect(page.locator('#messageList')).toContainText('History line 1')
  await expect(page.locator('#messageList')).toContainText('History line 2')
  await expect(page.locator('#conversationList')).not.toContainText('群邀请 · Project Alpha')
})

test('existing group members can invite a new peer from the active group shell', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=bootstrap_two_peers`)

  await page.evaluate(() => {
    window.ParaMindHarness.emitGlobalEvent({
      type: 'peer.joined',
      payload: {
        peer: {
          id: 'peer-c',
          display_name: 'Peer C',
          status: 'online',
          backend_port: 5003,
        },
      },
    })
    window.ParaMindHarness.emitGlobalEvent({
      type: 'conversation.created',
      payload: {
        conversation: {
          id: 'group-project-alpha',
          title: 'Project Alpha',
          kind: 'group',
          updated_at: '2026-04-01T10:00:00Z',
          participant_ids: ['peer-a', 'peer-b'],
          last_message: null,
        },
      },
    })
  })

  await page.locator('#conversationList [data-conversation-id="group-project-alpha"]').click()
  await expect(page.locator('#roomTitle')).toHaveText('Project Alpha')
  await expect(page.getByRole('button', { name: '邀请成员' })).toBeVisible()

  await page.getByRole('button', { name: '邀请成员' }).click()
  await expect(page.locator('#groupComposerModal')).toBeVisible()
  await expect(page.getByLabel('Peer C')).toBeVisible()
  await expect(page.getByLabel('Peer A')).toHaveCount(0)
  await expect(page.getByLabel('Peer B')).toHaveCount(0)
})

test('leaving a non-general group removes it locally and returns to the fallback conversation', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=bootstrap_two_peers`)

  await page.evaluate(() => {
    window.ParaMindHarness.emitGlobalEvent({
      type: 'conversation.created',
      payload: {
        conversation: {
          id: 'group-project-alpha',
          title: 'Project Alpha',
          kind: 'group',
          updated_at: '2026-04-01T10:00:00Z',
          participant_ids: ['peer-a', 'peer-b'],
          last_message: null,
        },
      },
    })
  })

  await page.locator('#conversationList [data-conversation-id="group-project-alpha"]').click()
  await expect(page.locator('#roomTitle')).toHaveText('Project Alpha')

  await page.getByRole('button', { name: '退出群聊' }).click()

  await expect(page.locator('#roomTitle')).toHaveText('General')
  await expect(page.locator('#conversationList')).not.toContainText('Project Alpha')
})

test('composer textarea auto-expands on multi-line input', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=bootstrap_two_peers`)
  const textarea = page.locator('#composerInput')
  const initialHeight = await textarea.evaluate(el => el.getBoundingClientRect().height)
  await textarea.fill('line1\nline2\nline3\nline4\nline5\nline6')
  await textarea.dispatchEvent('input')
  const expandedHeight = await textarea.evaluate(el => el.getBoundingClientRect().height)
  expect(expandedHeight).toBeGreaterThan(initialHeight)
})

test('@AI mode toggle activates AI mode and updates placeholder', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=bootstrap_two_peers`)
  const aiBtn = page.locator('#modeAiBtn')
  const textBtn = page.locator('#modeTextBtn')
  await expect(textBtn).toHaveClass(/active/)
  await aiBtn.click()
  await expect(aiBtn).toHaveClass(/active/)
  await expect(textBtn).not.toHaveClass(/active/)
  const placeholder = await page.locator('#composerInput').getAttribute('placeholder')
  expect(placeholder).toContain('AI 草稿')
})

test('marked.js and DOMPurify are loaded and render markdown bold and inline code in AI messages', async ({ page }) => {
  await openHarness(page, 'ai_streaming_draft')

  const markedLoaded = await page.evaluate(() => typeof window.marked !== 'undefined')
  const purifyLoaded = await page.evaluate(() => typeof window.DOMPurify !== 'undefined')
  expect(markedLoaded).toBe(true)
  expect(purifyLoaded).toBe(true)

  const draftId = 'draft-1'
  await page.evaluate((id) => {
    window.ParaMindHarness.emitConversationEvent({
      type: 'message.updated',
      conversation_id: 'general',
      entity_id: id,
      payload: {
        message: {
          id,
          conversation_id: 'general',
          sender_id: 'assistant',
          sender_name: 'AI',
          role: 'assistant',
          status: 'completed',
          content: '**bold** and `code`',
          metadata: { local_draft: true },
          created_at: '2026-04-01T09:02:00Z',
          updated_at: '2026-04-01T09:03:00Z',
        },
      },
    })
  }, draftId)

  const cardHtml = await page.locator(`[data-message-id="${draftId}"] [data-message-content="${draftId}"]`).innerHTML()
  expect(cardHtml).toContain('<strong>')
  expect(cardHtml).toContain('<code>')
})

test('scroll indicator appears when user is scrolled up and a new message arrives', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=bootstrap_two_peers`)
  await expect(page.locator('#messageList')).toContainText('Welcome to ParaMind')

  // Simulate user scrolled up by setting scrollTop to 0 and dispatching scroll event
  await page.locator('#messageList').evaluate((el) => { el.scrollTop = 0 })
  await page.locator('#messageList').dispatchEvent('scroll')

  // Inject a new message while the user is scrolled up
  await page.evaluate(() => {
    window.ParaMindHarness.emitConversationEvent({
      type: 'message.created',
      conversation_id: 'general',
      entity_id: 'msg-scroll-test',
      payload: {
        message: {
          id: 'msg-scroll-test',
          conversation_id: 'general',
          role: 'peer',
          sender_name: 'Peer B',
          content: 'New message while scrolled up',
          status: 'sent',
          metadata: {},
          created_at: '2026-04-01T10:00:00Z',
          updated_at: '2026-04-01T10:00:00Z',
        },
      },
    })
  })

  // The scroll indicator should be visible with count ≥ 1
  await expect(page.locator('#scrollIndicator')).toBeVisible({ timeout: 2000 })
  await expect(page.locator('#scrollIndicatorCount')).toContainText('1')
})

test('shows typing indicator for streaming message with no content', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=bootstrap_two_peers`)

  const draftId = await page.evaluate(() => {
    const draft = window.ParaMindHarness.createAssistantDraft('general')
    return draft.id
  })

  // Emit a streaming update with empty content — typing indicator should appear
  await page.evaluate((id) => {
    window.ParaMindHarness.emitConversationEvent({
      type: 'message.updated',
      conversation_id: 'general',
      entity_id: id,
      payload: {
        message: {
          id,
          conversation_id: 'general',
          sender_id: 'assistant',
          sender_name: 'AI',
          role: 'assistant',
          status: 'streaming',
          content: '',
          metadata: { local_draft: true },
          created_at: '2026-04-01T10:00:00Z',
          updated_at: '2026-04-01T10:00:01Z',
        },
      },
    })
  }, draftId)

  await expect(page.locator(`[data-message-content="${draftId}"] .typing-indicator`)).toBeVisible()

  // Emit a streaming update with actual content — typing indicator should disappear
  await page.evaluate((id) => {
    window.ParaMindHarness.emitConversationEvent({
      type: 'message.updated',
      conversation_id: 'general',
      entity_id: id,
      payload: {
        message: {
          id,
          conversation_id: 'general',
          sender_id: 'assistant',
          sender_name: 'AI',
          role: 'assistant',
          status: 'streaming',
          content: '你好',
          metadata: { local_draft: true },
          created_at: '2026-04-01T10:00:00Z',
          updated_at: '2026-04-01T10:00:02Z',
        },
      },
    })
  }, draftId)

  await expect(page.locator(`[data-message-content="${draftId}"] .typing-indicator`)).not.toBeVisible()
  await expect(page.locator(`[data-message-content="${draftId}"]`)).toContainText('你好')
})
