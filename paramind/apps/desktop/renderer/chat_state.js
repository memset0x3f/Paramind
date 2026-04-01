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

function normalizeRelationship(relationship) {
  const normalizedStatus = relationship?.relationship || relationship?.status || 'none'
  return {
    peer_id: relationship?.peer_id || relationship?.peerId || '',
    status: normalizedStatus,
    relationship: normalizedStatus,
    conversation_id: relationship?.conversation_id || relationship?.conversationId || null,
    request_id: relationship?.request_id || relationship?.requestId || null,
  }
}

function normalizeBootstrapPayload(payload = {}) {
  const relationships = (payload.relationships || payload.peer_relationships || []).map(normalizeRelationship)
  return {
    activeConversationId: payload.activeConversationId || payload.active_conversation_id || null,
    self: payload.self || null,
    peers: [...(payload.peers || [])],
    conversations: [...(payload.conversations || [])],
    dmRequests: [...(payload.dmRequests || payload.dm_requests || [])],
    groupInvitations: [...(payload.groupInvitations || payload.group_invitations || [])],
    relationships,
    relationshipsByPeerId: new Map(relationships.map((item) => [item.peer_id, item])),
    latestGlobalEventId: Number(payload.latestGlobalEventId || payload.latest_global_event_id || 0),
    latestConversationEventIds: new Map(Object.entries(payload.latestConversationEventIds || payload.latest_conversation_event_ids || {})),
  }
}

function buildSidebarCards(bootstrap) {
  const normalized = normalizeBootstrapPayload(bootstrap)
  const cards = []

  for (const request of normalized.dmRequests) {
    cards.push({
      id: `request:${request.id}`,
      kind: 'request',
      title: 'DM request',
      preview: 'Pending DM request',
      updatedAt: request.updated_at || request.created_at || '',
    })
  }

  for (const invitation of normalized.groupInvitations) {
    cards.push({
      id: `invite:${invitation.id}`,
      kind: 'invitation',
      title: invitation.title || 'Group invitation',
      preview: 'Pending group invitation',
      updatedAt: invitation.updated_at || invitation.created_at || '',
    })
  }

  for (const conversation of normalized.conversations) {
    cards.push({
      id: conversation.id,
      kind: 'conversation',
      title: conversation.title || conversation.id,
      preview: getConversationPreviewText(conversation.last_message?.content || ''),
      updatedAt: conversation.updated_at || '',
    })
  }

  return cards.sort((a, b) => {
    const byTime = String(b.updatedAt || '').localeCompare(String(a.updatedAt || ''))
    if (byTime !== 0) return byTime
    return String(a.id).localeCompare(String(b.id))
  })
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

function getConversationParticipantIdsForDisplay(conversation) {
  return [...(conversation?.participant_ids || [])]
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    .filter((item) => !['assistant', 'ai'].includes(item.toLowerCase()))
}

function getConversationParticipantCount(conversation) {
  return getConversationParticipantIdsForDisplay(conversation).length
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

function hasConversationShellChange(currentConversation, nextConversation) {
  if (!nextConversation) return false
  if (!currentConversation) return true

  return (
    String(currentConversation.title || '') !== String(nextConversation.title || '')
    || String(currentConversation.kind || '') !== String(nextConversation.kind || '')
    || getConversationParticipantIdsForDisplay(currentConversation).join('|')
      !== getConversationParticipantIdsForDisplay(nextConversation).join('|')
  )
}

function shouldRefreshActiveShellForEvent(event, activeConversationId, currentConversation = null) {
  const activeId = String(activeConversationId || '')
  if (!activeId) return false

  const conversationId = String(
    event?.payload?.conversation?.id
      || event?.payload?.conversation_id
      || event?.conversation_id
      || '',
  )

  switch (event?.type) {
    case 'conversation.created':
    case 'conversation.updated':
      return conversationId === activeId
        && hasConversationShellChange(currentConversation, event?.payload?.conversation || null)
    case 'conversation.deleted':
      return conversationId === activeId
    case 'dm.requested':
    case 'dm.accepted':
    case 'dm.rejected': {
      const requestId = String(event?.payload?.request?.id || event?.payload?.request_id || event?.entity_id || '')
      return (requestId ? `request:${requestId}` === activeId : false) || (conversationId === activeId)
    }
    case 'group.invited':
    case 'group.accepted':
    case 'group.rejected': {
      const invitationId = String(event?.payload?.invitation?.id || event?.payload?.invitation_id || event?.entity_id || '')
      return (invitationId ? `invite:${invitationId}` === activeId : false) || (conversationId === activeId)
    }
    default:
      return false
  }
}

function upsertById(items, nextItem) {
  const nextItems = items.slice()
  const index = nextItems.findIndex((item) => item.id === nextItem.id)
  if (index >= 0) {
    nextItems[index] = { ...nextItems[index], ...nextItem }
  } else {
    nextItems.push(nextItem)
  }
  return nextItems
}

function cloneBootstrapViewModel(viewModel) {
  const normalized = normalizeBootstrapPayload(viewModel)
  return {
    ...normalized,
    peers: normalized.peers.map((item) => ({ ...item })),
    conversations: normalized.conversations.map((item) => ({ ...item })),
    dmRequests: normalized.dmRequests.map((item) => ({ ...item })),
    groupInvitations: normalized.groupInvitations.map((item) => ({ ...item })),
    relationships: normalized.relationships.map((item) => ({ ...item })),
    relationshipsByPeerId: new Map(normalized.relationshipsByPeerId),
  }
}

function applyGlobalEvent(viewModel, event) {
  const next = cloneBootstrapViewModel(viewModel)
  const requestId = event?.payload?.request_id || event?.payload?.request?.id || event?.entity_id || null
  const conversation = event?.payload?.conversation || null
  const relationship = event?.payload?.relationship ? normalizeRelationship(event.payload.relationship) : null

  switch (event?.type) {
    case 'peer.joined':
    case 'peer.updated': {
      const peer = event.payload?.peer
      if (peer?.id) {
        next.peers = upsertById(next.peers, peer)
      }
      break
    }
    case 'peer.left': {
      const peerId = event.payload?.peer?.id || event.entity_id
      next.peers = next.peers.filter((item) => item.id !== peerId)
      break
    }
    case 'conversation.created':
    case 'conversation.updated':
      if (conversation?.id) {
        next.conversations = upsertById(next.conversations, conversation)
      }
      break
    case 'conversation.deleted': {
      const conversationId = conversation?.id || event.payload?.conversation_id || event.entity_id
      next.conversations = next.conversations.filter((item) => item.id !== conversationId)
      if (next.activeConversationId === conversationId) {
        next.activeConversationId = null
      }
      break
    }
    case 'dm.requested': {
      const request = event.payload?.request
      if (request?.id) {
        next.dmRequests = upsertById(next.dmRequests, request)
      }
      if (relationship?.peer_id) {
        next.relationshipsByPeerId.set(relationship.peer_id, relationship)
      }
      break
    }
    case 'dm.accepted': {
      next.dmRequests = next.dmRequests.filter((item) => item.id !== requestId)
      if (conversation?.id) {
        next.conversations = upsertById(next.conversations, conversation)
      }
      if (relationship?.peer_id) {
        next.relationshipsByPeerId.set(relationship.peer_id, relationship)
      }
      const selection = resolvePostDmRequestSelection({
        action: 'accept',
        wasViewingRequest: next.activeConversationId === `request:${requestId}`,
        resolvedConversationId: conversation?.id || relationship?.conversation_id || null,
        fallbackConversationId: next.activeConversationId,
      })
      if (selection.force) {
        next.activeConversationId = selection.nextConversationId
      }
      break
    }
    case 'dm.rejected': {
      next.dmRequests = next.dmRequests.filter((item) => item.id !== requestId)
      if (relationship?.peer_id) {
        next.relationshipsByPeerId.set(relationship.peer_id, relationship)
      }
      const selection = resolvePostDmRequestSelection({
        action: 'reject',
        wasViewingRequest: next.activeConversationId === `request:${requestId}`,
        resolvedConversationId: null,
        fallbackConversationId: next.activeConversationId,
      })
      if (selection.force) {
        next.activeConversationId = selection.nextConversationId
      }
      break
    }
    case 'group.invited': {
      const invitation = event.payload?.invitation
      if (invitation?.id) {
        next.groupInvitations = upsertById(next.groupInvitations, invitation)
      }
      break
    }
    case 'group.accepted':
    case 'group.rejected': {
      const invitationId = event.payload?.invitation_id || event.payload?.invitation?.id || event.entity_id
      next.groupInvitations = next.groupInvitations.filter((item) => item.id !== invitationId)
      if (conversation?.id && event.type === 'group.accepted') {
        next.conversations = upsertById(next.conversations, conversation)
      }
      break
    }
    default:
      break
  }

  next.relationships = Array.from(next.relationshipsByPeerId.values())
  next.conversations.sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')) || String(a.id).localeCompare(String(b.id)))
  return next
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
  normalizeBootstrapPayload,
  buildSidebarCards,
  applyGlobalEvent,
  applyConversationEvent: reduceEvent,
  reduceEvent,
  markConversationRead,
  getLatestRetryableUserMessage,
  createSystemMessageForEvent,
  getCardKind,
  getConversationPreviewText,
  getConversationParticipantCount,
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
  hasConversationShellChange,
  shouldRefreshActiveShellForEvent,
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = chatStateApi
}

if (typeof window !== 'undefined') {
  window.ParaMindChatState = chatStateApi
}
