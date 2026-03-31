# Desktop frontend test system

## Goal

Make renderer work testable without launching full Electron by default.

## Implemented layers

1. **Pure renderer state tests**
   - `test/backend/test_chat_state.mjs`
   - Covers bootstrap normalization, global lifecycle reduction, conversation event reduction, preview formatting, and stream cursor logic.

2. **Browser-openable renderer harness**
   - `paramind/apps/desktop/renderer/dev_harness.html`
   - `paramind/apps/desktop/renderer/dev_harness.js`
   - `paramind/apps/desktop/renderer/harness_fixtures.js`
   - Boots the real renderer with fake `window.electronAPI`, fake bootstrap payloads, and deterministic global/conversation events.

3. **Browser automation against the harness**
   - `test/backend/test_renderer_harness.spec.mjs`
   - Runs with Playwright from the desktop package scope.

4. **Small Electron/backend smoke layer**
   - `test/backend/test_api.py`
   - `test/backend/test_inference_runner.py`
   - Protects bootstrap shape, global-vs-conversation event routing, and inference startup expectations.

## Developer entry points

- `make test-frontend-fast`
- `make test-frontend-smoke`
- `make open-frontend-harness`

## Defaults

- Use the harness first for renderer bugs.
- Use Electron smoke only when changing preload, bootstrap contracts, inference startup, or doing final integration checks.
