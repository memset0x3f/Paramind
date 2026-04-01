import assert from 'node:assert/strict'
import { test } from 'node:test'

import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const chatRenderer = require('../../paramind/apps/desktop/renderer/chat.js')

import chatState from '../../paramind/apps/desktop/renderer/chat_state.js'

test('maps job and peer events into synthetic system messages', () => {
  const state = chatState.createChatState({ activeConversationId: 'conv-a' })

  const next = chatState.reduceEvent(state, {
    id: 7,
    type: 'job.failed',
    conversation_id: 'conv-a',
    entity_id: 'job-1',
    payload: { error: 'cancelled' },
  })

  const messages = next.messagesByConversation.get('conv-a')
  assert.equal(messages.at(-1).role, 'system')
  assert.equal(messages.at(-1).kind, 'job.failed')
  assert.match(messages.at(-1).content, /cancelled/)

  const routeCard = chatState.createSystemMessageForEvent({
    id: 8,
    type: 'job.route',
    conversation_id: 'conv-a',
    entity_id: 'job-1',
    payload: {
      route: [
        { id: 'peer-a', display_name: 'Peer A', layers: '0-12' },
        { id: 'peer-b', display_name: 'Peer B', layers: '12-24' },
      ],
    },
  })

  assert.equal(routeCard.kind, 'job.route')
  assert.match(routeCard.content, /Peer A:0-12/)

  const routed = chatState.reduceEvent(next, {
    id: 8,
    type: 'job.route',
    conversation_id: 'conv-a',
    entity_id: 'job-1',
    payload: {
      route: [
        { id: 'peer-a', display_name: 'Peer A', layers: '0-12' },
        { id: 'peer-b', display_name: 'Peer B', layers: '12-24' },
      ],
    },
  })

  assert.equal(routed.messagesByConversation.get('conv-a').at(-1).kind, 'job.failed')
})

test('tracks unread counts for inactive conversations only', () => {
  const state = chatState.createChatState({ activeConversationId: 'conv-a' })

  const next = chatState.reduceEvent(state, {
    id: 3,
    type: 'message.created',
    conversation_id: 'conv-b',
    entity_id: 'msg-1',
    payload: {
      message: {
        id: 'msg-1',
        conversation_id: 'conv-b',
        role: 'peer',
        sender_name: 'Peer B',
        content: 'hello',
        status: 'sent',
        created_at: '2026-03-24T00:00:00Z',
        updated_at: '2026-03-24T00:00:00Z',
      },
    },
  })

  assert.equal(next.unreadByConversation.get('conv-b'), 1)
  assert.equal(next.unreadByConversation.get('conv-a') || 0, 0)

  const markedRead = chatState.markConversationRead(next, 'conv-b')
  assert.equal(markedRead.activeConversationId, 'conv-b')
  assert.equal(markedRead.unreadByConversation.get('conv-b'), 0)
})

test('resolves the latest retryable user message', () => {
  const state = chatState.createChatState({ activeConversationId: 'conv-a' })
  const next = chatState.reduceEvent(state, {
    id: 10,
    type: 'message.created',
    conversation_id: 'conv-a',
    entity_id: 'msg-3',
    payload: {
      message: {
        id: 'msg-3',
        conversation_id: 'conv-a',
        role: 'user',
        sender_name: 'Me',
        content: 'retry me',
        status: 'sent',
        created_at: '2026-03-24T00:01:00Z',
        updated_at: '2026-03-24T00:01:00Z',
      },
    },
  })

  const retryable = chatState.getLatestRetryableUserMessage(next, 'conv-a')
  assert.equal(retryable.id, 'msg-3')
  assert.equal(retryable.content, 'retry me')
})


test('classifies renderer card kinds for user, peer, ai draft, ai, and request messages', () => {
  const userMessage = {
    id: 'user-1',
    conversation_id: 'conv-a',
    role: 'user',
    sender_name: 'Me',
    content: '@AI hello',
    status: 'sent',
    metadata: {},
  }
  const peerMessage = {
    id: 'peer-1',
    conversation_id: 'conv-a',
    role: 'peer',
    sender_name: 'Peer A',
    content: 'hello',
    status: 'sent',
    metadata: {},
  }
  const draftMessage = {
    id: 'draft-1',
    conversation_id: 'conv-a',
    role: 'assistant',
    sender_name: 'AI',
    content: 'draft',
    status: 'streaming',
    metadata: { local_draft: true },
  }
  const publishedAiMessage = {
    id: 'ai-1',
    conversation_id: 'conv-a',
    role: 'assistant',
    sender_name: 'AI',
    content: 'published',
    status: 'completed',
    metadata: { published_from_draft: true },
  }
  const requestMessage = {
    id: 'req-1',
    conversation_id: 'conv-a',
    role: 'system',
    sender_name: 'System',
    kind: 'dm.request',
    content: 'request',
    status: 'sent',
    metadata: {},
  }

  assert.equal(chatState.getCardKind(userMessage), 'message.user')
  assert.equal(chatState.getCardKind(peerMessage), 'message.peer')
  assert.equal(chatState.getCardKind(draftMessage), 'message.ai-draft')
  assert.equal(chatState.getCardKind(publishedAiMessage), 'message.ai')
  assert.equal(chatState.getCardKind(requestMessage), 'message.request')
})


test('derives display status for regular, ai draft, and published ai messages', () => {
  assert.equal(chatState.getDisplayStatus({ role: 'user', status: 'read', metadata: {} }), 'read')
  assert.equal(chatState.getDisplayStatus({ role: 'user', status: 'sent', metadata: {} }), 'sent')
  assert.equal(chatState.getDisplayStatus({ role: 'assistant', status: 'streaming', metadata: { local_draft: true } }), 'streaming')
  assert.equal(chatState.getDisplayStatus({ role: 'assistant', sender_name: 'AI', status: 'completed', metadata: { published_from_draft: true } }), 'completed')
})




test('maps display statuses to stable status tones for renderer badges', () => {
  assert.equal(chatState.getStatusTone({ role: 'user', status: 'sent', metadata: {} }), 'sent')
  assert.equal(chatState.getStatusTone({ role: 'peer', status: 'delivered', metadata: {} }), 'delivered')
  assert.equal(chatState.getStatusTone({ role: 'peer', status: 'read', metadata: {} }), 'read')
  assert.equal(chatState.getStatusTone({ role: 'assistant', status: 'streaming', metadata: { local_draft: true } }), 'streaming')
  assert.equal(chatState.getStatusTone({ role: 'assistant', sender_name: 'AI', status: 'completed', metadata: { published_from_draft: true } }), 'completed')
  assert.equal(chatState.getStatusTone({ role: 'assistant', status: 'failed', metadata: { local_draft: true } }), 'failed')
})



test('detects when bootstrap payload has no meaningful UI changes', () => {
  const baseline = {
    activeConversationId: 'general',
    self: { id: 'peer-a', display_name: 'Peer A' },
    peers: [
      { id: 'peer-a', display_name: 'Peer A', status: 'online', backend_port: 5001 },
      { id: 'peer-b', display_name: 'Peer B', status: 'online', backend_port: 5002 },
    ],
    conversations: [
      { id: 'general', title: 'General', kind: 'group', updated_at: '2026-03-26T10:00:00Z', participant_ids: ['peer-a', 'peer-b'], last_message: { id: 'm1' } },
    ],
    dmRequests: [],
    groupInvitations: [],
  }

  assert.equal(chatState.hasMeaningfulBootstrapChange(baseline, structuredClone(baseline)), false)
  assert.equal(
    chatState.hasMeaningfulBootstrapChange(baseline, {
      ...structuredClone(baseline),
      peers: [...baseline.peers, { id: 'peer-c', display_name: 'Peer C', status: 'online', backend_port: 5003 }],
    }),
    true,
  )
})

test('treats group invitation lifecycle events as conversation-scope UI changes', () => {
  assert.equal(
    chatState.getEventRenderScope({
      type: 'group.invited',
      payload: { invitation: { id: 'g1', title: 'Project Alpha' } },
    }),
    'conversations',
  )
  assert.equal(
    chatState.getEventRenderScope({
      type: 'conversation.created',
      payload: { conversation: { id: 'group-1' } },
    }),
    'conversations',
  )
})



test('applies message.token events incrementally to streaming messages', () => {
  let state = chatState.createChatState({ activeConversationId: 'conv-a' })
  state = chatState.reduceEvent(state, {
    id: 1,
    type: 'message.created',
    conversation_id: 'conv-a',
    entity_id: 'draft-1',
    payload: {
      message: {
        id: 'draft-1',
        conversation_id: 'conv-a',
        role: 'assistant',
        sender_name: 'AI',
        content: '',
        status: 'streaming',
        metadata: { local_draft: true },
        created_at: '2026-03-26T00:00:00Z',
        updated_at: '2026-03-26T00:00:00Z',
      },
    },
  })

  state = chatState.reduceEvent(state, {
    id: 2,
    type: 'message.token',
    conversation_id: 'conv-a',
    entity_id: 'draft-1',
    payload: { message_id: 'draft-1', token: '你好' },
  })
  state = chatState.reduceEvent(state, {
    id: 3,
    type: 'message.token',
    conversation_id: 'conv-a',
    entity_id: 'draft-1',
    payload: { message_id: 'draft-1', token: '你好，世界' },
  })

  const draft = state.messagesByConversation.get('conv-a').find((item) => item.id === 'draft-1')
  assert.equal(draft.content, '你好，世界')
  assert.equal(draft.status, 'streaming')
})

test('chooses cached conversation data instead of reloading the same open conversation', () => {
  assert.equal(
    chatState.getConversationSelectionMode({
      currentActiveConversationId: 'general',
      nextConversationId: 'general',
      hasCachedMessages: true,
      activeStreamConversationId: 'general',
    }),
    'noop',
  )
  assert.equal(
    chatState.getConversationSelectionMode({
      currentActiveConversationId: 'general',
      nextConversationId: 'dm:peer-a-peer-b',
      hasCachedMessages: true,
      activeStreamConversationId: 'general',
    }),
    'reuse-cache',
  )
  assert.equal(
    chatState.getConversationSelectionMode({
      currentActiveConversationId: 'general',
      nextConversationId: 'dm:peer-a-peer-b',
      hasCachedMessages: false,
      activeStreamConversationId: 'general',
    }),
    'load',
  )
})

test('derives peer action states for dm request lifecycle', () => {
  assert.deepEqual(
    chatState.getPeerActionState({ peerId: 'peer-b', selfId: 'peer-a' }),
    { label: '请求私聊', disabled: false, action: 'request-dm' },
  )

  assert.deepEqual(
    chatState.getPeerActionState({ peerId: 'peer-b', selfId: 'peer-a', existingConversationId: 'dm:peer-a-peer-b' }),
    { label: '打开 DM', disabled: false, action: 'open-dm', conversationId: 'dm:peer-a-peer-b' },
  )

  assert.deepEqual(
    chatState.getPeerActionState({
      peerId: 'peer-b',
      selfId: 'peer-a',
      existingConversationId: 'dm:peer-a-peer-b',
      activeConversationId: 'dm:peer-a-peer-b',
    }),
    { label: '退出 DM', disabled: false, action: 'leave-dm', conversationId: 'dm:peer-a-peer-b' },
  )

  assert.deepEqual(
    chatState.getPeerActionState({ peerId: 'peer-b', selfId: 'peer-a', pendingDirection: 'outbound', pendingRequestId: 'req-1' }),
    { label: '等待回应', disabled: true, action: 'pending-outbound', requestId: 'req-1' },
  )

  assert.deepEqual(
    chatState.getPeerActionState({ peerId: 'peer-b', selfId: 'peer-a', pendingDirection: 'inbound', pendingRequestId: 'req-2' }),
    { label: '回应请求', disabled: false, action: 'open-request', requestId: 'req-2' },
  )
})

test('truncates conversation preview text to keep card size stable', () => {
  assert.equal(chatState.getConversationPreviewText(''), 'No messages yet')
  assert.equal(chatState.getConversationPreviewText('short text'), 'short text')
  assert.equal(
    chatState.getConversationPreviewText('这是一个很长很长很长很长很长的消息内容，用来测试左边栏摘要截断效果', 14),
    '这是一个很长很长很长很...',
  )
})

test('formats message clock time using supplied timezone when provided', () => {
  assert.equal(
    chatState.formatClockTime('2026-03-27T05:01:00+00:00', { timeZone: 'Asia/Hong_Kong', locale: 'zh-HK' }),
    '13:01',
  )
})

test('maps event types to minimal renderer scopes', () => {
  assert.equal(
    chatState.getEventRenderScope({
      type: 'message.token',
      conversation_id: 'general',
      payload: { message_id: 'draft-1', token: '你' },
    }),
    'none',
  )

  assert.equal(
    chatState.getEventRenderScope({
      type: 'message.updated',
      conversation_id: 'general',
      payload: { message: { id: 'draft-1', conversation_id: 'general', status: 'streaming' } },
    }),
    'none',
  )

  assert.equal(
    chatState.getEventRenderScope({
      type: 'peer.updated',
      payload: { peer: { id: 'peer-b', display_name: 'Peer B' } },
    }),
    'peers',
  )

  assert.equal(
    chatState.getEventRenderScope({
      type: 'conversation.updated',
      payload: { conversation: { id: 'general' } },
    }),
    'conversations',
  )
})

test('maps conversation selection mode to minimal render scopes', () => {
  assert.equal(chatState.getConversationSelectionRenderScope('noop'), 'none')
  assert.equal(chatState.getConversationSelectionRenderScope('reuse-cache'), 'conversation')
  assert.equal(chatState.getConversationSelectionRenderScope('load'), 'shell')
})

test('prefers known conversation event cursor and otherwise falls back to bootstrap latest cursor', () => {
  assert.equal(
    chatState.getStreamStartAfter({
      knownConversationEventId: 12,
      bootstrapLatestEventId: 400,
    }),
    12,
  )

  assert.equal(
    chatState.getStreamStartAfter({
      knownConversationEventId: 0,
      bootstrapLatestEventId: 400,
    }),
    400,
  )

  assert.equal(
    chatState.getStreamStartAfter({
      knownConversationEventId: null,
      bootstrapLatestEventId: null,
    }),
    0,
  )
})

test('resolves post request navigation without leaving stale request selected', () => {
  assert.deepEqual(
    chatState.resolvePostDmRequestSelection({
      action: 'accept',
      wasViewingRequest: true,
      resolvedConversationId: 'dm:peer-a-peer-b',
      fallbackConversationId: 'general',
    }),
    { nextConversationId: 'dm:peer-a-peer-b', force: true },
  )

  assert.deepEqual(
    chatState.resolvePostDmRequestSelection({
      action: 'reject',
      wasViewingRequest: true,
      resolvedConversationId: null,
      fallbackConversationId: 'general',
    }),
    { nextConversationId: 'general', force: true },
  )
})

test('flags shell refreshes for active lifecycle and conversation updates', () => {
  assert.equal(
    chatState.shouldRefreshActiveShellForEvent(
      {
        type: 'conversation.updated',
        payload: {
          conversation: {
            id: 'general',
            title: 'General',
            kind: 'group',
            participant_ids: ['peer-a', 'peer-b', 'peer-c'],
          },
        },
      },
      'general',
      {
        id: 'general',
        title: 'General',
        kind: 'group',
        participant_ids: ['peer-a', 'peer-b'],
      },
    ),
    true,
  )

  assert.equal(
    chatState.shouldRefreshActiveShellForEvent(
      {
        type: 'conversation.updated',
        payload: {
          conversation: {
            id: 'general',
            title: 'General',
            kind: 'group',
            participant_ids: ['peer-a', 'peer-b'],
            last_message: { id: 'm2', content: 'new preview' },
          },
        },
      },
      'general',
      {
        id: 'general',
        title: 'General',
        kind: 'group',
        participant_ids: ['peer-a', 'peer-b'],
      },
    ),
    false,
  )

  assert.equal(
    chatState.shouldRefreshActiveShellForEvent(
      {
        type: 'dm.accepted',
        payload: {
          request_id: 'req-1',
          conversation: { id: 'dm:peer-a-peer-b' },
        },
      },
      'request:req-1',
    ),
    true,
  )

  assert.equal(
    chatState.shouldRefreshActiveShellForEvent(
      {
        type: 'group.rejected',
        payload: {
          invitation_id: 'invite-1',
        },
      },
      'invite:invite-1',
    ),
    true,
  )

  assert.equal(
    chatState.shouldRefreshActiveShellForEvent(
      {
        type: 'conversation.updated',
        payload: { conversation: { id: 'general' } },
      },
      'dm:peer-a-peer-b',
      {
        id: 'dm:peer-a-peer-b',
        title: 'Peer B',
        kind: 'dm',
        participant_ids: ['peer-a', 'peer-b'],
      },
    ),
    false,
  )
})

test('excludes assistant from displayed participant counts', () => {
  assert.equal(
    chatState.getConversationParticipantCount({
      participant_ids: ['peer-a', 'peer-b', 'assistant'],
    }),
    2,
  )
})


test('normalizes bootstrap payloads into stable sidebar cards', () => {
  const bootstrap = chatState.normalizeBootstrapPayload({
    active_conversation_id: 'general',
    conversations: [
      {
        id: 'general',
        title: 'General',
        kind: 'group',
        updated_at: '2026-03-31T09:00:00Z',
        last_message: { id: 'm1', content: 'A somewhat longer preview than the card should show' },
      },
    ],
    dm_requests: [
      {
        id: 'req-1',
        requester_id: 'peer-b',
        target_peer_id: 'peer-a',
        direction: 'inbound',
        status: 'pending',
        created_at: '2026-03-31T09:05:00Z',
        updated_at: '2026-03-31T09:05:00Z',
      },
    ],
  })

  assert.deepEqual(chatState.buildSidebarCards(bootstrap), [
    {
      id: 'request:req-1',
      kind: 'request',
      title: 'DM request',
      preview: 'Pending DM request',
      updatedAt: '2026-03-31T09:05:00Z',
    },
    {
      id: 'general',
      kind: 'conversation',
      title: 'General',
      preview: chatState.getConversationPreviewText('A somewhat longer preview than the card should show'),
      updatedAt: '2026-03-31T09:00:00Z',
    },
  ])
})

test('applies global lifecycle events without requiring electron runtime state', () => {
  const bootstrap = chatState.normalizeBootstrapPayload({
    active_conversation_id: 'request:req-1',
    dm_requests: [
      {
        id: 'req-1',
        requester_id: 'peer-a',
        target_peer_id: 'peer-b',
        direction: 'outbound',
        status: 'pending',
        created_at: '2026-03-31T09:00:00Z',
        updated_at: '2026-03-31T09:00:00Z',
      },
    ],
    peer_relationships: [
      { peer_id: 'peer-b', status: 'outbound_pending_dm', conversation_id: null, request_id: 'req-1' },
    ],
  })

  const next = chatState.applyGlobalEvent(bootstrap, {
    type: 'dm.accepted',
    payload: {
      request_id: 'req-1',
      conversation: {
        id: 'dm:peer-a-peer-b',
        title: 'Peer B',
        kind: 'dm',
        updated_at: '2026-03-31T09:01:00Z',
        participant_ids: ['peer-a', 'peer-b'],
      },
      relationship: {
        peer_id: 'peer-b',
        status: 'active_dm',
        conversation_id: 'dm:peer-a-peer-b',
        request_id: null,
      },
    },
  })

  assert.equal(next.activeConversationId, 'dm:peer-a-peer-b')
  assert.equal(next.dmRequests.length, 0)
  assert.equal(next.relationshipsByPeerId.get('peer-b').status, 'active_dm')
  assert.equal(next.conversations[0].id, 'dm:peer-a-peer-b')
})

test('normalizes backend relationship payloads that use the relationship field', () => {
  const bootstrap = chatState.normalizeBootstrapPayload({
    relationships: [
      {
        peer_id: 'peer-b',
        relationship: 'active_dm',
        conversation_id: 'dm:peer-a-peer-b',
        request_id: null,
      },
    ],
  })

  const relationship = bootstrap.relationshipsByPeerId.get('peer-b')
  assert.equal(relationship.status, 'active_dm')
  assert.equal(relationship.relationship, 'active_dm')
  assert.equal(relationship.conversation_id, 'dm:peer-a-peer-b')
})

test('conversation event helpers only update the targeted draft message', () => {
  let state = chatState.createChatState({ activeConversationId: 'general' })
  state = chatState.applyConversationEvent(state, {
    id: 1,
    type: 'message.created',
    conversation_id: 'general',
    entity_id: 'draft-1',
    payload: {
      message: {
        id: 'draft-1',
        conversation_id: 'general',
        role: 'assistant',
        sender_name: 'AI',
        content: '',
        status: 'streaming',
        metadata: { local_draft: true },
        created_at: '2026-03-31T09:00:00Z',
        updated_at: '2026-03-31T09:00:00Z',
      },
    },
  })
  state = chatState.applyConversationEvent(state, {
    id: 2,
    type: 'message.created',
    conversation_id: 'general',
    entity_id: 'draft-2',
    payload: {
      message: {
        id: 'draft-2',
        conversation_id: 'general',
        role: 'assistant',
        sender_name: 'AI',
        content: '',
        status: 'streaming',
        metadata: { local_draft: true },
        created_at: '2026-03-31T09:00:01Z',
        updated_at: '2026-03-31T09:00:01Z',
      },
    },
  })

  const next = chatState.applyConversationEvent(state, {
    id: 3,
    type: 'message.token',
    conversation_id: 'general',
    entity_id: 'draft-2',
    payload: { message_id: 'draft-2', token: 'hello' },
  })

  const messages = next.messagesByConversation.get('general')
  assert.equal(messages.find((item) => item.id === 'draft-1').content, '')
  assert.equal(messages.find((item) => item.id === 'draft-2').content, 'hello')
})

test('reads runtime config from fake electron api without needing a real window', () => {
  assert.deepEqual(
    chatRenderer.resolveRuntimeConfig({
      electronAPI: {
        getBackendUrl: () => 'http://127.0.0.1:5999',
        getInstanceMeta: () => ({ id: 'peer-test', display_name: 'Peer Test' }),
      },
      ParaMindChatState: chatState,
    }),
    {
      apiBase: 'http://127.0.0.1:5999',
      instanceMeta: { id: 'peer-test', display_name: 'Peer Test' },
      chatStateApi: chatState,
    },
  )
})
