const runtimeWindow = typeof window !== 'undefined' ? window : null
const runtimeDocument = typeof document !== 'undefined' ? document : null

function resolveRuntimeConfig(windowLike = runtimeWindow) {
  return {
    apiBase: windowLike?.electronAPI?.getBackendUrl?.() || 'http://127.0.0.1:5001',
    instanceMeta: windowLike?.electronAPI?.getInstanceMeta?.() || {},
    chatStateApi: windowLike?.ParaMindChatState || null,
  }
}

const RUNTIME_CONFIG = resolveRuntimeConfig(runtimeWindow)
const API_BASE = RUNTIME_CONFIG.apiBase
const INSTANCE_META = RUNTIME_CONFIG.instanceMeta
const CHAT_STATE = RUNTIME_CONFIG.chatStateApi || (typeof require === 'function' ? require('./chat_state.js') : null)

const SCROLL_THRESHOLD_PX = 50

const state = {
  apiBase: API_BASE,
  instanceMeta: INSTANCE_META,
  self: null,
  model: null,
  network: null,
  conversations: [],
  dmRequests: [],
  groupInvitations: [],
  relationshipsByPeerId: new Map(),
  activeConversationId: null,
  messagesByConversation: new Map(),
  peers: [],
  lastRouteByConversation: new Map(),
  lastEventIdByConversation: new Map(),
  processedEventIdsByConversation: new Map(),
  unreadByConversation: new Map(),
  lastSeenMessageIdByConversation: new Map(),
  activeJobId: null,
  activeGlobalStreamAbort: null,
  activeStreamAbort: null,
  activeStreamConversationId: null,
  bootstrapLatestEventId: 0,
  latestGlobalEventId: 0,
  latestConversationEventIds: new Map(),
  bootstrapRefreshTimer: null,
  hasInitialBootstrap: false,
  renderedConversationListSignature: null,
  conversationNodeById: new Map(),
  renderedConversationCardSignatureById: new Map(),
  renderedPeerListSignature: null,
  timelinePaneByConversationId: new Map(),
  timelineRegistryByConversationId: new Map(),
  activeTimelineConversationId: null,
  userScrolledUpByConversation: new Map(),
  unreadWhileScrolledUp: new Map(), // conversationId → count
  renderedRouteSignature: null,
  renderedDiagnosticsSignature: null,
  draftEditStateByMessageId: new Map(),
  composerMode: 'text', // 'text' | 'ai'
}

let chatState = CHAT_STATE?.createChatState ? CHAT_STATE.createChatState() : null
const groupComposerState = {
  mode: 'create',
  conversationId: null,
  title: '',
  eligiblePeers: [],
}

const $ = (id) => runtimeDocument?.getElementById(id)

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

function renderMarkdown(content) {
  if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
    return escapeHtml(content)
  }
  const raw = marked.parse(String(content || ''), { breaks: true, gfm: true })
  return DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } })
}

async function callApi(path, options = {}) {
  const response = await fetch(`${state.apiBase}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })

  if (!response.ok) {
    let message = `Request failed: ${response.status}`
    try {
      const data = await response.json()
      message = data.detail || data.error || message
    } catch (_) {}
    throw new Error(message)
  }

  const contentType = response.headers.get('content-type') || ''
  if (contentType.includes('application/json')) {
    return response.json()
  }
  return response.text()
}

function setComposerStatus(text) {
  $('composerStatus').textContent = text
}

function isRequestConversationId(conversationId) {
  return String(conversationId || '').startsWith('request:')
}

function isGroupInvitationConversationId(conversationId) {
  return String(conversationId || '').startsWith('invite:')
}

function isLifecycleConversationId(conversationId) {
  return isRequestConversationId(conversationId) || isGroupInvitationConversationId(conversationId)
}

function getRequestConversationId(requestId) {
  return `request:${requestId}`
}

function getGroupInvitationConversationId(invitationId) {
  return `invite:${invitationId}`
}

function getRequestIdFromConversationId(conversationId) {
  return String(conversationId || '').replace(/^request:/, '')
}

function getInvitationIdFromConversationId(conversationId) {
  return String(conversationId || '').replace(/^invite:/, '')
}

function getDmRequestById(requestId) {
  return state.dmRequests.find((item) => item.id === requestId) || null
}

function getGroupInvitationById(invitationId) {
  return state.groupInvitations.find((item) => item.id === invitationId) || null
}

function getRelationshipForPeer(peerId) {
  return state.relationshipsByPeerId.get(peerId) || null
}

function getActiveConversation() {
  if (isRequestConversationId(state.activeConversationId)) {
    const request = getDmRequestById(getRequestIdFromConversationId(state.activeConversationId))
    if (!request) return null
    return {
      id: getRequestConversationId(request.id),
      title: getPendingRequestTitle(request),
      kind: 'dm-pending',
      request_id: request.id,
      updated_at: request.updated_at || request.created_at,
    }
  }
  if (isGroupInvitationConversationId(state.activeConversationId)) {
    const invitation = getGroupInvitationById(getInvitationIdFromConversationId(state.activeConversationId))
    if (!invitation) return null
    return {
      id: getGroupInvitationConversationId(invitation.id),
      title: getPendingGroupInvitationTitle(invitation),
      kind: 'group-pending',
      invitation_id: invitation.id,
      updated_at: invitation.updated_at || invitation.created_at,
    }
  }
  return state.conversations.find((item) => item.id === state.activeConversationId) || null
}

function getConversationMessages(conversationId) {
  return state.messagesByConversation.get(conversationId) || []
}

function updateScrollIndicator() {
  const conversationId = state.activeConversationId
  const scrolledUp = state.userScrolledUpByConversation.get(conversationId)
  const unread = state.unreadWhileScrolledUp.get(conversationId) || 0
  const indicator = $('scrollIndicator')
  if (!indicator) return
  if (scrolledUp && unread > 0) {
    $('scrollIndicatorCount').textContent = String(unread)
    indicator.hidden = false
  } else {
    indicator.hidden = true
    if (!scrolledUp) {
      state.unreadWhileScrolledUp.set(conversationId, 0)
    }
  }
}

function getMessageById(conversationId, messageId) {
  return getConversationMessages(conversationId).find((item) => String(item.id) === String(messageId)) || null
}

function ensureLocalDraftMessage(conversationId, messageId) {
  let draft = getMessageById(conversationId, messageId)
  if (draft) return draft
  const messageNode = runtimeDocument?.querySelector(`[data-message-id="${String(messageId)}"]`)
  if (!messageNode) return null
  draft = {
    id: String(messageId),
    conversation_id: conversationId,
    sender_id: 'assistant',
    sender_name: 'AI',
    role: 'assistant',
    status: 'streaming',
    content: messageNode.querySelector(`[data-message-content="${String(messageId)}"]`)?.textContent || '',
    metadata: { local_draft: true },
    created_at: messageNode.dataset.messageCreatedAt || new Date().toISOString(),
    updated_at: new Date().toISOString(),
  }
  upsertMessage(draft)
  return draft
}

function getDraftEditState(messageId) {
  return state.draftEditStateByMessageId.get(String(messageId)) || null
}

function setDraftEditState(messageId, nextState) {
  state.draftEditStateByMessageId.set(String(messageId), nextState)
}

function clearDraftEditState(messageId) {
  state.draftEditStateByMessageId.delete(String(messageId))
}

function focusDraftEditor(messageId) {
  runtimeWindow?.requestAnimationFrame?.(() => {
    const editor = runtimeDocument?.querySelector(`textarea[data-message-editor="${String(messageId)}"]`)
    if (!editor) return
    editor.focus()
    const length = editor.value.length
    try {
      editor.setSelectionRange(length, length)
    } catch (_) {}
  })
}

function getLatestMessage(conversationId, role) {
  const messages = getConversationMessages(conversationId)
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (!role || messages[index].role === role) return messages[index]
  }
  return null
}

function getLatestRetryableUserMessage(conversationId) {
  if (CHAT_STATE?.getLatestRetryableUserMessage) {
    return CHAT_STATE.getLatestRetryableUserMessage({
      messagesByConversation: state.messagesByConversation,
    }, conversationId)
  }
  return getLatestMessage(conversationId, 'user')
}

function isAiRequestContent(content) {
  return /^@AI\s+.+/is.test(String(content || ''))
}

function hasRetryableAssistantTurn(conversationId) {
  const latestAssistant = getConversationMessages(conversationId)
    .filter((message) => message.role === 'assistant' && message.metadata?.local_draft !== true)
    .at(-1) || null
  const latestUser = getLatestRetryableUserMessage(conversationId)
  return !!(
    latestAssistant
    && latestUser
    && isAiRequestContent(latestUser.content)
    && ['completed', 'failed', 'cancelled'].includes(latestAssistant.status)
  )
}

function touchConversationReadState(conversationId, messageId, { clearUnread = false } = {}) {
  if (!conversationId) return
  if (messageId) state.lastSeenMessageIdByConversation.set(conversationId, messageId)
  if (clearUnread) state.unreadByConversation.set(conversationId, 0)
}

function getUnreadCount(conversationId) {
  return state.unreadByConversation.get(conversationId) || 0
}

function incrementUnread(conversationId) {
  if (!conversationId) return
  const current = state.unreadByConversation.get(conversationId) || 0
  state.unreadByConversation.set(conversationId, current + 1)
}

function getPeerRenderSignature(peer) {
  return JSON.stringify({
    id: peer?.id || '',
    display_name: peer?.display_name || '',
    status: peer?.status || '',
    backend_port: peer?.backend_port || 0,
    device: peer?.capabilities?.device || '',
  })
}

function getCounterpartPeerId(conversation) {
  if (!conversation || conversation.kind !== 'dm') return null
  const ids = conversation.participant_ids || []
  return ids.find((id) => id !== state.self?.id) || null
}

function getPeerDisplayName(peerId) {
  return state.peers.find((peer) => peer.id === peerId)?.display_name || peerId
}

function getConversationDisplayTitle(conversation) {
  if (!conversation) return 'No conversation selected'
  if (conversation.kind !== 'dm') return conversation.title
  const counterpartId = getCounterpartPeerId(conversation)
  return getPeerDisplayName(counterpartId)
}

function getFallbackConversationId(excludingConversationId = null) {
  return buildConversationCards().find((item) => item.id === 'general')?.id
    || buildConversationCards().find((item) => !isLifecycleConversationId(item.id) && item.id !== excludingConversationId)?.id
    || null
}

function getPendingGroupInviteTargets(conversationId) {
  return new Set(
    state.groupInvitations
      .filter((invitation) => invitation.status === 'pending' && invitation.conversation_id === conversationId)
      .map((invitation) => invitation.target_peer_id),
  )
}

function getEligiblePeersForGroupComposer({ conversationId = null } = {}) {
  const existingParticipants = new Set()
  if (conversationId) {
    const conversation = state.conversations.find((item) => item.id === conversationId)
    for (const peerId of conversation?.participant_ids || []) existingParticipants.add(peerId)
  }
  existingParticipants.add(state.self?.id)
  const pendingTargets = conversationId ? getPendingGroupInviteTargets(conversationId) : new Set()
  return state.peers.filter((peer) => (
    peer.id !== state.self?.id
    && peer.status === 'online'
    && !existingParticipants.has(peer.id)
    && !pendingTargets.has(peer.id)
  ))
}

function getPendingRequestTitle(request) {
  const counterpartId =
    request.requester_id === state.self?.id ? request.target_peer_id : request.requester_id
  return `私聊请求 · ${getPeerDisplayName(counterpartId)}`
}

function getPendingRequestPreview(request) {
  if (request.status !== 'pending') {
    return request.status === 'accepted' ? '请求已接受' : '请求已拒绝'
  }
  return request.direction === 'outbound' ? '等待对方同意' : '等待你处理'
}

function getPendingGroupInvitationTitle(invitation) {
  return `群邀请 · ${invitation.title}`
}

function getPendingGroupInvitationPreview(invitation) {
  if (invitation.status !== 'pending') {
    return invitation.status === 'accepted' ? '邀请已接受' : '邀请已拒绝'
  }
  return invitation.direction === 'outbound' ? '等待成员回应' : '等待你处理'
}

function getDmConversationForPeer(peerId) {
  return state.conversations.find((conversation) => {
    const ids = conversation.participant_ids || []
    return conversation.kind === 'dm' && ids.includes(peerId) && ids.includes(state.self?.id)
  }) || null
}

function getPendingDmRequestForPeer(peerId) {
  return state.dmRequests.find((request) => {
    if (request.status !== 'pending') return false
    return (
      (request.requester_id === state.self?.id && request.target_peer_id === peerId)
      || (request.requester_id === peerId && request.target_peer_id === state.self?.id)
    )
  }) || null
}

function buildConversationCards() {
  const cards = state.conversations.map((conversation) => ({
    id: conversation.id,
    title: getConversationDisplayTitle(conversation),
    kind: conversation.kind === 'dm' ? 'conversation.dm' : 'conversation.group',
    subtitle: CHAT_STATE.getConversationPreviewText(conversation.last_message?.content || ''),
    updated_at: conversation.updated_at,
    unread: getUnreadCount(conversation.id),
    raw: conversation,
  }))

  for (const request of state.dmRequests) {
    if (request.status !== 'pending') continue
    cards.push({
      id: getRequestConversationId(request.id),
      title: getPendingRequestTitle(request),
      kind: 'conversation.dm-pending',
      subtitle: getPendingRequestPreview(request),
      updated_at: request.updated_at || request.created_at,
      unread: getUnreadCount(getRequestConversationId(request.id)),
      raw: request,
    })
  }

  for (const invitation of state.groupInvitations) {
    if (invitation.status !== 'pending') continue
    cards.push({
      id: getGroupInvitationConversationId(invitation.id),
      title: getPendingGroupInvitationTitle(invitation),
      kind: 'conversation.group-pending',
      subtitle: getPendingGroupInvitationPreview(invitation),
      updated_at: invitation.updated_at || invitation.created_at,
      unread: getUnreadCount(getGroupInvitationConversationId(invitation.id)),
      raw: invitation,
    })
  }

  cards.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
  return cards
}

function upsertConversation(conversation) {
  const existingIndex = state.conversations.findIndex((item) => item.id === conversation.id)
  if (existingIndex >= 0) {
    // Preserve existing updated_at to prevent signature change on every refresh
    const existingUpdatedAt = state.conversations[existingIndex].updated_at
    state.conversations[existingIndex] = { ...state.conversations[existingIndex], ...conversation, updated_at: existingUpdatedAt }
  } else {
    state.conversations.unshift(conversation)
  }
  state.conversations.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
  if (!state.unreadByConversation.has(conversation.id)) {
    state.unreadByConversation.set(conversation.id, 0)
  }
  if (!state.lastSeenMessageIdByConversation.has(conversation.id)) {
    state.lastSeenMessageIdByConversation.set(conversation.id, conversation.last_message?.id || null)
  }
}

function upsertDmRequest(request) {
  const existingIndex = state.dmRequests.findIndex((item) => item.id === request.id)
  if (existingIndex >= 0) {
    state.dmRequests[existingIndex] = { ...state.dmRequests[existingIndex], ...request }
  } else {
    state.dmRequests.unshift(request)
  }
  state.dmRequests.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
}

function upsertGroupInvitation(invitation) {
  const existingIndex = state.groupInvitations.findIndex((item) => item.id === invitation.id)
  if (existingIndex >= 0) {
    state.groupInvitations[existingIndex] = { ...state.groupInvitations[existingIndex], ...invitation }
  } else {
    state.groupInvitations.unshift(invitation)
  }
  state.groupInvitations.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
}

function removeGroupInvitation(invitationId) {
  state.groupInvitations = state.groupInvitations.filter((item) => item.id !== invitationId)
  state.unreadByConversation.delete(getGroupInvitationConversationId(invitationId))
}

function syncRelationshipsFromBootstrap(relationships) {
  state.relationshipsByPeerId = new Map(
    (relationships || []).map((item) => [item.peer_id, item]),
  )
}

function upsertRelationship(relationship) {
  if (!relationship?.peer_id) return
  state.relationshipsByPeerId.set(relationship.peer_id, relationship)
}

function upsertMessage(message) {
  const current = state.messagesByConversation.get(message.conversation_id) || []
  const index = current.findIndex((item) => item.id === message.id)
  if (index >= 0) current[index] = message
  else current.push(message)
  current.sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''))
  state.messagesByConversation.set(message.conversation_id, current)
  chatState.messagesByConversation.set(message.conversation_id, current.slice())

  const conversation = state.conversations.find((item) => item.id === message.conversation_id)
  if (conversation && message.metadata?.local_draft !== true) {
    conversation.last_message = message
    conversation.updated_at = message.updated_at || message.created_at
  }

  touchConversationReadState(message.conversation_id, message.id, {
    clearUnread: message.conversation_id === state.activeConversationId,
  })
}

function removeMessage(conversationId, messageId) {
  const current = state.messagesByConversation.get(conversationId) || []
  const next = current.filter((item) => item.id !== messageId)
  state.messagesByConversation.set(conversationId, next)
  chatState.messagesByConversation.set(conversationId, next.slice())
  clearDraftEditState(messageId)
}

function syncUnreadFromBootstrap(conversations) {
  for (const conversation of conversations) {
    upsertConversation(conversation)
    const latestMessageId = conversation.last_message?.id || null
    const seenMessageId = state.lastSeenMessageIdByConversation.get(conversation.id)

    if (!state.hasInitialBootstrap) {
      state.lastSeenMessageIdByConversation.set(conversation.id, latestMessageId)
      state.unreadByConversation.set(conversation.id, 0)
      continue
    }

    if (conversation.id === state.activeConversationId) {
      state.lastSeenMessageIdByConversation.set(conversation.id, latestMessageId)
      state.unreadByConversation.set(conversation.id, 0)
      continue
    }

    if (latestMessageId && latestMessageId !== seenMessageId) {
      incrementUnread(conversation.id)
      state.lastSeenMessageIdByConversation.set(conversation.id, latestMessageId)
    }
  }
}

function syncDmRequestsFromBootstrap(dmRequests) {
  state.dmRequests = []
  for (const request of dmRequests || []) {
    upsertDmRequest(request)
    const requestConversationId = getRequestConversationId(request.id)
    if (!state.unreadByConversation.has(requestConversationId)) {
      state.unreadByConversation.set(requestConversationId, 0)
    }
  }
}

function syncGroupInvitationsFromBootstrap(groupInvitations) {
  state.groupInvitations = []
  for (const invitation of groupInvitations || []) {
    upsertGroupInvitation(invitation)
    const conversationId = getGroupInvitationConversationId(invitation.id)
    if (!state.unreadByConversation.has(conversationId)) {
      state.unreadByConversation.set(conversationId, 0)
    }
  }
}

function removeDmRequest(requestId) {
  state.dmRequests = state.dmRequests.filter((item) => item.id !== requestId)
  state.unreadByConversation.delete(getRequestConversationId(requestId))
}

function removeConversation(conversationId) {
  const messages = state.messagesByConversation.get(conversationId) || []
  messages.forEach((message) => clearDraftEditState(message.id))
  state.conversations = state.conversations.filter((item) => item.id !== conversationId)
  state.messagesByConversation.delete(conversationId)
  state.unreadByConversation.delete(conversationId)
  state.lastSeenMessageIdByConversation.delete(conversationId)
  const pane = state.timelinePaneByConversationId.get(conversationId)
  if (pane) pane.remove()
  state.timelinePaneByConversationId.delete(conversationId)
  state.timelineRegistryByConversationId.delete(conversationId)
  state.lastEventIdByConversation.delete(conversationId)
  state.processedEventIdsByConversation.delete(conversationId)
}

function upsertPeer(peer) {
  const index = state.peers.findIndex((item) => item.id === peer.id)
  let changed = false
  if (index >= 0) {
    changed = getPeerRenderSignature(state.peers[index]) !== getPeerRenderSignature(peer)
    state.peers[index] = peer
  } else {
    state.peers.push(peer)
    changed = true
  }
  state.peers.sort((a, b) => a.display_name.localeCompare(b.display_name))
  return changed
}

function syncDefaultGroupParticipantsFromPeers() {
  const generalConversation = state.conversations.find((item) => item.id === 'general' && item.kind === 'group')
  if (!generalConversation) return false

  const nextParticipantIds = Array.from(new Set(
    state.peers
      .map((peer) => String(peer?.id || '').trim())
      .filter(Boolean)
      .filter((peerId) => !['assistant', 'ai'].includes(peerId.toLowerCase())),
  )).sort()

  const currentParticipantIds = Array.from(new Set(
    (generalConversation.participant_ids || [])
      .map((peerId) => String(peerId || '').trim())
      .filter(Boolean)
      .filter((peerId) => !['assistant', 'ai'].includes(peerId.toLowerCase())),
  )).sort()

  if (currentParticipantIds.join('|') === nextParticipantIds.join('|')) {
    return false
  }

  generalConversation.participant_ids = nextParticipantIds
  return true
}

function renderConversationCard(card) {
  return `
    <div class="conversation-row">
      <div class="title">${escapeHtml(card.title)}</div>
      ${card.unread ? `<span class="conversation-badge">${escapeHtml(card.unread)}</span>` : ''}
    </div>
    <div class="subtitle">${escapeHtml(card.subtitle)}</div>
    <div class="conversation-kind">${escapeHtml(card.kind.replace('conversation.', ''))}</div>
  `
}

function ensureConversationNodeStructure(node) {
  if (node.querySelector('.conversation-row') && node.querySelector('.subtitle') && node.querySelector('.conversation-kind')) {
    return
  }
  node.innerHTML = `
    <div class="conversation-row">
      <div class="title"></div>
    </div>
    <div class="subtitle"></div>
    <div class="conversation-kind"></div>
  `
}

function patchConversationNode(node, card) {
  const renderSignature = JSON.stringify({
    title: card.title,
    subtitle: card.subtitle,
    kind: card.kind,
    unread: card.unread,
    active: card.id === state.activeConversationId,
  })
  if (node.dataset.renderSignature === renderSignature) return
  node.className = `conversation${card.id === state.activeConversationId ? ' active' : ''}`
  node.dataset.conversationId = card.id
  node.dataset.renderSignature = renderSignature
  ensureConversationNodeStructure(node)

  const titleNode = node.querySelector('.title')
  const subtitleNode = node.querySelector('.subtitle')
  const kindNode = node.querySelector('.conversation-kind')
  const rowNode = node.querySelector('.conversation-row')
  let badgeNode = rowNode?.querySelector('.conversation-badge') || null

  if (titleNode) titleNode.textContent = card.title
  if (subtitleNode) subtitleNode.textContent = card.subtitle
  if (kindNode) kindNode.textContent = card.kind.replace('conversation.', '')

  if (card.unread) {
    if (!badgeNode && rowNode) {
      badgeNode = document.createElement('span')
      badgeNode.className = 'conversation-badge'
      rowNode.appendChild(badgeNode)
    }
    if (badgeNode) badgeNode.textContent = String(card.unread)
  } else if (badgeNode) {
    badgeNode.remove()
  }
}

function renderConversations() {
  const list = $('conversationList')
  const cards = buildConversationCards()
  const signature = JSON.stringify(cards.map((card) => ({
    id: card.id,
    title: card.title,
    subtitle: card.subtitle,
    kind: card.kind,
    unread: card.unread,
  })))

  if (signature === state.renderedConversationListSignature) {
    patchConversationSelection()
    return
  }

  if (!cards.length) {
    state.conversationNodeById.clear()
    state.renderedConversationCardSignatureById.clear()
    list.innerHTML = ''
    list.innerHTML = `<div class="empty">
      <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        <line x1="9" y1="9" x2="15" y2="9"/>
        <line x1="9" y1="13" x2="13" y2="13"/>
      </svg>
      还没有会话。<br>创建一个群聊，或等另一实例上线。
    </div>`
    return
  }

  list.querySelector('.empty')?.remove()
  const nextIds = new Set(cards.map((card) => card.id))
  for (const [conversationId, node] of Array.from(state.conversationNodeById.entries())) {
    if (!nextIds.has(conversationId)) {
      node.remove()
      state.conversationNodeById.delete(conversationId)
      state.renderedConversationCardSignatureById.delete(conversationId)
    }
  }

  cards.forEach((card, index) => {
    let item = state.conversationNodeById.get(card.id)
    if (!item) {
      item = document.createElement('button')
      item.addEventListener('click', () => selectConversation(card.id))
      state.conversationNodeById.set(card.id, item)
    }
    const currentSignature = JSON.stringify({
      title: card.title,
      subtitle: card.subtitle,
      kind: card.kind,
      unread: card.unread,
      active: card.id === state.activeConversationId,
    })
    if (state.renderedConversationCardSignatureById.get(card.id) !== currentSignature) {
      patchConversationNode(item, card)
      state.renderedConversationCardSignatureById.set(card.id, currentSignature)
    }
    const desiredNode = list.children[index] || null
    if (desiredNode !== item) {
      list.insertBefore(item, desiredNode)
    }
  })
  state.renderedConversationListSignature = signature
}

function patchConversationSelection() {
  $('conversationList').querySelectorAll('.conversation').forEach((node) => {
    node.classList.toggle('active', node.dataset.conversationId === state.activeConversationId)
  })
}

function renderDraftCard(message, conversationId) {
  const time = CHAT_STATE.formatClockTime(message.created_at)
  const statusTone = CHAT_STATE.getStatusTone(message)
  const editState = getDraftEditState(message.id)
  const content = editState
    ? `
      <div class="message-card draft-card draft-card-editing">
        <textarea
          class="draft-editor"
          data-message-editor="${escapeHtml(message.id)}"
          data-message-id="${escapeHtml(message.id)}"
          ${editState.saving ? 'disabled' : ''}
        >${escapeHtml(editState.content || '')}</textarea>
      </div>
    `
    : `<div class="message-card draft-card prose" data-message-content="${escapeHtml(message.id)}">${renderMarkdown(message.content || '生成中…')}</div>`
  const actions = editState
    ? `
      <div class="draft-actions">
        <button type="button" class="draft-save-btn" data-message-id="${escapeHtml(message.id)}" ${editState.saving ? 'disabled' : ''}><svg class="ico ico-sm" style="display:inline;width:12px;height:12px"><use href="#ico-check"/></svg> 保存</button>
        <button type="button" class="draft-cancel-btn" data-message-id="${escapeHtml(message.id)}" ${editState.saving ? 'disabled' : ''}><svg class="ico ico-sm" style="display:inline;width:12px;height:12px"><use href="#ico-x"/></svg> 取消</button>
      </div>
    `
    : `
      <div class="draft-actions">
        <button type="button" class="draft-edit-btn" data-message-id="${escapeHtml(message.id)}"><svg class="ico ico-sm" style="display:inline;width:12px;height:12px"><use href="#ico-edit"/></svg> 编辑</button>
        <button type="button" class="draft-copy-btn" data-message-id="${escapeHtml(message.id)}"><svg class="ico ico-sm" style="display:inline;width:12px;height:12px"><use href="#ico-copy"/></svg> 复制</button>
        <button type="button" class="draft-send-btn" data-message-id="${escapeHtml(message.id)}" data-conversation-id="${escapeHtml(conversationId)}"><svg class="ico ico-sm" style="display:inline;width:12px;height:12px"><use href="#ico-send"/></svg> 发送</button>
        <button type="button" class="draft-delete-btn" data-message-id="${escapeHtml(message.id)}" data-conversation-id="${escapeHtml(conversationId)}"><svg class="ico ico-sm" style="display:inline;width:12px;height:12px"><use href="#ico-trash"/></svg> 删除</button>
      </div>
    `
  return `
    <div class="message ai-draft${editState ? ' editing' : ''}" data-message-id="${escapeHtml(message.id)}" data-card-kind="message.ai-draft">
      <div class="avatar">AI</div>
      <div style="flex:1;min-width:0;">
        <div class="message-meta">
          <strong>AI 草稿</strong>
          <span class="status-pill status-${escapeHtml(statusTone)}" data-message-status="${escapeHtml(message.id)}">${escapeHtml(CHAT_STATE.getDisplayStatus(message))}</span>
          <span data-message-time="${escapeHtml(message.id)}">${escapeHtml(time)}</span>
        </div>
        ${content}
        ${actions}
      </div>
    </div>
  `
}

function renderRequestConversation(request) {
  const isInbound = request.direction === 'inbound'
  const counterpartId = isInbound ? request.requester_id : request.target_peer_id
  const counterpartName = getPeerDisplayName(counterpartId)
  const statusTone = request.status === 'accepted' ? 'completed' : request.status === 'rejected' ? 'failed' : 'pending'
  const statusText = request.status === 'pending'
    ? (isInbound ? '等待你决定是否接受私聊。' : '等待对方同意你的私聊请求。')
    : (request.status === 'accepted' ? '该请求已被接受。' : '该请求已被拒绝。')

  const actions = request.status === 'pending'
    ? (
      isInbound
        ? `
          <div class="request-actions">
            <button type="button" class="primary accept-request-btn" data-request-id="${escapeHtml(request.id)}"><svg class="ico ico-sm" style="display:inline;width:12px;height:12px"><use href="#ico-check"/></svg> 接受</button>
            <button type="button" class="danger reject-request-btn" data-request-id="${escapeHtml(request.id)}"><svg class="ico ico-sm" style="display:inline;width:12px;height:12px"><use href="#ico-x"/></svg> 拒绝</button>
          </div>
        `
        : `<div class="request-actions"><button type="button" disabled class="pending">等待回应</button></div>`
    )
    : ''

  return `
    <div class="message request" data-request-id="${escapeHtml(request.id)}">
      <div class="avatar">?</div>
      <div style="flex:1;min-width:0;">
        <div class="message-meta">
          <strong>私聊请求</strong>
          <span class="status-pill status-${escapeHtml(statusTone)}">${escapeHtml(request.status)}</span>
        </div>
        <div class="message-card">${escapeHtml(counterpartName)}<br>${escapeHtml(statusText)}</div>
        ${actions}
      </div>
    </div>
  `
}

function renderGroupInvitation(invitation) {
  const isInbound = invitation.direction === 'inbound'
  const statusTone = invitation.status === 'accepted' ? 'completed' : invitation.status === 'rejected' ? 'failed' : 'pending'
  const statusText = invitation.status === 'pending'
    ? (isInbound ? '等待你决定是否加入群聊。' : '等待成员回应群邀请。')
    : (invitation.status === 'accepted' ? '该邀请已被接受。' : '该邀请已被拒绝。')
  const actions = invitation.status === 'pending'
    ? (
      isInbound
        ? `
          <div class="request-actions">
            <button type="button" class="primary accept-group-invite-btn" data-invitation-id="${escapeHtml(invitation.id)}">接受</button>
            <button type="button" class="danger reject-group-invite-btn" data-invitation-id="${escapeHtml(invitation.id)}">拒绝</button>
          </div>
        `
        : `<div class="request-actions"><button type="button" disabled class="pending">等待回应</button></div>`
    )
    : ''
  return `
    <div class="message request" data-invitation-id="${escapeHtml(invitation.id)}">
      <div class="avatar">G</div>
      <div style="flex:1;min-width:0;">
        <div class="message-meta">
          <strong>群邀请</strong>
          <span class="status-pill status-${escapeHtml(statusTone)}">${escapeHtml(invitation.status)}</span>
        </div>
        <div class="message-card">${escapeHtml(invitation.title)}<br>${escapeHtml(statusText)}</div>
        ${actions}
      </div>
    </div>
  `
}

function updateGroupComposerSubmitState() {
  const selectedCount = runtimeDocument?.querySelectorAll('#groupComposerPeerList input[type="checkbox"]:checked').length || 0
  const title = String($('groupComposerTitle')?.value || '').trim()
  const requiresTitle = groupComposerState.mode === 'create'
  $('groupComposerSubmitBtn').disabled = selectedCount === 0 || (requiresTitle && !title)
}

function renderGroupComposerPeerList() {
  const list = $('groupComposerPeerList')
  if (!list) return
  if (!groupComposerState.eligiblePeers.length) {
    list.innerHTML = '<div class="peer-picker-empty">当前没有可邀请的新成员。</div>'
    updateGroupComposerSubmitState()
    return
  }
  list.innerHTML = groupComposerState.eligiblePeers.map((peer) => `
    <label class="peer-picker-item">
      <input type="checkbox" value="${escapeHtml(peer.id)}" aria-label="${escapeHtml(peer.display_name)}" />
      <div class="peer-picker-copy">
        <div class="title">${escapeHtml(peer.display_name)}</div>
        <div class="subtitle">${escapeHtml(peer.id)} · ${escapeHtml(peer.status)}</div>
      </div>
    </label>
  `).join('')
  list.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => {
    checkbox.addEventListener('change', updateGroupComposerSubmitState)
  })
  updateGroupComposerSubmitState()
}

function closeGroupComposer() {
  $('groupComposerModal').hidden = true
  $('groupComposerTitle').value = ''
  $('groupComposerPeerList').innerHTML = ''
  $('groupComposerSubmitBtn').disabled = true
  groupComposerState.mode = 'create'
  groupComposerState.conversationId = null
  groupComposerState.title = ''
  groupComposerState.eligiblePeers = []
}

function openGroupComposer({ mode = 'create', conversationId = null } = {}) {
  const conversation = conversationId
    ? state.conversations.find((item) => item.id === conversationId) || null
    : null
  groupComposerState.mode = mode
  groupComposerState.conversationId = conversationId
  groupComposerState.title = conversation?.title || ''
  groupComposerState.eligiblePeers = getEligiblePeersForGroupComposer({ conversationId })

  $('groupComposerHeading').textContent = mode === 'invite' ? '邀请成员' : '新建群聊'
  $('groupComposerSubheading').textContent = mode === 'invite'
    ? '选择尚未加入该群聊的节点。'
    : '输入群聊名称并选择至少一个成员。'
  $('groupComposerTitle').value = mode === 'invite' ? (conversation?.title || '') : ''
  $('groupComposerTitle').disabled = mode === 'invite'
  $('groupComposerTitle').placeholder = mode === 'invite' ? '当前群聊名称' : '例如：Project Alpha'
  $('groupComposerSubmitBtn').textContent = mode === 'invite' ? '发送邀请' : '创建群聊'
  renderGroupComposerPeerList()
  $('groupComposerModal').hidden = false
  if (mode === 'create') $('groupComposerTitle').focus()
}

async function submitGroupComposer() {
  const selectedPeerIds = Array.from(runtimeDocument?.querySelectorAll('#groupComposerPeerList input[type="checkbox"]:checked') || [])
    .map((node) => node.value)
    .filter(Boolean)
  if (!selectedPeerIds.length) return

  try {
    if (groupComposerState.mode === 'invite' && groupComposerState.conversationId) {
      const response = await callApi('/api/group/invitations', {
        method: 'POST',
        body: JSON.stringify({
          conversation_id: groupComposerState.conversationId,
          target_peer_ids: selectedPeerIds,
        }),
      })
      if (response?.conversation) upsertConversation(response.conversation)
      for (const invitation of response?.invitations || []) upsertGroupInvitation(invitation)
      renderConversations()
      renderMessages()
      setComposerStatus('群邀请已发送')
      closeGroupComposer()
      return
    }

    const title = String($('groupComposerTitle').value || '').trim()
    if (!title) return
    const created = await callApi('/api/group/invitations', {
      method: 'POST',
      body: JSON.stringify({ title, target_peer_ids: selectedPeerIds }),
    })
    if (created?.conversation) {
      upsertConversation(created.conversation)
    }
    for (const invitation of created?.invitations || []) {
      upsertGroupInvitation(invitation)
    }
    closeGroupComposer()
    renderConversations()
    if (created?.conversation?.id) {
      await selectConversation(created.conversation.id)
    }
  } catch (error) {
    setComposerStatus(error.message)
  }
}

function renderRoomActions() {
  const conversation = getActiveConversation()
  const isInvitableGroup = conversation?.kind === 'group' && conversation?.id !== 'general'
  $('inviteGroupBtn').hidden = !isInvitableGroup
  $('leaveGroupBtn').hidden = !isInvitableGroup
}

function renderBaseMessageCard(message, cssRole, content, actions = '') {
  const role = message.role || 'system'
  const senderInitial = (message.sender_name || role || '?').slice(0, 1).toUpperCase()
  const time = CHAT_STATE.formatClockTime(message.created_at)
  const statusTone = CHAT_STATE.getStatusTone(message)
  return `
    <div class="message ${cssRole} ${message.status === 'failed' ? 'failed' : ''}" data-message-id="${escapeHtml(message.id)}" data-card-kind="${escapeHtml(CHAT_STATE.getCardKind(message))}">
      <div class="avatar">${escapeHtml(senderInitial)}</div>
      <div style="flex:1;min-width:0;">
        <div class="message-meta">
          <strong>${escapeHtml(message.sender_name || role)}</strong>
          <span class="status-pill status-${escapeHtml(statusTone)}" data-message-status="${escapeHtml(message.id)}">${escapeHtml(CHAT_STATE.getDisplayStatus(message))}</span>
          <span data-message-time="${escapeHtml(message.id)}">${escapeHtml(time)}</span>
        </div>
        <div class="message-card prose" data-message-content="${escapeHtml(message.id)}">${renderMarkdown(content)}</div>
        <div data-message-actions="${escapeHtml(message.id)}">${actions}</div>
      </div>
    </div>
  `
}

function renderUserMessageCard(message) {
  return renderBaseMessageCard(message, 'user', message.content || '')
}

function renderPeerMessageCard(message) {
  return renderBaseMessageCard(message, 'peer', message.content || '')
}

function renderPublishedAIMessageCard(message, conversationId) {
  const latestAssistant = getConversationMessages(conversationId)
    .filter((item) => CHAT_STATE.getCardKind(item) === 'message.ai')
    .at(-1) || null
  const retryActions = hasRetryableAssistantTurn(conversationId) && latestAssistant?.id === message.id
    ? `<div class="message-actions"><button type="button" class="primary retry-btn" data-conversation-id="${escapeHtml(conversationId)}"><svg class="ico ico-sm" style="display:inline;width:12px;height:12px"><use href="#ico-retry"/></svg> 重试此轮</button></div>`
    : ''
  return renderBaseMessageCard(message, 'assistant', message.content || '...', retryActions)
}

function renderSystemMessageCard(message) {
  return renderBaseMessageCard(message, 'system', message.content || '')
}

function renderRequestMessageCard(message) {
  return renderBaseMessageCard(message, 'system', message.content || '')
}

function renderTimelineCard(message, conversationId) {
  switch (CHAT_STATE.getCardKind(message)) {
    case 'message.ai-draft':
      return renderDraftCard(message, conversationId)
    case 'message.user':
      return renderUserMessageCard(message)
    case 'message.peer':
      return renderPeerMessageCard(message)
    case 'message.ai':
      return renderPublishedAIMessageCard(message, conversationId)
    case 'message.request':
      return renderRequestMessageCard(message)
    default:
      return renderSystemMessageCard(message)
  }
}

function createNodeFromHTML(html) {
  const template = document.createElement('template')
  template.innerHTML = html.trim()
  return template.content.firstElementChild
}

function createTimelineRegistry() {
  return {
    messageNodeById: new Map(),
    messageContentNodeById: new Map(),
    messageStatusNodeById: new Map(),
    messageActionNodeById: new Map(),
    messageTimeNodeById: new Map(),
  }
}

function syncMessageNodeOrderKey(node, message) {
  if (!node) return
  node.dataset.messageCreatedAt = String(message?.created_at || message?.updated_at || '')
}

function indexMessageNode(registry, node) {
  if (!node) return
  const messageId = node.dataset.messageId
  if (!messageId) return
  registry.messageNodeById.set(messageId, node)
  const contentNode = node.querySelector('[data-message-content]')
  const statusNode = node.querySelector('[data-message-status]')
  const actionNode = node.querySelector('[data-message-actions]')
  const timeNode = node.querySelector('[data-message-time]')
  if (contentNode) registry.messageContentNodeById.set(messageId, contentNode)
  if (statusNode) registry.messageStatusNodeById.set(messageId, statusNode)
  if (actionNode) registry.messageActionNodeById.set(messageId, actionNode)
  if (timeNode) registry.messageTimeNodeById.set(messageId, timeNode)
}

function dropMessageNodeFromRegistry(registry, messageId) {
  registry.messageNodeById.delete(String(messageId))
  registry.messageContentNodeById.delete(String(messageId))
  registry.messageStatusNodeById.delete(String(messageId))
  registry.messageActionNodeById.delete(String(messageId))
  registry.messageTimeNodeById.delete(String(messageId))
}

function getOrCreateTimelinePane(conversationId) {
  let pane = state.timelinePaneByConversationId.get(conversationId)
  let registry = state.timelineRegistryByConversationId.get(conversationId)
  if (!pane) {
    pane = document.createElement('div')
    pane.className = 'timeline-pane'
    pane.dataset.conversationId = conversationId
    state.timelinePaneByConversationId.set(conversationId, pane)
  }
  if (!registry) {
    registry = createTimelineRegistry()
    state.timelineRegistryByConversationId.set(conversationId, registry)
  }
  return { pane, registry }
}

function renderEmptyTimelinePane(message) {
  const pane = document.createElement('div')
  pane.className = 'timeline-pane empty-pane'
  pane.innerHTML = `<div class="empty">
      <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        <line x1="9" y1="9" x2="15" y2="9"/>
        <line x1="9" y1="13" x2="13" y2="13"/>
      </svg>
      ${message}
    </div>`
  return pane
}

function buildTimelinePane(conversationId) {
  const { pane, registry } = getOrCreateTimelinePane(conversationId)

  // If registry already has nodes (cached), skip rebuild - nodes are already in DOM
  if (registry.messageNodeById.size > 0) {
    pane.querySelectorAll('.empty').forEach((n) => n.remove())
    reorderTimelineNodes(conversationId)
    return pane
  }

  pane.replaceChildren()
  registry.messageNodeById.clear()
  registry.messageContentNodeById.clear()
  registry.messageStatusNodeById.clear()
  const conversation = state.conversations.find((item) => item.id === conversationId)
  const messages = getConversationMessages(conversationId)
  if (!conversation) {
    pane.appendChild(renderEmptyTimelinePane('选择一个会话开始聊天。'))
    return pane
  }
  if (!messages.length) {
    pane.appendChild(renderEmptyTimelinePane('这个会话还没有消息。<br>发送第一条消息开始。'))
    return pane
  }
  for (const message of messages) {
    const node = createNodeFromHTML(renderTimelineCard(message, conversation.id))
    syncMessageNodeOrderKey(node, message)
    pane.appendChild(node)
    indexMessageNode(registry, node)
  }
  return pane
}

function reorderTimelineNodes(conversationId) {
  const pane = state.timelinePaneByConversationId.get(conversationId)
  const registry = state.timelineRegistryByConversationId.get(conversationId)
  if (!pane || !registry) return
  const orderedNodes = Array.from(registry.messageNodeById.values())
    .filter((node) => node?.parentElement === pane)
    .sort((a, b) => {
      const aTime = String(a.dataset.messageCreatedAt || '')
      const bTime = String(b.dataset.messageCreatedAt || '')
      return aTime.localeCompare(bTime) || String(a.dataset.messageId || '').localeCompare(String(b.dataset.messageId || ''))
    })
  orderedNodes.forEach((node, index) => {
    const desiredNode = pane.children[index] || null
    if (desiredNode !== node) {
      pane.insertBefore(node, desiredNode)
    }
  })
}

function replaceTimelineNodePreservingPosition(conversationId, registry, existingNode, replacement) {
  const pane = state.timelinePaneByConversationId.get(conversationId)
  if (!pane) return
  if (existingNode?.parentElement === pane) {
    existingNode.replaceWith(replacement)
    return
  }
  pane.appendChild(replacement)
}

function showTimelinePane(conversationId) {
  const list = $('messageList')
  const { pane } = getOrCreateTimelinePane(conversationId)

  buildTimelinePane(conversationId)
  if (!pane.isConnected) list.appendChild(pane)
  Array.from(list.children).forEach((child) => {
    child.hidden = child !== pane
  })
  state.activeTimelineConversationId = conversationId
  list.scrollTop = list.scrollHeight
  state.unreadWhileScrolledUp.set(conversationId, 0)
  updateScrollIndicator()
}

function patchMessageNode(conversationId, message) {
  const registry = state.timelineRegistryByConversationId.get(conversationId)
  const pane = state.timelinePaneByConversationId.get(conversationId)
  if (!registry || !pane) return
  const messageId = String(message.id)
  const existingNode = registry.messageNodeById.get(messageId)
  const replacement = createNodeFromHTML(renderTimelineCard(message, conversationId))
  syncMessageNodeOrderKey(replacement, message)
  if (!existingNode) {
    pane.querySelectorAll('.empty').forEach((node) => node.remove())
    const messages = getConversationMessages(conversationId)
    const targetIndex = messages.findIndex((item) => String(item.id) === messageId)
    const nextMessage = messages.slice(targetIndex + 1).find((item) => registry.messageNodeById.has(String(item.id)))
    if (nextMessage) {
      pane.insertBefore(replacement, registry.messageNodeById.get(String(nextMessage.id)))
    } else {
      pane.appendChild(replacement)
    }
    indexMessageNode(registry, replacement)
    reorderTimelineNodes(conversationId)
    // Show scroll indicator if user is scrolled up
    if (conversationId === state.activeConversationId) {
      const scrolledUp = state.userScrolledUpByConversation.get(conversationId)
      if (scrolledUp) {
        const count = (state.unreadWhileScrolledUp.get(conversationId) || 0) + 1
        state.unreadWhileScrolledUp.set(conversationId, count)
        updateScrollIndicator()
      }
    }
    return
  }
  const existingKind = existingNode.dataset.cardKind || ''
  const nextKind = replacement.dataset.cardKind || ''
  if (existingKind !== nextKind) {
    replaceTimelineNodePreservingPosition(conversationId, registry, existingNode, replacement)
    dropMessageNodeFromRegistry(registry, messageId)
    indexMessageNode(registry, replacement)
    reorderTimelineNodes(conversationId)
    return
  }

  const existingEditor = existingNode.querySelector('[data-message-editor]')
  const replacementEditor = replacement.querySelector('[data-message-editor]')
  if (existingEditor || replacementEditor) {
    replaceTimelineNodePreservingPosition(conversationId, registry, existingNode, replacement)
    dropMessageNodeFromRegistry(registry, messageId)
    indexMessageNode(registry, replacement)
    reorderTimelineNodes(conversationId)
    return
  }

  existingNode.className = replacement.className
  existingNode.dataset.cardKind = nextKind
  syncMessageNodeOrderKey(existingNode, message)

  const contentNode = registry.messageContentNodeById.get(messageId)
  if (contentNode) {
    if (message.metadata?.local_draft === true && message.status === 'streaming') {
      contentNode.textContent = message.content || ''
      contentNode.dataset.streamingStarted = '1'
    } else if (!contentNode.dataset.streamingStarted) {
      contentNode.textContent = message.content || ''
    }
  }

  patchMessageStatus(conversationId, messageId, message)

  const timeNode = registry.messageTimeNodeById.get(messageId)
  if (timeNode) {
    timeNode.textContent = CHAT_STATE.formatClockTime(message.created_at)
  }

  const replacementActionNode = replacement.querySelector('[data-message-actions]')
  const existingActionNode = registry.messageActionNodeById.get(messageId)
  if (replacementActionNode && existingActionNode) {
    existingActionNode.innerHTML = replacementActionNode.innerHTML
  } else if (replacementActionNode && !existingActionNode) {
    const body = existingNode.children[1]
    if (body) {
      body.appendChild(replacementActionNode)
      registry.messageActionNodeById.set(messageId, replacementActionNode)
    }
  } else if (!replacementActionNode && existingActionNode) {
    existingActionNode.remove()
    registry.messageActionNodeById.delete(messageId)
  }

  reorderTimelineNodes(conversationId)
}

function removeMessageNode(conversationId, messageId) {
  const registry = state.timelineRegistryByConversationId.get(conversationId)
  const pane = state.timelinePaneByConversationId.get(conversationId)
  if (!registry) return
  const node = registry.messageNodeById.get(String(messageId))
  if (node) node.remove()
  dropMessageNodeFromRegistry(registry, messageId)
  reorderTimelineNodes(conversationId)
  if (pane && !pane.querySelector('[data-message-id]')) {
    pane.replaceChildren(renderEmptyTimelinePane('这个会话还没有消息。<br>发送第一条消息开始。'))
  }
}

function patchMessageStatus(conversationId, messageId, message) {
  const registry = state.timelineRegistryByConversationId.get(conversationId)
  if (!registry) return
  const statusNode = registry.messageStatusNodeById.get(String(messageId))
  if (!statusNode) return
  const tone = CHAT_STATE.getStatusTone(message)
  statusNode.textContent = CHAT_STATE.getDisplayStatus(message)
  statusNode.className = `status-pill status-${tone}`
}

function appendTokenToDraft(conversationId, messageId, token) {
  if (typeof token !== 'string' || !token) return

  const registry = state.timelineRegistryByConversationId.get(conversationId)
  const pane = state.timelinePaneByConversationId.get(conversationId)
  if (!registry || !pane) return

  const message = getMessageById(conversationId, messageId)
  if (!message) return

  let contentNode = registry.messageContentNodeById.get(String(messageId))

  if (!contentNode) {
    // First token - create message card shell inline with empty content (no "生成中…" flash)
    pane.querySelectorAll('.empty').forEach((node) => node.remove())

    const time = CHAT_STATE.formatClockTime(message.created_at)
    const statusTone = CHAT_STATE.getStatusTone(message)

    const shellHTML = `
      <div class="message ai-draft" data-message-id="${escapeHtml(message.id)}" data-card-kind="message.ai-draft">
        <div class="avatar">AI</div>
        <div style="flex:1;min-width:0;">
          <div class="message-meta">
            <strong>AI 草稿</strong>
            <span class="status-pill status-${statusTone}" data-message-status="${escapeHtml(message.id)}">${escapeHtml(CHAT_STATE.getDisplayStatus(message))}</span>
            <span data-message-time="${escapeHtml(message.id)}">${escapeHtml(time)}</span>
          </div>
          <div class="message-card draft-card" data-message-content="${escapeHtml(message.id)}"></div>
          <div class="draft-actions">
            <button type="button" class="draft-edit-btn" data-message-id="${escapeHtml(message.id)}"><svg class="ico ico-sm" style="display:inline;width:12px;height:12px"><use href="#ico-edit"/></svg> 编辑</button>
            <button type="button" class="draft-copy-btn" data-message-id="${escapeHtml(message.id)}"><svg class="ico ico-sm" style="display:inline;width:12px;height:12px"><use href="#ico-copy"/></svg> 复制</button>
            <button type="button" class="draft-send-btn" data-message-id="${escapeHtml(message.id)}" data-conversation-id="${escapeHtml(conversationId)}"><svg class="ico ico-sm" style="display:inline;width:12px;height:12px"><use href="#ico-send"/></svg> 发送</button>
            <button type="button" class="draft-delete-btn" data-message-id="${escapeHtml(message.id)}" data-conversation-id="${escapeHtml(conversationId)}"><svg class="ico ico-sm" style="display:inline;width:12px;height:12px"><use href="#ico-trash"/></svg> 删除</button>
          </div>
        </div>
      </div>
    `
    pane.appendChild(createNodeFromHTML(shellHTML))
    indexMessageNode(registry, pane.lastElementChild)
    contentNode = registry.messageContentNodeById.get(String(messageId))
  }

  if (!contentNode) {
    // Safety net: if contentNode is still null, fall back to patchMessageNode
    patchMessageNode(conversationId, { ...message, status: 'streaming' })
    return
  }

  contentNode.textContent = message.content || `${contentNode.textContent || ''}${token}`
  contentNode.dataset.streamingStarted = '1'
  patchMessageStatus(conversationId, messageId, { ...message, status: 'streaming' })

  // Scroll guard: only auto-scroll if user is at bottom
  const list = $('messageList')
  const scrolledUp = state.userScrolledUpByConversation.get(conversationId)
  if (!scrolledUp) {
    list.scrollTop = list.scrollHeight
  }
}

function renderRequestView(request) {
  const list = $('messageList')
  const paneId = request
    ? getRequestConversationId(request.id)
    : '__request-empty__'
  let pane = state.timelinePaneByConversationId.get(paneId)
  if (!pane) {
    pane = document.createElement('div')
    pane.className = 'timeline-pane'
    pane.dataset.conversationId = paneId
    state.timelinePaneByConversationId.set(paneId, pane)
  }
  if (!request) {
    pane.replaceChildren(renderEmptyTimelinePane('请求不存在或已失效。'))
    if (!pane.isConnected) list.appendChild(pane)
    Array.from(list.children).forEach((child) => {
      child.hidden = child !== pane
    })
    state.activeTimelineConversationId = paneId
    return
  }
  pane.replaceChildren(createNodeFromHTML(renderRequestConversation(request)))
  if (!pane.isConnected) list.appendChild(pane)
  Array.from(list.children).forEach((child) => {
    child.hidden = child !== pane
  })
  state.activeTimelineConversationId = paneId
}

function renderGroupInvitationView(invitation) {
  const list = $('messageList')
  const paneId = invitation ? getGroupInvitationConversationId(invitation.id) : '__invite-empty__'
  let pane = state.timelinePaneByConversationId.get(paneId)
  if (!pane) {
    pane = document.createElement('div')
    pane.className = 'timeline-pane'
    pane.dataset.conversationId = paneId
    state.timelinePaneByConversationId.set(paneId, pane)
  }
  if (!invitation) {
    pane.replaceChildren(renderEmptyTimelinePane('邀请不存在或已失效。'))
  } else {
    pane.replaceChildren(createNodeFromHTML(renderGroupInvitation(invitation)))
  }
  if (!pane.isConnected) list.appendChild(pane)
  Array.from(list.children).forEach((child) => {
    child.hidden = child !== pane
  })
  state.activeTimelineConversationId = paneId
}

function bindMessageActions() {
  $('messageList').addEventListener('click', async (event) => {
    const button = event.target.closest('button')
    if (!button) return
    if (button.classList.contains('retry-btn')) {
      await retryLatestMessage(button.dataset.conversationId)
      return
    }
    if (button.classList.contains('draft-copy-btn')) {
      const draft = getMessageById(state.activeConversationId, button.dataset.messageId)
      navigator.clipboard.writeText(draft?.content || '').catch(() => {})
      setComposerStatus('已复制到剪贴板')
      return
    }
    if (button.classList.contains('draft-edit-btn')) {
      const conversationId = state.activeConversationId
      const draft = ensureLocalDraftMessage(conversationId, button.dataset.messageId)
      if (!draft) return
      setDraftEditState(draft.id, { content: draft.content || '', saving: false })
      patchMessageNode(conversationId, draft)
      focusDraftEditor(draft.id)
      return
    }
    if (button.classList.contains('draft-save-btn')) {
      const conversationId = state.activeConversationId
      const draft = getMessageById(conversationId, button.dataset.messageId)
      const editState = getDraftEditState(button.dataset.messageId)
      if (!draft || !editState) return
      if (editState.content === draft.content) {
        clearDraftEditState(draft.id)
        patchMessageNode(conversationId, draft)
        return
      }
      setDraftEditState(draft.id, { ...editState, saving: true })
      patchMessageNode(conversationId, draft)
      try {
        const updated = await callApi(`/api/ai/drafts/${draft.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ content: editState.content }),
        })
        clearDraftEditState(draft.id)
        upsertMessage(updated)
        patchMessageNode(updated.conversation_id, updated)
        setComposerStatus('AI 草稿已更新')
      } catch (error) {
        setDraftEditState(draft.id, { ...editState, saving: false })
        patchMessageNode(conversationId, draft)
        setComposerStatus(error.message)
      }
      return
    }
    if (button.classList.contains('draft-cancel-btn')) {
      const conversationId = state.activeConversationId
      const draft = getMessageById(conversationId, button.dataset.messageId)
      if (!draft) return
      clearDraftEditState(draft.id)
      patchMessageNode(conversationId, draft)
      return
    }
    if (button.classList.contains('draft-send-btn')) {
      await sendDraftAsUser(button.dataset.messageId, button.dataset.conversationId)
      return
    }
    if (button.classList.contains('draft-delete-btn')) {
      await deleteDraft(button.dataset.messageId, button.dataset.conversationId)
      return
    }
    if (button.classList.contains('accept-request-btn')) {
      await acceptDmRequest(button.dataset.requestId)
      return
    }
    if (button.classList.contains('reject-request-btn')) {
      await rejectDmRequest(button.dataset.requestId)
      return
    }
    if (button.classList.contains('accept-group-invite-btn')) {
      await acceptGroupInvitation(button.dataset.invitationId)
      return
    }
    if (button.classList.contains('reject-group-invite-btn')) {
      await rejectGroupInvitation(button.dataset.invitationId)
    }
  })

  // Track scroll position to detect if user scrolled up
  $('messageList').addEventListener('scroll', () => {
    const list = $('messageList')
    const conversationId = state.activeTimelineConversationId
    if (!conversationId) return
    const isAtBottom = list.scrollHeight - list.scrollTop - list.clientHeight < SCROLL_THRESHOLD_PX
    state.userScrolledUpByConversation.set(conversationId, !isAtBottom)
    updateScrollIndicator()
  })

  $('messageList').addEventListener('input', (event) => {
    const editor = event.target.closest('.draft-editor')
    if (!editor) return
    const editState = getDraftEditState(editor.dataset.messageId)
    if (!editState) return
    setDraftEditState(editor.dataset.messageId, { ...editState, content: editor.value })
  })
}

async function acknowledgeVisiblePeerMessages(conversationId) {
  const messages = getConversationMessages(conversationId)
  const unreadPeerMessages = messages.filter((message) => message.role === 'peer' && message.status !== 'read')
  for (const message of unreadPeerMessages) {
    try {
      await callApi(`/api/messages/${message.id}/ack`, {
        method: 'POST',
        body: JSON.stringify({ status: 'read' }),
      })
    } catch (_) {}
  }
}

function renderMessages() {
  const conversation = getActiveConversation()
  const isLifecycleConversation = conversation?.kind === 'dm-pending' || conversation?.kind === 'group-pending'
  const participantCount = CHAT_STATE.getConversationParticipantCount(conversation)

  $('roomTitle').textContent = conversation?.title || 'No conversation selected'
  $('roomMeta').textContent = conversation
    ? (isLifecycleConversation ? 'pending request' : `${conversation.kind} · ${participantCount || 1} participants`)
    : 'Choose or create a conversation'
  renderRoomActions()

  if (!conversation) {
    $('messageList').replaceChildren(renderEmptyTimelinePane('选择一个会话开始聊天。'))
    state.activeTimelineConversationId = null
    return
  }

  if (conversation?.kind === 'dm-pending') {
    const request = getDmRequestById(conversation.request_id)
    renderRequestView(request)
    return
  }
  if (conversation?.kind === 'group-pending') {
    const invitation = getGroupInvitationById(conversation.invitation_id)
    renderGroupInvitationView(invitation)
    return
  }
  showTimelinePane(conversation.id)
}

function getPeerCardAction(peer) {
  if (peer.id === state.self?.id) {
    return CHAT_STATE.getPeerActionState({ peerId: peer.id, selfId: state.self?.id })
  }
  const relationship = getRelationshipForPeer(peer.id)
  const action = CHAT_STATE.getPeerActionState({
    peerId: peer.id,
    selfId: state.self?.id,
    existingConversationId: relationship?.relationship === 'active_dm' ? relationship.conversation_id : null,
    pendingDirection:
      relationship?.relationship === 'outbound_pending_dm'
        ? 'outbound'
        : relationship?.relationship === 'inbound_pending_dm'
          ? 'inbound'
          : null,
    pendingRequestId: relationship?.request_id || null,
    activeConversationId: state.activeConversationId,
  })
  return { ...action, peerId: peer.id }
}

function renderPeerCard(peer) {
  const action = getPeerCardAction(peer)
  const actionIcon = {
    'self': '',
    'open-dm': '<svg class="ico ico-sm" style="display:inline;width:12px;height:12px"><use href="#ico-message-square"/></svg>',
    'request-dm': '<svg class="ico ico-sm" style="display:inline;width:12px;height:12px"><use href="#ico-plus"/></svg>',
    'pending-outbound': '<svg class="ico ico-sm" style="display:inline;width:12px;height:12px"><use href="#ico-clock"/></svg>',
    'open-request': '<svg class="ico ico-sm" style="display:inline;width:12px;height:12px"><use href="#ico-message-square"/></svg>',
  }[action.action] || ''
  return `
    <div class="peer-card ${peer.id === state.self?.id ? 'local' : ''}">
      <div class="title">${escapeHtml(peer.display_name)}</div>
      <div class="subtitle">${escapeHtml(peer.id)} · ${escapeHtml(peer.status)}</div>
      <div class="mono">port ${escapeHtml(peer.backend_port)} · ${escapeHtml(peer.capabilities?.device || 'unknown')}</div>
      <div class="peer-actions">
        <button
          type="button"
          class="${action.disabled ? 'pending' : ''} peer-action-btn"
          ${action.disabled ? 'disabled' : ''}
          data-action="${escapeHtml(action.action)}"
          data-peer-id="${escapeHtml(peer.id)}"
          data-request-id="${escapeHtml(action.requestId || '')}"
          data-conversation-id="${escapeHtml(action.conversationId || '')}"
        >${actionIcon} ${escapeHtml(action.label)}</button>
      </div>
    </div>
  `
}

function renderPeers() {
  const online = state.peers.filter((peer) => peer.status === 'online').length
  const signature = JSON.stringify(state.peers.map((peer) => {
    const action = getPeerCardAction(peer)
    return {
      id: peer.id,
      display_name: peer.display_name,
      status: peer.status,
      backend_port: peer.backend_port,
      device: peer.capabilities?.device || '',
      action: action.action,
      label: action.label,
      conversationId: action.conversationId || '',
      requestId: action.requestId || '',
    }
  }))
  if (signature === state.renderedPeerListSignature) return
  $('networkMeta').innerHTML = `<span class="online-dot"></span>${online} peer${online !== 1 ? 's' : ''} online`
  $('peerList').innerHTML = state.peers.map(renderPeerCard).join('') || '<div class="empty">No peers discovered yet.</div>'
  state.renderedPeerListSignature = signature
  $('peerList').querySelectorAll('.peer-action-btn').forEach((button) => {
    button.addEventListener('click', async () => {
      const action = button.dataset.action
      if (action === 'request-dm') {
        await requestDirectMessage(button.dataset.peerId)
      } else if (action === 'open-dm') {
        await selectConversation(button.dataset.conversationId)
      } else if (action === 'leave-dm') {
        const leavingConversationId = button.dataset.conversationId
        const leavingConversation = state.conversations.find((item) => item.id === leavingConversationId) || null
        const counterpartId = leavingConversation
          ? (leavingConversation.participant_ids || []).find((item) => item !== state.self?.id) || null
          : null
        await callApi(`/api/conversations/${leavingConversationId}/close`, { method: 'POST' })
        const fallbackConversationId = buildConversationCards().find((item) => item.id === 'general')?.id
          || buildConversationCards().find((item) => !isLifecycleConversationId(item.id) && item.id !== leavingConversationId)?.id
          || null
        if (counterpartId) {
          upsertRelationship({
            peer_id: counterpartId,
            relationship: 'closed_dm',
            status: 'closed_dm',
            request_id: null,
            conversation_id: null,
            updated_at: new Date().toISOString(),
          })
        }
        if (state.activeConversationId === leavingConversationId) {
          state.activeConversationId = fallbackConversationId
        }
        removeConversation(leavingConversationId)
        renderConversations()
        renderPeers()
        if (fallbackConversationId) await selectConversationWithOptions(fallbackConversationId, { force: true })
      } else if (action === 'open-request') {
        await selectConversation(getRequestConversationId(button.dataset.requestId))
      }
    })
  })
}

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
  $('routeList').innerHTML = route.length
    ? route.map((node) => `
      <div class="route-node${node.local ? ' local' : ''}">
        <div>
          <div class="title">${escapeHtml(node.display_name || node.id)}${node.local ? ' ← 本机' : ''}</div>
          <div class="subtitle">${escapeHtml(node.id)} · layers ${escapeHtml(node.layers)}</div>
        </div>
        <div class="mono" style="margin-left:auto;flex-shrink:0;">${node.local ? 'local' : 'remote'} · ${escapeHtml(node.latency_ms || '?')} ms</div>
      </div>`).join('')
    : '<div class="empty" style="font-size:12px;">AI route will appear here after generation starts.</div>'
}

function renderDiagnostics() {
  const signature = JSON.stringify({
    apiBase: state.apiBase,
    modelId: state.model?.model_id,
    modelFamily: state.model?.family,
    modelDevice: state.model?.device,
    selfName: state.self?.display_name,
    selfId: state.self?.id,
  })
  if (signature === state.renderedDiagnosticsSignature) return
  state.renderedDiagnosticsSignature = signature
  $('diagList').innerHTML = `
    <div class="diag-card">
      <div class="title">Backend</div>
      <div class="mono">${escapeHtml(state.apiBase)}</div>
    </div>
    <div class="diag-card">
      <div class="title">Model</div>
      <div class="mono">${escapeHtml(state.model?.model_id || 'unknown')}</div>
      <div class="subtitle">${escapeHtml(state.model?.family || '')} · ${escapeHtml(state.model?.device || '')}</div>
    </div>
    <div class="diag-card">
      <div class="title">Instance</div>
      <div class="mono">${escapeHtml(state.self?.display_name || state.instanceMeta?.instanceName || 'unknown')}</div>
      <div class="subtitle">${escapeHtml(state.self?.id || state.instanceMeta?.instanceId || 'unknown')}</div>
    </div>
  `
}

function renderAll() {
  $('selfName').textContent = state.self?.display_name || 'ParaMind'
  $('selfMeta').textContent = state.self?.id ? `${state.self.id} · ${state.apiBase}` : 'Connecting…'
  $('selfAvatar').textContent = (state.self?.display_name || 'PM').slice(0, 2).toUpperCase()

  renderConversations()
  patchConversationSelection()
  renderMessages()
  renderPeers()
  renderRoute()
  renderDiagnostics()

  const activeConversation = getActiveConversation()
  const requestConversation = activeConversation?.kind === 'dm-pending' || activeConversation?.kind === 'group-pending'
  $('sendBtn').disabled = !activeConversation || requestConversation
  $('stopBtn').disabled = !state.activeJobId
  $('retryBtn').disabled = !activeConversation || requestConversation || !!state.activeJobId || !hasRetryableAssistantTurn(activeConversation.id)
}

function renderConversationShell() {
  patchConversationSelection()
  renderMessages()
  renderPeers()
  renderRoute()

  const activeConversation = getActiveConversation()
  const requestConversation = activeConversation?.kind === 'dm-pending' || activeConversation?.kind === 'group-pending'
  $('sendBtn').disabled = !activeConversation || requestConversation
  $('stopBtn').disabled = !state.activeJobId
  $('retryBtn').disabled = !activeConversation || requestConversation || !!state.activeJobId || !hasRetryableAssistantTurn(activeConversation.id)
}

function applyRenderScope(scope) {
  switch (scope) {
    case 'none':
      return
    case 'messages':
      renderMessages()
      $('stopBtn').disabled = !state.activeJobId
      $('retryBtn').disabled = !getActiveConversation() || ['dm-pending', 'group-pending'].includes(getActiveConversation()?.kind) || !!state.activeJobId || !hasRetryableAssistantTurn(state.activeConversationId)
      return
    case 'conversations':
      renderConversations()
      return
    case 'shell-list':
      renderConversations()
      renderConversationShell()
      return
    case 'peers':
      renderPeers()
      return
    case 'conversation':
    case 'shell':
      renderConversationShell()
      return
    case 'all':
    default:
      renderAll()
  }
}

async function refreshBootstrap({ preserveSelection = true } = {}) {
  const bootstrap = await callApi('/api/bootstrap')
  const filteredRequests = (bootstrap.dm_requests || []).filter((item) => item.status === 'pending')
  const filteredGroupInvitations = (bootstrap.group_invitations || []).filter((item) => item.status === 'pending')
  const previousSnapshot = {
    activeConversationId: state.activeConversationId,
    self: state.self,
    peers: state.peers,
    conversations: state.conversations,
    dmRequests: state.dmRequests,
    groupInvitations: state.groupInvitations,
  }
  const nextSnapshot = {
    activeConversationId: preserveSelection ? (state.activeConversationId || null) : null,
    self: bootstrap.self,
    peers: bootstrap.peers || [],
    conversations: bootstrap.conversations || [],
    dmRequests: filteredRequests,
    groupInvitations: filteredGroupInvitations,
  }
  const hasMeaningfulChange = CHAT_STATE.hasMeaningfulBootstrapChange(previousSnapshot, nextSnapshot)

  state.self = bootstrap.self
  state.model = bootstrap.model
  state.network = bootstrap.network
  state.peers = bootstrap.peers || []
  state.bootstrapLatestEventId = Number(bootstrap.latest_event_id || 0)
  state.latestGlobalEventId = Number(bootstrap.latest_global_event_id || bootstrap.latest_event_id || 0)
  state.latestConversationEventIds = new Map(Object.entries(bootstrap.latest_conversation_event_ids || {}))
  syncRelationshipsFromBootstrap(bootstrap.relationships || [])

  const previousActive = preserveSelection ? state.activeConversationId : null
  state.conversations = []
  syncUnreadFromBootstrap(bootstrap.conversations || [])
  syncDmRequestsFromBootstrap(filteredRequests)
  syncGroupInvitationsFromBootstrap(filteredGroupInvitations)

  const allCards = buildConversationCards()
  if (!previousActive || !allCards.find((item) => item.id === previousActive)) {
    state.activeConversationId = allCards[0]?.id || null
  } else {
    state.activeConversationId = previousActive
  }

  if (hasMeaningfulChange) {
    renderAll()
  }
  setComposerStatus('Ready')
  if (hasMeaningfulChange && state.activeConversationId && !isLifecycleConversationId(state.activeConversationId)) {
    await loadMessages(state.activeConversationId)
    void openConversationStream(state.activeConversationId)
  }
  state.hasInitialBootstrap = true
}

async function loadMessages(conversationId) {
  const messages = await callApi(`/api/conversations/${conversationId}/messages`)
  state.messagesByConversation.set(conversationId, messages)
  chatState.messagesByConversation.set(conversationId, messages.slice())
  touchConversationReadState(conversationId, messages.at(-1)?.id || null, { clearUnread: true })
  // Only build if not already cached; showTimelinePane will call buildTimelinePane internally
  if (!state.timelineRegistryByConversationId.has(conversationId)) {
    buildTimelinePane(conversationId)
  }
  if (state.activeConversationId === conversationId) {
    showTimelinePane(conversationId)
  }
  await acknowledgeVisiblePeerMessages(conversationId)
}

async function selectConversation(conversationId) {
  return selectConversationWithOptions(conversationId)
}

async function selectConversationWithOptions(conversationId, { force = false } = {}) {
  const selectionMode = CHAT_STATE.getConversationSelectionMode({
    currentActiveConversationId: state.activeConversationId,
    nextConversationId: conversationId,
    hasCachedMessages: state.messagesByConversation.has(conversationId),
    activeStreamConversationId: state.activeStreamConversationId,
  })
  if (selectionMode === 'noop' && !force) return
  state.activeConversationId = conversationId
  applyRenderScope(force ? 'conversation' : CHAT_STATE.getConversationSelectionRenderScope(selectionMode))
  if (isLifecycleConversationId(conversationId)) return
  if (selectionMode === 'load') {
    await loadMessages(conversationId)
  } else {
    // For cached conversations: use showTimelinePane directly which has caching
    showTimelinePane(conversationId)
    await acknowledgeVisiblePeerMessages(conversationId)
  }
  void openConversationStream(conversationId)
}

function injectConversationSnapshot(conversation, messages = [], latestConversationEventId = null) {
  if (!conversation?.id) return
  upsertConversation(conversation)
  const sortedMessages = [...messages].sort((a, b) => {
    const aTime = String(a?.created_at || a?.updated_at || '')
    const bTime = String(b?.created_at || b?.updated_at || '')
    return aTime.localeCompare(bTime) || String(a?.id || '').localeCompare(String(b?.id || ''))
  })
  state.messagesByConversation.set(conversation.id, [])
  chatState.messagesByConversation.set(conversation.id, [])
  for (const message of sortedMessages) {
    upsertMessage(message)
  }
  if (latestConversationEventId !== null && latestConversationEventId !== undefined) {
    const nextId = Number(latestConversationEventId || 0)
    state.latestConversationEventIds.set(
      conversation.id,
      Math.max(Number(state.latestConversationEventIds.get(conversation.id) || 0), nextId),
    )
    state.lastEventIdByConversation.set(
      conversation.id,
      Math.max(Number(state.lastEventIdByConversation.get(conversation.id) || 0), nextId),
    )
  }
}

async function createGroupConversation() {
  openGroupComposer({ mode: 'create' })
}

async function requestDirectMessage(peerId) {
  try {
    const request = await callApi('/api/dm/requests', {
      method: 'POST',
      body: JSON.stringify({ target_peer_id: peerId }),
    })
    if (request?.conversation) {
      upsertConversation(request.conversation)
      await selectConversation(request.conversation.id)
      return
    }
    upsertDmRequest(request)
    upsertRelationship({
      peer_id: peerId,
      relationship: 'outbound_pending_dm',
      request_id: request.id,
      conversation_id: request.conversation_id || null,
      updated_at: request.updated_at,
    })
    renderConversations()
    renderPeers()
    setComposerStatus('已发起私聊请求')
    await selectConversation(getRequestConversationId(request.id))
  } catch (error) {
    setComposerStatus(error.message)
  }
}

async function acceptDmRequest(requestId) {
  try {
    const requestConversationId = getRequestConversationId(requestId)
    const wasViewingRequest = state.activeConversationId === requestConversationId
    const request = await callApi(`/api/dm/requests/${requestId}/accept`, { method: 'POST' })
    removeDmRequest(requestId)
    await refreshBootstrap({ preserveSelection: !wasViewingRequest })
    const fallbackConversationId = buildConversationCards().find((item) => !isLifecycleConversationId(item.id))?.id || null
    const resolution = CHAT_STATE.resolvePostDmRequestSelection({
      action: 'accept',
      wasViewingRequest,
      resolvedConversationId: request.conversation_id || null,
      fallbackConversationId,
    })
    if (resolution.nextConversationId) {
      await selectConversationWithOptions(resolution.nextConversationId, { force: resolution.force })
    } else if (resolution.force) {
      applyRenderScope('conversation')
    }
  } catch (error) {
    setComposerStatus(error.message)
  }
}

async function autoEnterAcceptedDm(request, conversation) {
  if (!request || !conversation) return
  if (request.requester_id !== state.self?.id) return
  upsertConversation(conversation)
  removeDmRequest(request.id)
  renderConversations()
  renderPeers()
  await selectConversationWithOptions(conversation.id, { force: true })
}

async function autoEnterAcceptedGroupInvitation(invitation, conversation, payload = {}) {
  if (!invitation || !conversation) return
  if (invitation.target_peer_id !== state.self?.id) return
  injectConversationSnapshot(conversation, payload.messages || [], payload.latestConversationEventId ?? payload.latest_conversation_event_id ?? null)
  removeGroupInvitation(invitation.id)
  renderConversations()
  await selectConversationWithOptions(conversation.id, { force: true })
}

async function rejectDmRequest(requestId) {
  try {
    const requestConversationId = getRequestConversationId(requestId)
    const wasViewingRequest = state.activeConversationId === requestConversationId
    await callApi(`/api/dm/requests/${requestId}/reject`, { method: 'POST' })
    removeDmRequest(requestId)
    setComposerStatus('已拒绝私聊请求')
    await refreshBootstrap({ preserveSelection: !wasViewingRequest })
    const fallbackConversationId = buildConversationCards().find((item) => !isLifecycleConversationId(item.id))?.id || null
    const resolution = CHAT_STATE.resolvePostDmRequestSelection({
      action: 'reject',
      wasViewingRequest,
      resolvedConversationId: null,
      fallbackConversationId,
    })
    if (resolution.nextConversationId) {
      await selectConversationWithOptions(resolution.nextConversationId, { force: resolution.force })
    } else if (resolution.force) {
      applyRenderScope('conversation')
    }
  } catch (error) {
    setComposerStatus(error.message)
  }
}

async function acceptGroupInvitation(invitationId) {
  try {
    const response = await callApi(`/api/group/invitations/${invitationId}/accept`, { method: 'POST' })
    removeGroupInvitation(invitationId)
    if (response?.conversation) {
      injectConversationSnapshot(
        response.conversation,
        response.messages || [],
        response.latest_conversation_event_id ?? null,
      )
      renderConversations()
      await selectConversationWithOptions(response.conversation.id, { force: true })
    }
  } catch (error) {
    setComposerStatus(error.message)
  }
}

async function rejectGroupInvitation(invitationId) {
  try {
    await callApi(`/api/group/invitations/${invitationId}/reject`, { method: 'POST' })
    removeGroupInvitation(invitationId)
    renderConversations()
    const fallbackConversationId = buildConversationCards().find((item) => !isLifecycleConversationId(item.id))?.id || null
    if (state.activeConversationId === getGroupInvitationConversationId(invitationId) && fallbackConversationId) {
      await selectConversationWithOptions(fallbackConversationId, { force: true })
    }
  } catch (error) {
    setComposerStatus(error.message)
  }
}

async function leaveGroupConversation(conversationId) {
  const conversation = state.conversations.find((item) => item.id === conversationId) || null
  if (!conversation || conversation.kind !== 'group' || conversation.id === 'general') return
  const fallbackConversationId = getFallbackConversationId(conversationId)
  await callApi(`/api/conversations/${conversationId}/close`, { method: 'POST' })
  if (state.activeConversationId === conversationId) {
    state.activeConversationId = fallbackConversationId
  }
  removeConversation(conversationId)
  renderConversations()
  renderMessages()
  if (fallbackConversationId) {
    await selectConversationWithOptions(fallbackConversationId, { force: true })
  }
}

async function sendMessage(event) {
  event.preventDefault()
  const conversation = getActiveConversation()
  if (!conversation || ['dm-pending', 'group-pending'].includes(conversation.kind)) return

  const input = $('composerInput')
  const rawContent = input.value.trim()
  if (!rawContent) return
  const isAiRequest = state.composerMode === 'ai' || isAiRequestContent(rawContent)
  const content = (state.composerMode === 'ai' && !isAiRequestContent(rawContent))
    ? `@AI ${rawContent}`
    : rawContent

  $('sendBtn').disabled = true
  setComposerStatus(isAiRequest ? '正在发送 @AI 请求…' : 'Sending message…')
  try {
    const userMessage = await callApi(`/api/conversations/${conversation.id}/messages`, {
      method: 'POST',
      body: JSON.stringify({ role: 'user', content }),
    })
    upsertMessage(userMessage)
    input.value = ''
    $('composerInput').style.height = 'auto'
    patchMessageNode(conversation.id, userMessage)
    patchConversationSelection()

    if (isAiRequest) {
      await triggerAIDraft(conversation.id, { sourceMessageId: userMessage.id })
      return
    }

    setComposerStatus('Message sent')
  } catch (error) {
    setComposerStatus(error.message)
  } finally {
    $('sendBtn').disabled = false
  }
}

async function triggerAIDraft(conversationId, options = {}) {
  const payload = { conversation_id: conversationId }
  if (options.sourceMessageId) payload.source_message_id = options.sourceMessageId
  if (options.prompt) payload.prompt = options.prompt
  try {
    const result = await callApi('/api/ai/drafts', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    upsertMessage(result.draft)
    state.activeJobId = result.job.id
    setComposerStatus('AI 草稿生成中…')
    patchMessageNode(result.draft.conversation_id, result.draft)
    $('stopBtn').disabled = false
    $('retryBtn').disabled = true
  } catch (error) {
    setComposerStatus(error.message)
  }
}

async function stopJob() {
  if (!state.activeJobId) return
  try {
    await callApi(`/api/inference/jobs/${state.activeJobId}/cancel`, { method: 'POST' })
    setComposerStatus('Generation cancelled')
  } catch (error) {
    setComposerStatus(error.message)
  }
}

async function retryLatestMessage(conversationId = state.activeConversationId) {
  const retryable = getLatestRetryableUserMessage(conversationId)
  if (!retryable || !isAiRequestContent(retryable.content)) {
    setComposerStatus('没有可重试的 @AI 请求')
    return
  }
  await triggerAIDraft(conversationId, { sourceMessageId: retryable.id })
}

async function sendDraftAsUser(messageId) {
  try {
    const sent = await callApi(`/api/ai/drafts/${messageId}/send`, { method: 'POST' })
    clearDraftEditState(messageId)
    upsertMessage(sent)
    removeMessage(sent.conversation_id, messageId)
    setComposerStatus('AI 消息已发送')
    removeMessageNode(sent.conversation_id, messageId)
    patchMessageNode(sent.conversation_id, sent)
    patchConversationSelection()
    $('sendBtn').disabled = !getActiveConversation() || ['dm-pending', 'group-pending'].includes(getActiveConversation()?.kind)
  } catch (error) {
    setComposerStatus(error.message)
  }
}

async function deleteDraft(messageId, conversationId) {
  try {
    await callApi(`/api/ai/drafts/${messageId}`, { method: 'DELETE' })
    clearDraftEditState(messageId)
    removeMessage(conversationId, messageId)
    setComposerStatus('AI 草稿已删除')
    removeMessageNode(conversationId, messageId)
    patchConversationSelection()
  } catch (error) {
    setComposerStatus(error.message)
  }
}

function handleDmRequestEvent(event) {
  const request = event.payload?.request
  if (!request) return
  if (event.payload?.relationship) upsertRelationship(event.payload.relationship)
  if (event.type === 'dm.requested' && request.status === 'pending') {
    upsertDmRequest(request)
    const counterpartId = request.requester_id === state.self?.id ? request.target_peer_id : request.requester_id
    upsertRelationship({
      peer_id: counterpartId,
      relationship: request.requester_id === state.self?.id ? 'outbound_pending_dm' : 'inbound_pending_dm',
      request_id: request.id,
      conversation_id: request.conversation_id || null,
      updated_at: request.updated_at,
    })
    return
  }
  if (event.type === 'dm.accepted' && event.payload?.conversation) {
    void autoEnterAcceptedDm(request, event.payload.conversation)
    const counterpartId = request.requester_id === state.self?.id ? request.target_peer_id : request.requester_id
    upsertRelationship({
      peer_id: counterpartId,
      relationship: 'active_dm',
      status: 'active_dm',
      request_id: null,
      conversation_id: event.payload?.conversation?.id || request.conversation_id || null,
      updated_at: request.updated_at,
    })
  } else if (event.type === 'dm.rejected') {
    const counterpartId = request.requester_id === state.self?.id ? request.target_peer_id : request.requester_id
    upsertRelationship({
      peer_id: counterpartId,
      relationship: 'none',
      status: 'none',
      request_id: null,
      conversation_id: null,
      updated_at: request.updated_at,
    })

    const requestConversationId = getRequestConversationId(request.id)
    const wasViewingRequest = state.activeConversationId === requestConversationId
    if (wasViewingRequest) {
      const fallbackConversationId = buildConversationCards().find((item) => !isLifecycleConversationId(item.id) && item.id !== requestConversationId)?.id || null
      const resolution = CHAT_STATE.resolvePostDmRequestSelection({
        action: 'reject',
        wasViewingRequest,
        resolvedConversationId: null,
        fallbackConversationId,
      })
      if (resolution.nextConversationId) {
        void selectConversationWithOptions(resolution.nextConversationId, { force: resolution.force })
      } else if (resolution.force) {
        applyRenderScope('conversation')
      }
    }
  }
  removeDmRequest(request.id)
  renderPeers()
}

function handleGroupInvitationEvent(event) {
  const invitation = event.payload?.invitation
  if (!invitation) return
  if (event.type === 'group.invited' && invitation.status === 'pending') {
    upsertGroupInvitation(invitation)
    renderConversations()
    return
  }
  if (event.type === 'group.accepted' && event.payload?.conversation) {
    upsertConversation(event.payload.conversation)
    void autoEnterAcceptedGroupInvitation(invitation, event.payload.conversation, {
      messages: event.payload.messages || [],
      latestConversationEventId: event.payload.latest_conversation_event_id ?? null,
    })
  }
  removeGroupInvitation(invitation.id)
  renderConversations()
}

function handleEvent(event) {
  const activeConversationBeforeEvent = getActiveConversation()

  if (event.conversation_id) {
    state.lastEventIdByConversation.set(
      event.conversation_id,
      Math.max(state.lastEventIdByConversation.get(event.conversation_id) || 0, event.id || 0),
    )
  }

  if (event.type === 'message.deleted') {
    const conversationId = event.payload?.conversation_id || event.conversation_id
    const messageId = event.payload?.message_id || event.entity_id
    if (conversationId && messageId) {
      removeMessage(conversationId, messageId)
      removeMessageNode(conversationId, messageId)
      patchConversationSelection()
    }
    return
  }

  if (event.type.startsWith('dm.')) {
    handleDmRequestEvent(event)
  } else if (event.type.startsWith('group.')) {
    handleGroupInvitationEvent(event)
  } else {
    chatState = CHAT_STATE.reduceEvent(chatState, event)
    state.messagesByConversation = chatState.messagesByConversation
  }

  let renderScope = CHAT_STATE.getEventRenderScope(event)
  switch (event.type) {
    case 'conversation.created':
    case 'conversation.updated':
      if (event.payload?.conversation) {
        const previousConversation = state.conversations.find((item) => item.id === event.payload.conversation.id) || null
        upsertConversation(event.payload.conversation)
        if (
          CHAT_STATE.shouldRefreshActiveShellForEvent(
            event,
            state.activeConversationId,
            previousConversation || activeConversationBeforeEvent,
          )
        ) {
          renderScope = 'shell-list'
        }
      }
      break
    case 'conversation.deleted': {
      const conversationId = event.payload?.conversation_id || event.conversation_id || event.entity_id
      if (conversationId) {
        const removedConversation = state.conversations.find((item) => item.id === conversationId)
        const wasActive = state.activeConversationId === conversationId
        removeConversation(conversationId)
        if (removedConversation?.kind === 'dm') {
          const counterpartId = (removedConversation.participant_ids || []).find((item) => item !== state.self?.id)
          if (counterpartId) {
            upsertRelationship({
              peer_id: counterpartId,
              relationship: 'closed_dm',
              request_id: null,
              conversation_id: null,
              updated_at: new Date().toISOString(),
            })
          }
        }
        if (wasActive) {
          const fallbackConversationId = buildConversationCards().find((item) => item.id === 'general')?.id
            || buildConversationCards().find((item) => !isLifecycleConversationId(item.id))?.id
            || null
          state.activeConversationId = fallbackConversationId
          applyRenderScope('conversation')
        }
        renderConversations()
        renderPeers()
      }
      renderScope = 'none'
      break
    }
    case 'message.created':
    case 'message.updated': {
      const message = event.payload?.message
      if (message?.conversation_id) {
        patchMessageNode(message.conversation_id, message)
        // DO NOT call renderConversations() here — causes entire sidebar rebuild on every message event
      }
      renderScope = 'none'
      break
    }
    case 'message.token': {
      const conversationId = event.conversation_id
      const messageId = event.payload?.message_id || event.entity_id
      const token = String(event.payload?.token || '')
      if (conversationId && messageId && token) appendTokenToDraft(conversationId, messageId, token)
      renderScope = 'none'
      break
    }
    case 'job.route':
      state.lastRouteByConversation.set(event.conversation_id, event.payload?.route || [])
      renderScope = 'conversation'
      break
    case 'job.completed':
      state.activeJobId = null
      setComposerStatus('AI 草稿已生成')
      break
    case 'job.failed':
      state.activeJobId = null
      setComposerStatus(event.payload?.error === 'cancelled' ? 'Generation cancelled' : `Job failed: ${event.payload?.error || 'unknown error'}`)
      break
    case 'peer.joined':
    case 'peer.updated':
    case 'peer.left': {
      let peerChanged = false
      if (event.type === 'peer.left') {
        const peerId = event.payload?.peer?.id || event.entity_id
        const beforeLength = state.peers.length
        state.peers = state.peers.filter((peer) => peer.id !== peerId)
        peerChanged = state.peers.length !== beforeLength
      } else if (event.payload?.peer) {
        peerChanged = upsertPeer(event.payload.peer)
      }
      const generalParticipantsChanged = syncDefaultGroupParticipantsFromPeers()
      if (generalParticipantsChanged && state.activeConversationId === 'general') {
        renderScope = 'shell-list'
      } else if (peerChanged) {
        renderScope = 'peers'
      } else {
        renderScope = 'none'
      }
      break
    }
    default:
      break
  }

  if (
    renderScope === 'conversations'
    && CHAT_STATE.shouldRefreshActiveShellForEvent(event, state.activeConversationId, activeConversationBeforeEvent)
  ) {
    renderScope = 'shell-list'
  }

  applyRenderScope(renderScope)
}

function findEventBoundary(buffer) {
  // Check \r\n\r\n first (most specific), then \r\r, then \n\n
  const idxRn = buffer.indexOf('\r\n\r\n')
  if (idxRn >= 0) return { index: idxRn, delimiter: '\r\n\r\n', length: 4 }
  const idxR = buffer.indexOf('\r\r')
  if (idxR >= 0) return { index: idxR, delimiter: '\r\r', length: 2 }
  const idxN = buffer.indexOf('\n\n')
  if (idxN >= 0) return { index: idxN, delimiter: '\n\n', length: 2 }
  return null
}

function parseSSEEvent(rawEvent) {
  const lines = rawEvent.split('\n')
  const dataParts = []
  for (const line of lines) {
    if (line.startsWith('data: ')) {
      dataParts.push(line.slice(6))
    }
  }
  if (dataParts.length === 0) return null
  return dataParts.join('\n')
}

async function openConversationStream(conversationId) {
  if (state.activeStreamConversationId === conversationId && state.activeStreamAbort) return
  if (state.activeStreamAbort) state.activeStreamAbort.abort()

  const after = CHAT_STATE.getStreamStartAfter({
    knownConversationEventId: state.lastEventIdByConversation.get(conversationId) || state.latestConversationEventIds.get(conversationId) || 0,
    bootstrapLatestEventId: state.latestConversationEventIds.get(conversationId) || 0,
  })
  const abortController = new AbortController()
  state.activeStreamAbort = abortController
  state.activeStreamConversationId = conversationId

  try {
    const response = await fetch(`${state.apiBase}/api/conversations/${conversationId}/stream?after=${after}`, {
      signal: abortController.signal,
      headers: { Accept: 'text/event-stream' },
    })
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    let lastEventTime = Date.now()
    const HEARTBEAT_TIMEOUT_MS = 30000
    let parseFailureSkips = 0  // track consecutive parse failures to avoid infinite loop

    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      lastEventTime = Date.now()

      let boundaryInfo = findEventBoundary(buffer)
      while (boundaryInfo !== null) {
        const rawEvent = buffer.slice(0, boundaryInfo.index)
        buffer = buffer.slice(boundaryInfo.index + boundaryInfo.length)

        const dataStr = parseSSEEvent(rawEvent)
        if (dataStr === null) {
          boundaryInfo = findEventBoundary(buffer)
          continue
        }

        try {
          const event = JSON.parse(dataStr)

          // Deduplication: skip events already processed in this stream
          const eventId = event.id || event.payload?.message_id || null
          if (eventId !== null) {
            const lastProcessed = state.processedEventIdsByConversation.get(conversationId) || 0
            if (eventId <= lastProcessed) {
              boundaryInfo = findEventBoundary(buffer)
              continue
            }
            state.processedEventIdsByConversation.set(conversationId, eventId)
          }

          handleEvent(event)
          parseFailureSkips = 0
          lastEventTime = Date.now()
        } catch (error) {
          console.error('Failed to parse SSE event', error)
          // On parse failure, do NOT consume past this boundary.
          // Restore the raw event back into buffer so it can accumulate
          // with subsequent data until a valid complete event arrives.
          buffer = rawEvent + boundaryInfo.delimiter + buffer
          parseFailureSkips += 1
          if (parseFailureSkips > 10) {
            console.error('Too many consecutive parse failures, resetting buffer')
            buffer = ''
            parseFailureSkips = 0
          }
        }

        // Check heartbeat timeout
        if (Date.now() - lastEventTime > HEARTBEAT_TIMEOUT_MS) {
          setComposerStatus('Connection may be stale (no events for 30s)...')
        }

        boundaryInfo = findEventBoundary(buffer)
      }
    }
  } catch (error) {
    if (error.name !== 'AbortError') {
      setComposerStatus(`Stream disconnected: ${error.message}`)
    }
  } finally {
    if (state.activeStreamAbort === abortController) {
      state.activeStreamAbort = null
      if (state.activeStreamConversationId === conversationId) {
        state.activeStreamConversationId = null
      }
    }
  }
}

async function openGlobalStream() {
  if (state.activeGlobalStreamAbort) return
  const after = Number(state.latestGlobalEventId || 0)
  const abortController = new AbortController()
  state.activeGlobalStreamAbort = abortController

  try {
    const response = await fetch(`${state.apiBase}/api/events/stream?after=${after}`, {
      signal: abortController.signal,
      headers: { Accept: 'text/event-stream' },
    })
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let boundaryInfo = findEventBoundary(buffer)
      while (boundaryInfo !== null) {
        const rawEvent = buffer.slice(0, boundaryInfo.index)
        buffer = buffer.slice(boundaryInfo.index + boundaryInfo.length)
        const dataStr = parseSSEEvent(rawEvent)
        if (dataStr === null) {
          boundaryInfo = findEventBoundary(buffer)
          continue
        }
        try {
          const event = JSON.parse(dataStr)
          state.latestGlobalEventId = Math.max(state.latestGlobalEventId, Number(event.id || 0))
          handleEvent(event)
        } catch (error) {
          console.error('Failed to parse global SSE event', error)
        }
        boundaryInfo = findEventBoundary(buffer)
      }
    }
  } catch (error) {
    if (error.name !== 'AbortError') {
      setComposerStatus(`Global stream disconnected: ${error.message}`)
    }
  } finally {
    if (state.activeGlobalStreamAbort === abortController) {
      state.activeGlobalStreamAbort = null
    }
  }
}

async function waitForBootstrap(retries = 20) {
  let lastError = null
  for (let attempt = 0; attempt < retries; attempt += 1) {
    try {
      await refreshBootstrap({ preserveSelection: false })
      return
    } catch (error) {
      lastError = error
      setComposerStatus(`Waiting for backend… (${attempt + 1}/${retries})`)
      await new Promise((resolve) => setTimeout(resolve, 500))
    }
  }
  throw lastError || new Error('Backend did not become ready')
}

async function init() {
  bindMessageActions()
  $('composerForm').addEventListener('submit', sendMessage)

  // Auto-resize textarea
  const composerTextarea = $('composerInput')
  if (composerTextarea) {
    composerTextarea.addEventListener('input', () => {
      composerTextarea.style.height = 'auto'
      composerTextarea.style.height = `${Math.min(composerTextarea.scrollHeight, 320)}px`
    })
  }

  $('modeTextBtn')?.addEventListener('click', () => {
    state.composerMode = 'text'
    $('modeTextBtn').classList.add('active')
    $('modeAiBtn').classList.remove('active')
    $('composerInput').placeholder = '输入消息…'
  })
  $('modeAiBtn')?.addEventListener('click', () => {
    state.composerMode = 'ai'
    $('modeAiBtn').classList.add('active')
    $('modeTextBtn').classList.remove('active')
    $('composerInput').placeholder = '输入内容，将生成 AI 草稿…'
  })
  $('scrollIndicator')?.addEventListener('click', () => {
    const list = $('messageList')
    list.scrollTop = list.scrollHeight
    state.unreadWhileScrolledUp.set(state.activeConversationId, 0)
    updateScrollIndicator()
  })
  $('stopBtn').addEventListener('click', stopJob)
  $('retryBtn').addEventListener('click', () => retryLatestMessage())
  $('newGroupBtn').addEventListener('click', createGroupConversation)
  $('refreshBtn').addEventListener('click', () => refreshBootstrap({ preserveSelection: true }))
  $('inviteGroupBtn').addEventListener('click', () => {
    const conversation = getActiveConversation()
    if (conversation?.kind === 'group' && conversation.id !== 'general') {
      openGroupComposer({ mode: 'invite', conversationId: conversation.id })
    }
  })
  $('leaveGroupBtn').addEventListener('click', () => {
    const conversation = getActiveConversation()
    if (conversation?.kind === 'group' && conversation.id !== 'general') {
      void leaveGroupConversation(conversation.id)
    }
  })
  $('groupComposerCancelBtn').addEventListener('click', closeGroupComposer)
  $('groupComposerSubmitBtn').addEventListener('click', () => { void submitGroupComposer() })
  $('groupComposerTitle').addEventListener('input', updateGroupComposerSubmitState)
  $('groupComposerModal').addEventListener('click', (event) => {
    if (event.target === $('groupComposerModal')) closeGroupComposer()
  })

  await waitForBootstrap()
  void openGlobalStream()

  state.bootstrapRefreshTimer = null
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    resolveRuntimeConfig,
  }
}

if (runtimeWindow && runtimeDocument) {
  runtimeWindow.addEventListener('beforeunload', () => {
    if (state.activeStreamAbort) state.activeStreamAbort.abort()
    if (state.activeGlobalStreamAbort) state.activeGlobalStreamAbort.abort()
    if (state.bootstrapRefreshTimer) clearInterval(state.bootstrapRefreshTimer)
  })

  init().catch((error) => {
    console.error(error)
    setComposerStatus(error.message)
  })
}
