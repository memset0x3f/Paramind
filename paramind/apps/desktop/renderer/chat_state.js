function cloneMap(map) {
  return new Map(Array.from(map.entries(), ([key, value]) => [key, Array.isArray(value) ? value.map((item) => ({ ...item })) : value]))
}

function createChatState(overrides = {}) {
  return {
    activeConversationId: overrides.activeConversationId || null,
    conversationsById: new Map(),
    messagesByConversation: new Map(),
    unreadByConversation: new Map(),
    lastEventIdByConversation: new Map(),
    ...overrides,
    conversationsById: overrides.conversationsById ? new Map(overrides.conversationsById) : new Map(),
    messagesByConversation: overrides.messagesByConversation ? cloneMap(overrides.messagesByConversation) : new Map(),
    unreadByConversation: overrides.unreadByConversation ? new Map(overrides.unreadByConversation) : new Map(),
    lastEventIdByConversation: overrides.lastEventIdByConversation ? new Map(overrides.lastEventIdByConversation) : new Map(),
  }
}

function cloneState(state) {
  return createChatState({
    ...state,
    conversationsById: state.conversationsById,
    messagesByConversation: state.messagesByConversation,
    unreadByConversation: state.unreadByConversation,
    lastEventIdByConversation: state.lastEventIdByConversation,
  })
}

function getCardKind(message) {
  const normalized = normalizeMessage(message)
  if (normalized.metadata?.local_draft) return 'message.ai-draft'
  if (normalized.kind === 'dm.request') return 'message.request'
  if (normalized.role === 'assistant' && normalized.sender_name === 'AI') return 'message.ai'
  if (normalized.role === 'peer') return 'message.peer'
  if (normalized.role === 'user') return 'message.user'
  return 'message.system-chat'
}

function getDisplayStatus(message) {
  const normalized = normalizeMessage(message)
  if (normalized.metadata?.local_draft) return normalized.status || 'pending'
  if (normalized.role === 'assistant' && normalized.sender_name === 'AI') return normalized.status || 'completed'
  return normalized.status || 'sent'
}

function getStatusTone(message) {
  const status = getDisplayStatus(message)
  switch (status) {
    case 'streaming':
    case 'completed':
    case 'failed':
    case 'read':
    case 'delivered':
    case 'sent':
    case 'pending':
    case 'cancelled':
      return status
    default:
      return 'sent'
  }
}

function _stablePeer(peer) {
  return {
    id: peer?.id || '',
    display_name: peer?.display_name || '',
    status: peer?.status || '',
    backend_port: peer?.backend_port || 0,
    device: peer?.capabilities?.device || '',
  }
}

function _stableConversation(conversation) {
  return {
    id: conversation?.id || '',
    title: conversation?.title || '',
    kind: conversation?.kind || '',
    updated_at: conversation?.updated_at || '',
    participant_ids: [...(conversation?.participant_ids || [])].sort(),
    last_message_id: conversation?.last_message?.id || '',
    last_message_status: conversation?.last_message?.status || '',
    last_message_content: conversation?.last_message?.content || '',
  }
}

function _stableDmRequest(request) {
  return {
    id: request?.id || '',
    requester_id: request?.requester_id || '',
    target_peer_id: request?.target_peer_id || '',
    direction: request?.direction || '',
    status: request?.status || '',
    conversation_id: request?.conversation_id || '',
    updated_at: request?.updated_at || request?.created_at || '',
  }
}

function _stableGroupInvitation(invitation) {
  return {
    id: invitation?.id || '',
    conversation_id: invitation?.conversation_id || '',
    inviter_id: invitation?.inviter_id || '',
    target_peer_id: invitation?.target_peer_id || '',
    title: invitation?.title || '',
    status: invitation?.status || '',
    updated_at: invitation?.updated_at || invitation?.created_at || '',
  }
}

function hasMeaningfulBootstrapChange(previous, next) {
  const prevSignature = JSON.stringify({
    activeConversationId: previous?.activeConversationId || null,
    self: {
      id: previous?.self?.id || '',
      display_name: previous?.self?.display_name || '',
      backend_port: previous?.self?.backend_port || 0,
    },
    peers: (previous?.peers || []).map(_stablePeer).sort((a, b) => a.id.localeCompare(b.id)),
    conversations: (previous?.conversations || []).map(_stableConversation).sort((a, b) => a.id.localeCompare(b.id)),
    dmRequests: (previous?.dmRequests || []).map(_stableDmRequest).sort((a, b) => a.id.localeCompare(b.id)),
    groupInvitations: (previous?.groupInvitations || []).map(_stableGroupInvitation).sort((a, b) => a.id.localeCompare(b.id)),
  })
  const nextSignature = JSON.stringify({
    activeConversationId: next?.activeConversationId || null,
    self: {
      id: next?.self?.id || '',
      display_name: next?.self?.display_name || '',
      backend_port: next?.self?.backend_port || 0,
    },
    peers: (next?.peers || []).map(_stablePeer).sort((a, b) => a.id.localeCompare(b.id)),
    conversations: (next?.conversations || []).map(_stableConversation).sort((a, b) => a.id.localeCompare(b.id)),
    dmRequests: (next?.dmRequests || []).map(_stableDmRequest).sort((a, b) => a.id.localeCompare(b.id)),
    groupInvitations: (next?.groupInvitations || []).map(_stableGroupInvitation).sort((a, b) => a.id.localeCompare(b.id)),
  })
  return prevSignature !== nextSignature
}

function getConversationSelectionMode({
  currentActiveConversationId,
  nextConversationId,
  hasCachedMessages,
  activeStreamConversationId,
}) {
  if (
    currentActiveConversationId === nextConversationId
    && hasCachedMessages
    && activeStreamConversationId === nextConversationId
  ) {
    return 'noop'
  }
  if (hasCachedMessages) return 'reuse-cache'
  return 'load'
}

function getConversationSelectionRenderScope(selectionMode) {
  switch (selectionMode) {
    case 'noop':
      return 'none'
    case 'reuse-cache':
      return 'conversation'
    case 'load':
    default:
      return 'shell'
  }
}

function getConversationPreviewText(content, maxLength = 28) {
  const text = String(content || '').replace(/\s+/g, ' ').trim()
  if (!text) return 'No messages yet'
  if (text.length <= maxLength) return text
  return `${text.slice(0, Math.max(0, maxLength - 3))}...`
}

function getUserTimeZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined
  } catch {
    return undefined
  }
}

function formatClockTime(value, options = {}) {
  if (!value) return ''
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const formatter = new Intl.DateTimeFormat(options.locale || undefined, {
    timeZone: options.timeZone || getUserTimeZone(),
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
  return formatter.format(date)
}

function getStreamStartAfter({
  knownConversationEventId,
  bootstrapLatestEventId,
}) {
  const known = Number.isFinite(Number(knownConversationEventId)) ? Number(knownConversationEventId) : 0
  const bootstrapLatest = Number.isFinite(Number(bootstrapLatestEventId)) ? Number(bootstrapLatestEventId) : 0
  return known > 0 ? known : bootstrapLatest
}

function getEventRenderScope(event) {
  switch (event?.type) {
    case 'message.created':
    case 'message.updated':
    case 'message.deleted':
    case 'message.token':
      return 'none'
    case 'job.completed':
    case 'job.failed':
      return 'messages'
    case 'peer.joined':
    case 'peer.updated':
    case 'peer.left':
      return 'peers'
    case 'conversation.updated':
    case 'conversation.created':
    case 'conversation.deleted':
    case 'group.invited':
    case 'group.accepted':
    case 'group.rejected':
    case 'dm.requested':
    case 'dm.accepted':
    case 'dm.rejected':
      return 'conversations'
    default:
      return 'all'
  }
}

function resolvePostDmRequestSelection({
  action,
  wasViewingRequest,
  resolvedConversationId,
  fallbackConversationId,
}) {
  if (!wasViewingRequest) {
    return { nextConversationId: resolvedConversationId || null, force: false }
  }

  if (action === 'accept' && resolvedConversationId) {
    return { nextConversationId: resolvedConversationId, force: true }
  }

  if (fallbackConversationId) {
    return { nextConversationId: fallbackConversationId, force: true }
  }

  return { nextConversationId: null, force: true }
}

function normalizeMessage(message) {
  return {
    ...message,
    conversation_id: message.conversation_id || message.conversationId || null,
    sender_name: message.sender_name || message.senderName || '',
    status: message.status || 'sent',
    metadata: message.metadata || {},
    kind: message.kind || null,
  }
}

function upsertMessage(state, message) {
  const normalized = normalizeMessage(message)
  const conversationId = normalized.conversation_id
  const current = state.messagesByConversation.get(conversationId) || []
  const index = current.findIndex((item) => item.id === normalized.id)
  const nextMessages = current.slice()
  if (index >= 0) {
    nextMessages[index] = { ...nextMessages[index], ...normalized }
  } else {
    nextMessages.push(normalized)
  }
  nextMessages.sort((a, b) => {
    const aTime = a.created_at || a.updated_at || ''
    const bTime = b.created_at || b.updated_at || ''
    return aTime.localeCompare(bTime) || String(a.id).localeCompare(String(b.id))
  })
  state.messagesByConversation.set(conversationId, nextMessages)
  return normalized
}

function createSystemMessageForEvent(event) {
  const entityId = String(event.entity_id || event.id || 'event')
  const conversationId = event.conversation_id || event.payload?.message?.conversation_id || null

  switch (event.type) {
    case 'job.started':
      return {
        id: `system:${entityId}:started`,
        conversation_id: conversationId,
        sender_id: 'system',
        sender_name: 'System',
        role: 'system',
        kind: 'job.started',
        content: 'Assistant generation started',
        status: 'sent',
        metadata: { source_event: event.type, job_id: entityId, hide_in_timeline: true },
        created_at: event.timestamp || new Date().toISOString(),
        updated_at: event.timestamp || new Date().toISOString(),
      }
    case 'job.route': {
      const route = event.payload?.route || []
      const routeText = route.length
        ? route.map((node) => `${node.display_name || node.id}:${node.layers}`).join(' → ')
        : 'route unavailable'
      return {
        id: `system:${entityId}:route`,
        conversation_id: conversationId,
        sender_id: 'system',
        sender_name: 'System',
        role: 'system',
        kind: 'job.route',
        content: `Route selected: ${routeText}`,
        status: 'sent',
        metadata: { source_event: event.type, job_id: entityId, route, hide_in_timeline: true },
        created_at: event.timestamp || new Date().toISOString(),
        updated_at: event.timestamp || new Date().toISOString(),
      }
    }
    case 'job.failed':
      return {
        id: `system:${entityId}:failed`,
        conversation_id: conversationId,
        sender_id: 'system',
        sender_name: 'System',
        role: 'system',
        kind: 'job.failed',
        content: `Job failed${event.payload?.error ? `: ${event.payload.error}` : ''}`,
        status: 'sent',
        metadata: { source_event: event.type, job_id: entityId },
        created_at: event.timestamp || new Date().toISOString(),
        updated_at: event.timestamp || new Date().toISOString(),
      }
    case 'job.completed':
      return {
        id: `system:${entityId}:completed`,
        conversation_id: conversationId,
        sender_id: 'system',
        sender_name: 'System',
        role: 'system',
        kind: 'job.completed',
        content: 'Job completed',
        status: 'sent',
        metadata: { source_event: event.type, job_id: entityId, hide_in_timeline: true },
        created_at: event.timestamp || new Date().toISOString(),
        updated_at: event.timestamp || new Date().toISOString(),
      }
    case 'peer.joined':
      return {
        id: `system:${entityId}:joined`,
        conversation_id: conversationId,
        sender_id: 'system',
        sender_name: 'System',
        role: 'system',
        kind: 'peer.joined',
        content: `${event.payload?.peer?.display_name || entityId} joined`,
        status: 'sent',
        metadata: { source_event: event.type, peer_id: entityId, hide_in_timeline: true },
        created_at: event.timestamp || new Date().toISOString(),
        updated_at: event.timestamp || new Date().toISOString(),
      }
    case 'peer.left':
      return {
        id: `system:${entityId}:left`,
        conversation_id: conversationId,
        sender_id: 'system',
        sender_name: 'System',
        role: 'system',
        kind: 'peer.left',
        content: `${event.payload?.peer?.display_name || entityId} left`,
        status: 'sent',
        metadata: { source_event: event.type, peer_id: entityId, hide_in_timeline: true },
        created_at: event.timestamp || new Date().toISOString(),
        updated_at: event.timestamp || new Date().toISOString(),
      }
    case 'peer.updated':
      return {
        id: `system:${entityId}:updated`,
        conversation_id: conversationId,
        sender_id: 'system',
        sender_name: 'System',
        role: 'system',
        kind: 'peer.updated',
        content: `${event.payload?.peer?.display_name || entityId} updated`,
        status: 'sent',
        metadata: { source_event: event.type, peer_id: entityId, hide_in_timeline: true },
        created_at: event.timestamp || new Date().toISOString(),
        updated_at: event.timestamp || new Date().toISOString(),
      }
    default:
      return null
  }
}

function getPeerActionState({
  peerId,
  selfId,
  existingConversationId = null,
  pendingDirection = null,
  pendingRequestId = null,
  activeConversationId = null,
}) {
  if (peerId === selfId) return { label: '当前节点', disabled: true, action: 'self' }
  if (existingConversationId) {
    if (activeConversationId === existingConversationId) {
      return { label: '退出 DM', disabled: false, action: 'leave-dm', conversationId: existingConversationId }
    }
    return { label: '打开 DM', disabled: false, action: 'open-dm', conversationId: existingConversationId }
  }
  if (pendingDirection === 'outbound') {
    return { label: '等待回应', disabled: true, action: 'pending-outbound', requestId: pendingRequestId }
  }
  if (pendingDirection === 'inbound') {
    return { label: '回应请求', disabled: false, action: 'open-request', requestId: pendingRequestId }
  }
  return { label: '请求私聊', disabled: false, action: 'request-dm' }
}

function maybeIncrementUnread(state, conversationId, shouldCount) {
  if (!shouldCount || !conversationId) return
  const current = state.unreadByConversation.get(conversationId) || 0
  state.unreadByConversation.set(conversationId, current + 1)
}

function markConversationRead(state, conversationId) {
  const next = cloneState(state)
  if (!conversationId) return next
  next.activeConversationId = conversationId
  next.unreadByConversation.set(conversationId, 0)
  return next
}

function reduceEvent(state, event) {
  const next = cloneState(state)
  const conversationId =
    event.conversation_id ||
    event.payload?.message?.conversation_id ||
    event.payload?.conversation?.id ||
    null
  if (conversationId) {
    next.lastEventIdByConversation.set(conversationId, Math.max(next.lastEventIdByConversation.get(conversationId) || 0, Number(event.id || 0)))
  }

  let insertedVisibleMessage = false

  if (event.type === 'message.created' || event.type === 'message.updated') {
    const message = event.payload?.message
    if (message) {
      upsertMessage(next, message)
      insertedVisibleMessage = event.type === 'message.created'
    }
  }

  if (event.type === 'message.token') {
    const conversationIdForToken = conversationId
    const token = String(event.payload?.token || '')
    const messageId = String(event.payload?.message_id || event.entity_id || '')
    const current = next.messagesByConversation.get(conversationIdForToken) || []
    const index = current.findIndex((item) => String(item.id) === messageId)
    if (index >= 0 && token) {
      const existing = current[index]
      const updated = {
        ...existing,
        content: `${existing.content || ''}${token}`,
        status: 'streaming',
      }
      const nextMessages = current.slice()
      nextMessages[index] = updated
      next.messagesByConversation.set(conversationIdForToken, nextMessages)
    }
  }

  const synthetic = createSystemMessageForEvent(event)
  if (synthetic && !synthetic.metadata?.hide_in_timeline) {
    upsertMessage(next, synthetic)
    insertedVisibleMessage = true
  }

  maybeIncrementUnread(
    next,
    conversationId,
    insertedVisibleMessage && conversationId !== next.activeConversationId,
  )

  return next
}

function getLatestRetryableUserMessage(state, conversationId) {
  const messages = state.messagesByConversation.get(conversationId) || []
  const candidates = messages.filter((message) => message.role === 'user' && message.content)
  if (!candidates.length) return null

  return candidates.reduce((latest, message) => {
    if (!latest) return message
    const latestTime = latest.created_at || latest.updated_at || ''
    const messageTime = message.created_at || message.updated_at || ''
    return messageTime.localeCompare(latestTime) >= 0 ? message : latest
  }, null)
}

const chatStateApi = {
  createChatState,
  reduceEvent,
  markConversationRead,
  getLatestRetryableUserMessage,
  createSystemMessageForEvent,
  getCardKind,
  getConversationPreviewText,
  formatClockTime,
  getConversationSelectionMode,
  getConversationSelectionRenderScope,
  getStreamStartAfter,
  getDisplayStatus,
  getEventRenderScope,
  hasMeaningfulBootstrapChange,
  getStatusTone,
  getPeerActionState,
  resolvePostDmRequestSelection,
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = chatStateApi
}

if (typeof window !== 'undefined') {
  window.ParaMindChatState = chatStateApi
}
