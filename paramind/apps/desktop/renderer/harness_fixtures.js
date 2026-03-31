function createBaseBootstrap() {
  return {
    self: { id: 'peer-a', display_name: 'Peer A', backend_port: 5001 },
    peers: [
      { id: 'peer-a', display_name: 'Peer A', status: 'online', backend_port: 5001 },
      { id: 'peer-b', display_name: 'Peer B', status: 'online', backend_port: 5002 },
    ],
    conversations: [
      {
        id: 'general',
        title: 'General',
        kind: 'group',
        updated_at: '2026-03-31T09:00:00Z',
        participant_ids: ['peer-a', 'peer-b'],
        last_message: {
          id: 'msg-general-1',
          conversation_id: 'general',
          role: 'peer',
          sender_name: 'Peer B',
          content: 'Welcome to ParaMind',
          status: 'sent',
          created_at: '2026-03-31T09:00:00Z',
          updated_at: '2026-03-31T09:00:00Z',
        },
      },
    ],
    dm_requests: [],
    group_invitations: [],
    relationships: [
      { peer_id: 'peer-b', status: 'none', conversation_id: null, request_id: null },
    ],
    latest_event_id: 0,
    latest_global_event_id: 0,
    latest_conversation_event_ids: { general: 0 },
    active_conversation_id: 'general',
    conversation_messages: {
      general: [
        {
          id: 'msg-general-1',
          conversation_id: 'general',
          role: 'peer',
          sender_name: 'Peer B',
          content: 'Welcome to ParaMind',
          status: 'sent',
          created_at: '2026-03-31T09:00:00Z',
          updated_at: '2026-03-31T09:00:00Z',
          metadata: {},
        },
      ],
    },
  }
}

function createHarnessFixture(name = 'bootstrap_two_peers') {
  const bootstrap = createBaseBootstrap()
  switch (name) {
    case 'pending_dm_request':
      return {
        bootstrap,
        globalEvents: [
          {
            type: 'dm.requested',
            payload: {
              request: {
                id: 'req-1',
                requester_id: 'peer-a',
                target_peer_id: 'peer-b',
                direction: 'outbound',
                status: 'pending',
                created_at: '2026-03-31T09:01:00Z',
                updated_at: '2026-03-31T09:01:00Z',
              },
              relationship: {
                peer_id: 'peer-b',
                status: 'outbound_pending_dm',
                conversation_id: null,
                request_id: 'req-1',
              },
            },
          },
        ],
        conversationEvents: {},
      }
    case 'accepted_dm':
      return {
        bootstrap: { ...bootstrap, active_conversation_id: 'request:req-1' },
        globalEvents: [
          {
            type: 'dm.requested',
            payload: {
              request: {
                id: 'req-1',
                requester_id: 'peer-a',
                target_peer_id: 'peer-b',
                direction: 'outbound',
                status: 'pending',
                created_at: '2026-03-31T09:01:00Z',
                updated_at: '2026-03-31T09:01:00Z',
              },
              relationship: {
                peer_id: 'peer-b',
                status: 'outbound_pending_dm',
                conversation_id: null,
                request_id: 'req-1',
              },
            },
          },
          {
            type: 'dm.accepted',
            payload: {
              request_id: 'req-1',
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
          },
        ],
        conversationEvents: {
          'dm:peer-a-peer-b': [],
        },
      }
    case 'ai_streaming_draft':
      return {
        bootstrap,
        globalEvents: [],
        conversationEvents: {
          general: [
            {
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
                  created_at: '2026-03-31T09:02:00Z',
                  updated_at: '2026-03-31T09:02:00Z',
                },
              },
            },
            {
              type: 'message.token',
              conversation_id: 'general',
              entity_id: 'draft-1',
              payload: { message_id: 'draft-1', token: 'Hello' },
            },
          ],
        },
      }
    case 'published_ai_message':
      return {
        bootstrap,
        globalEvents: [
          {
            type: 'conversation.updated',
            payload: {
              conversation: {
                ...bootstrap.conversations[0],
                updated_at: '2026-03-31T09:03:00Z',
                last_message: {
                  id: 'ai-1',
                  conversation_id: 'general',
                  role: 'assistant',
                  sender_name: 'AI',
                  content: 'Published AI answer',
                  status: 'completed',
                  created_at: '2026-03-31T09:03:00Z',
                  updated_at: '2026-03-31T09:03:00Z',
                  metadata: { published_from_draft: true },
                },
              },
            },
          },
        ],
        conversationEvents: {
          general: [
            {
              type: 'message.created',
              conversation_id: 'general',
              entity_id: 'ai-1',
              payload: {
                message: {
                  id: 'ai-1',
                  conversation_id: 'general',
                  role: 'assistant',
                  sender_name: 'AI',
                  content: 'Published AI answer',
                  status: 'completed',
                  metadata: { published_from_draft: true },
                  created_at: '2026-03-31T09:03:00Z',
                  updated_at: '2026-03-31T09:03:00Z',
                },
              },
            },
          ],
        },
      }
    case 'bootstrap_two_peers':
    default:
      return { bootstrap, globalEvents: [], conversationEvents: {} }
  }
}

function applyHarnessFixture(controller, name = 'bootstrap_two_peers') {
  const fixture = createHarnessFixture(name)
  controller.seedBootstrap(fixture.bootstrap)
  for (const event of fixture.globalEvents || []) {
    controller.emitGlobalEvent(event)
  }
  for (const [conversationId, events] of Object.entries(fixture.conversationEvents || {})) {
    for (const event of events) {
      controller.emitConversationEvent({ ...event, conversation_id: event.conversation_id || conversationId })
    }
  }
  return fixture
}

const fixturesApi = {
  createBaseBootstrap,
  createHarnessFixture,
  applyHarnessFixture,
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = fixturesApi
}

if (typeof window !== 'undefined') {
  window.ParaMindHarnessFixtures = fixturesApi
}
