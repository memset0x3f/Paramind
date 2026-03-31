const harnessChatStateApi = (typeof window !== 'undefined' && window.ParaMindChatState)
  || (typeof require === 'function' ? require('./chat_state.js') : null)
const rendererHarnessFixturesApi = (typeof window !== 'undefined' && window.ParaMindHarnessFixtures)
  || (typeof require === 'function' ? require('./harness_fixtures.js') : null)

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value))
}

function createHarnessController({
  chatState = harnessChatStateApi,
  fixtures = rendererHarnessFixturesApi,
  initialFixture = 'bootstrap_two_peers',
} = {}) {
  if (!chatState) throw new Error('ParaMind chat state API is required for the renderer harness')
  const fixtureApi = fixtures || { createHarnessFixture: () => ({ bootstrap: {} }) }

  let bootstrapModel = chatState.normalizeBootstrapPayload({})
  let conversationState = chatState.createChatState()
  let nextEventId = 1
  let globalEvents = []
  let globalSubscribers = new Set()
  let conversationEventsById = new Map()
  let conversationSubscribersById = new Map()
  let fixtureName = initialFixture
  let nextLocalId = 1

  function ensureConversationSubscribers(conversationId) {
    if (!conversationSubscribersById.has(conversationId)) {
      conversationSubscribersById.set(conversationId, new Set())
    }
    return conversationSubscribersById.get(conversationId)
  }

  function ensureConversationEvents(conversationId) {
    if (!conversationEventsById.has(conversationId)) {
      conversationEventsById.set(conversationId, [])
    }
    return conversationEventsById.get(conversationId)
  }

  function resetRuntime(bootstrapPayload) {
    const payload = cloneJson(bootstrapPayload || fixtureApi.createHarnessFixture(fixtureName).bootstrap)
    bootstrapModel = chatState.normalizeBootstrapPayload(payload)
    bootstrapModel.relationshipsByPeerId = new Map(bootstrapModel.relationshipsByPeerId)
    conversationState = chatState.createChatState({ activeConversationId: bootstrapModel.activeConversationId })
    conversationEventsById = new Map()
    globalEvents = []
    nextEventId = 1
    nextLocalId = 1

    const bootstrapMessages = payload.conversation_messages || {}
    for (const [conversationId, messages] of Object.entries(bootstrapMessages)) {
      const clonedMessages = cloneJson(messages)
      conversationState.messagesByConversation.set(conversationId, clonedMessages)
      conversationState.lastEventIdByConversation.set(conversationId, Number(bootstrapModel.latestConversationEventIds.get(conversationId) || 0))
      ensureConversationEvents(conversationId)
    }
  }

  function seedBootstrap(bootstrapPayload) {
    resetRuntime(bootstrapPayload)
    return getBootstrapPayload()
  }

  function pushGlobalEvent(event) {
    globalEvents.push(event)
    for (const subscriber of globalSubscribers) {
      subscriber(event)
    }
  }

  function pushConversationEvent(conversationId, event) {
    ensureConversationEvents(conversationId).push(event)
    for (const subscriber of ensureConversationSubscribers(conversationId)) {
      subscriber(event)
    }
  }

  function assignEventId(event) {
    return {
      id: Number(event.id || nextEventId++),
      timestamp: event.timestamp || new Date().toISOString(),
      ...event,
    }
  }

  function emitGlobalEvent(event) {
    const assigned = assignEventId(event)
    bootstrapModel = chatState.applyGlobalEvent(bootstrapModel, assigned)
    bootstrapModel.latestGlobalEventId = Math.max(Number(bootstrapModel.latestGlobalEventId || 0), Number(assigned.id || 0))
    pushGlobalEvent(assigned)
    return assigned
  }

  function emitConversationEvent(event) {
    const assigned = assignEventId(event)
    const conversationId = assigned.conversation_id || assigned.payload?.message?.conversation_id
    conversationState = chatState.applyConversationEvent(conversationState, assigned)
    if (conversationId) {
      bootstrapModel.latestConversationEventIds.set(conversationId, Math.max(Number(bootstrapModel.latestConversationEventIds.get(conversationId) || 0), Number(assigned.id || 0)))
      pushConversationEvent(conversationId, assigned)
    }
    return assigned
  }

  function getSidebarCards() {
    return chatState.buildSidebarCards(bootstrapModel)
  }

  function getMessages(conversationId) {
    return cloneJson(conversationState.messagesByConversation.get(conversationId) || [])
  }

  function getBootstrapPayload() {
    return {
      self: cloneJson(bootstrapModel.self),
      model: null,
      network: null,
      peers: cloneJson(bootstrapModel.peers),
      conversations: cloneJson(bootstrapModel.conversations),
      dm_requests: cloneJson(bootstrapModel.dmRequests),
      group_invitations: cloneJson(bootstrapModel.groupInvitations),
      relationships: cloneJson(Array.from(bootstrapModel.relationshipsByPeerId.values())),
      latest_event_id: Math.max(Number(bootstrapModel.latestGlobalEventId || 0), ...Array.from(bootstrapModel.latestConversationEventIds.values(), (value) => Number(value || 0)), 0),
      latest_global_event_id: Number(bootstrapModel.latestGlobalEventId || 0),
      latest_conversation_event_ids: Object.fromEntries(bootstrapModel.latestConversationEventIds.entries()),
      active_conversation_id: bootstrapModel.activeConversationId,
    }
  }

  function subscribeGlobal(after, listener) {
    globalEvents.filter((event) => Number(event.id || 0) > Number(after || 0)).forEach(listener)
    globalSubscribers.add(listener)
    return () => globalSubscribers.delete(listener)
  }

  function subscribeConversation(conversationId, after, listener) {
    ensureConversationEvents(conversationId)
      .filter((event) => Number(event.id || 0) > Number(after || 0))
      .forEach(listener)
    const subscribers = ensureConversationSubscribers(conversationId)
    subscribers.add(listener)
    return () => subscribers.delete(listener)
  }

  function requestDirectMessage(targetPeerId = 'peer-b') {
    const request = {
      id: `req-${nextLocalId++}`,
      requester_id: bootstrapModel.self?.id || 'peer-a',
      target_peer_id: targetPeerId,
      direction: 'outbound',
      status: 'pending',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }
    const relationship = {
      peer_id: targetPeerId,
      status: 'outbound_pending_dm',
      conversation_id: null,
      request_id: request.id,
    }
    emitGlobalEvent({ type: 'dm.requested', payload: { request, relationship } })
    return request
  }

  function acceptDirectMessage(requestId) {
    const request = bootstrapModel.dmRequests.find((item) => item.id === requestId)
    const peerId = request?.target_peer_id || request?.requester_id || 'peer-b'
    const conversation = {
      id: `dm:${[bootstrapModel.self?.id || 'peer-a', peerId].sort().join('-')}`,
      title: bootstrapModel.peers.find((item) => item.id === peerId)?.display_name || 'Direct Message',
      kind: 'dm',
      updated_at: new Date().toISOString(),
      participant_ids: [bootstrapModel.self?.id || 'peer-a', peerId],
      last_message: null,
    }
    const relationship = {
      peer_id: peerId,
      status: 'active_dm',
      conversation_id: conversation.id,
      request_id: null,
    }
    emitGlobalEvent({ type: 'dm.accepted', payload: { request_id: requestId, request: cloneJson(request), conversation, relationship } })
    return conversation
  }

  function createAssistantDraft(conversationId = bootstrapModel.activeConversationId || 'general') {
    const draft = {
      id: `draft-${nextLocalId++}`,
      conversation_id: conversationId,
      role: 'assistant',
      sender_name: 'AI',
      content: '',
      status: 'streaming',
      metadata: { local_draft: true },
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }
    emitConversationEvent({ type: 'message.created', conversation_id: conversationId, entity_id: draft.id, payload: { message: draft } })
    return draft
  }

  function appendAssistantToken(conversationId, messageId, token) {
    const currentMessage = getMessages(conversationId).find((item) => item.id === messageId)
    if (currentMessage) {
      emitConversationEvent({
        type: 'message.updated',
        conversation_id: conversationId,
        entity_id: messageId,
        payload: {
          message: {
            ...currentMessage,
            content: `${currentMessage.content || ''}${token}`,
            status: 'streaming',
            updated_at: new Date().toISOString(),
          },
        },
      })
      return
    }
    emitConversationEvent({ type: 'message.token', conversation_id: conversationId, entity_id: messageId, payload: { message_id: messageId, token } })
  }

  function publishAssistantMessage(conversationId, message) {
    emitConversationEvent({ type: 'message.created', conversation_id: conversationId, entity_id: message.id, payload: { message } })
    const conversation = bootstrapModel.conversations.find((item) => item.id === conversationId)
    if (conversation) {
      emitGlobalEvent({
        type: 'conversation.updated',
        payload: {
          conversation: {
            ...conversation,
            updated_at: message.updated_at || message.created_at,
            last_message: cloneJson(message),
          },
        },
      })
    }
  }

  resetRuntime(fixtureApi.createHarnessFixture(fixtureName).bootstrap)

  return {
    seedBootstrap,
    emitGlobalEvent,
    emitConversationEvent,
    getBootstrapPayload,
    getSidebarCards,
    getMessages,
    subscribeGlobal,
    subscribeConversation,
    requestDirectMessage,
    acceptDirectMessage,
    createAssistantDraft,
    appendAssistantToken,
    publishAssistantMessage,
    getActiveConversationId: () => bootstrapModel.activeConversationId,
    resetHarness(nextFixture = fixtureName) {
      fixtureName = nextFixture
      resetRuntime(fixtureApi.createHarnessFixture(fixtureName).bootstrap)
      return getBootstrapPayload()
    },
  }
}

function createSseResponse(register) {
  const encoder = new TextEncoder()
  return new Response(new ReadableStream({
    start(controller) {
      const unsubscribe = register((event) => {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`))
      })
      this.unsubscribe = unsubscribe
    },
    cancel() {
      this.unsubscribe?.()
    },
  }), {
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

function createHarnessFetch(controller, baseUrl = 'http://paramind-harness.invalid') {
  return async function harnessFetch(input, init = {}) {
    const requestUrl = new URL(typeof input === 'string' ? input : input.url, baseUrl)
    const path = requestUrl.pathname
    const method = String(init.method || input.method || 'GET').toUpperCase()

    if (path === '/api/bootstrap' && method === 'GET') {
      return Response.json(controller.getBootstrapPayload())
    }

    if (path === '/api/events/stream' && method === 'GET') {
      const after = Number(requestUrl.searchParams.get('after') || 0)
      return createSseResponse((listener) => controller.subscribeGlobal(after, listener))
    }

    const conversationMessagesMatch = path.match(/^\/api\/conversations\/([^/]+)\/messages$/)
    if (conversationMessagesMatch && method === 'GET') {
      const conversationId = decodeURIComponent(conversationMessagesMatch[1])
      return Response.json(controller.getMessages(conversationId))
    }

    const conversationStreamMatch = path.match(/^\/api\/conversations\/([^/]+)\/stream$/)
    if (conversationStreamMatch && method === 'GET') {
      const conversationId = decodeURIComponent(conversationStreamMatch[1])
      const after = Number(requestUrl.searchParams.get('after') || 0)
      return createSseResponse((listener) => controller.subscribeConversation(conversationId, after, listener))
    }

    if (path === '/api/dm/requests' && method === 'POST') {
      const body = init.body ? JSON.parse(init.body) : {}
      return Response.json(controller.requestDirectMessage(body.target_peer_id || 'peer-b'))
    }

    const requestAcceptMatch = path.match(/^\/api\/dm\/requests\/([^/]+)\/accept$/)
    if (requestAcceptMatch && method === 'POST') {
      return Response.json(controller.acceptDirectMessage(decodeURIComponent(requestAcceptMatch[1])))
    }

    return new Response(JSON.stringify({ detail: `Harness route not implemented: ${method} ${path}` }), {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    })
  }
}

function installBrowserHarness() {
  if (typeof window === 'undefined' || typeof document === 'undefined') return null
  const params = new URLSearchParams(window.location.search)
  if (params.get('harness') !== '1') return null

  const fixtureName = params.get('fixture') || 'bootstrap_two_peers'
  const controller = createHarnessController({ initialFixture: fixtureName })
  if (rendererHarnessFixturesApi?.applyHarnessFixture) {
    rendererHarnessFixturesApi.applyHarnessFixture(controller, fixtureName)
  }

  const harnessFetch = createHarnessFetch(controller)
  const originalFetch = window.fetch?.bind(window)
  window.fetch = (input, init) => {
    const url = typeof input === 'string' ? input : input.url
    if (String(url).startsWith('http://paramind-harness.invalid') || String(url).startsWith('/api/')) {
      return harnessFetch(input, init)
    }
    return originalFetch ? originalFetch(input, init) : harnessFetch(input, init)
  }

  window.electronAPI = {
    getBackendUrl: () => 'http://paramind-harness.invalid',
    getInstanceMeta: () => controller.getBootstrapPayload().self || {},
  }

  window.ParaMindHarness = {
    seedBootstrap: (payload) => controller.seedBootstrap(payload),
    emitGlobalEvent: (event) => controller.emitGlobalEvent(event),
    emitConversationEvent: (event) => controller.emitConversationEvent(event),
    resetHarness: (nextFixture) => {
      controller.resetHarness(nextFixture || fixtureName)
      if (rendererHarnessFixturesApi?.applyHarnessFixture) {
        rendererHarnessFixturesApi.applyHarnessFixture(controller, nextFixture || fixtureName)
      }
    },
    getSidebarCards: () => controller.getSidebarCards(),
    getMessages: (conversationId) => controller.getMessages(conversationId),
    requestDirectMessage: (targetPeerId) => controller.requestDirectMessage(targetPeerId),
    acceptDirectMessage: (requestId) => controller.acceptDirectMessage(requestId),
    createAssistantDraft: (conversationId) => controller.createAssistantDraft(conversationId),
    appendAssistantToken: (conversationId, messageId, token) => controller.appendAssistantToken(conversationId, messageId, token),
    publishAssistantMessage: (conversationId, message) => controller.publishAssistantMessage(conversationId, message),
  }

  document.addEventListener('DOMContentLoaded', () => {
    const panel = document.createElement('div')
    panel.id = 'harnessControls'
    panel.style.position = 'fixed'
    panel.style.top = '12px'
    panel.style.right = '12px'
    panel.style.zIndex = '9999'
    panel.style.display = 'flex'
    panel.style.gap = '8px'
    panel.innerHTML = [
      '<button id="harnessSeedBtn" type="button">Seed bootstrap</button>',
      '<button id="harnessRequestBtn" type="button">Request DM</button>',
      '<button id="harnessAcceptBtn" type="button">Accept DM</button>',
      '<button id="harnessAiBtn" type="button">Append AI token</button>',
    ].join('')
    document.body.appendChild(panel)

    document.getElementById('harnessSeedBtn')?.addEventListener('click', () => window.ParaMindHarness.resetHarness(fixtureName))
    document.getElementById('harnessRequestBtn')?.addEventListener('click', () => window.ParaMindHarness.requestDirectMessage('peer-b'))
    document.getElementById('harnessAcceptBtn')?.addEventListener('click', () => {
      const request = controller.getBootstrapPayload().dm_requests[0]
      if (request?.id) window.ParaMindHarness.acceptDirectMessage(request.id)
    })
    document.getElementById('harnessAiBtn')?.addEventListener('click', () => {
      const draft = window.ParaMindHarness.createAssistantDraft('general')
      window.ParaMindHarness.appendAssistantToken('general', draft.id, 'token')
    })
  })

  return controller
}

const harnessApi = {
  createHarnessController,
  createHarnessFetch,
  installBrowserHarness,
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = harnessApi
}

if (typeof window !== 'undefined') {
  window.ParaMindRendererHarness = harnessApi
  installBrowserHarness()
}
