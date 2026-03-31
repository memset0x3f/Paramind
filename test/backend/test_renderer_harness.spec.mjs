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
})

test('ai streaming fixture exposes draft state in the browser harness', async ({ page }) => {
  await page.goto(`${baseUrl}/dev_harness.html?fixture=ai_streaming_draft`)
  await expect.poll(async () => page.evaluate(() => {
    const draft = window.ParaMindHarness.getMessages('general').find((item) => item.id === 'draft-1')
    return draft?.content || ''
  })).toBe('Hello')
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
