import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const chatState = require('../../paramind/apps/desktop/renderer/chat_state.js')
const harness = require('../../paramind/apps/desktop/renderer/dev_harness.js')
const fixtures = require('../../paramind/apps/desktop/renderer/harness_fixtures.js')

test('harness bootstrap renders deterministic sidebar cards', () => {
  const controller = harness.createHarnessController({ chatState, fixtures })
  fixtures.applyHarnessFixture(controller, 'bootstrap_two_peers')

  assert.deepEqual(controller.getSidebarCards(), [
    {
      id: 'general',
      kind: 'conversation',
      title: 'General',
      preview: 'Welcome to ParaMind',
      updatedAt: '2026-03-31T09:00:00Z',
    },
  ])
})

test('triggering dm.requested adds a pending lifecycle card', () => {
  const controller = harness.createHarnessController({ chatState, fixtures })
  fixtures.applyHarnessFixture(controller, 'bootstrap_two_peers')

  controller.emitGlobalEvent({
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
  })

  assert.equal(controller.getSidebarCards()[0].id, 'request:req-1')
})

test('triggering dm.accepted auto-selects the dm conversation', () => {
  const controller = harness.createHarnessController({ chatState, fixtures })
  fixtures.applyHarnessFixture(controller, 'accepted_dm')

  assert.equal(controller.getActiveConversationId(), 'dm:peer-a-peer-b')
  assert.equal(controller.getSidebarCards()[0].id, 'dm:peer-a-peer-b')
})

test('ai token events update only the targeted draft message', () => {
  const controller = harness.createHarnessController({ chatState, fixtures })
  fixtures.applyHarnessFixture(controller, 'bootstrap_two_peers')

  const firstDraft = controller.createAssistantDraft('general')
  const secondDraft = controller.createAssistantDraft('general')
  controller.appendAssistantToken('general', secondDraft.id, 'Hello')

  const messages = controller.getMessages('general')
  assert.equal(messages.find((item) => item.id === firstDraft.id).content, '')
  assert.equal(messages.find((item) => item.id === secondDraft.id).content, 'Hello')
})
