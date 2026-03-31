# ParaMind desktop app

This directory contains the Electron shell, renderer, and FastAPI backend for the ParaMind desktop demo.

## Frontend testing workflow

Use the browser harness by default when you are working on renderer behavior.

### Fast loop — no Electron required

From the repo root:

```bash
make test-frontend-fast
```

This runs:
- renderer state tests in `/Users/acropolis/Github_Project/Paramind/test/backend/test_chat_state.mjs`
- harness unit tests in `/Users/acropolis/Github_Project/Paramind/test/backend/test_renderer_harness.mjs`
- Playwright browser tests in `/Users/acropolis/Github_Project/Paramind/test/backend/test_renderer_harness.spec.mjs`

### Open the renderer harness manually

```bash
make open-frontend-harness
```

Then open:

- `http://127.0.0.1:4173/dev_harness.html?harness=1`

Useful query fixtures:
- `?fixture=bootstrap_two_peers`
- `?fixture=accepted_dm`
- `?fixture=ai_streaming_draft`
- `?fixture=published_ai_message`

### When to use Electron smoke instead

Use the smoke path only when your change depends on:
- preload bridge behavior
- backend bootstrap payload shape
- real inference startup
- final integration before merge

```bash
make test-frontend-smoke
```

## Files added for the frontend-first test loop

- `/Users/acropolis/Github_Project/Paramind/paramind/apps/desktop/renderer/dev_harness.html`
- `/Users/acropolis/Github_Project/Paramind/paramind/apps/desktop/renderer/dev_harness.js`
- `/Users/acropolis/Github_Project/Paramind/paramind/apps/desktop/renderer/harness_fixtures.js`
- `/Users/acropolis/Github_Project/Paramind/test/backend/test_renderer_harness.mjs`
- `/Users/acropolis/Github_Project/Paramind/test/backend/test_renderer_harness.spec.mjs`
- `/Users/acropolis/Github_Project/Paramind/paramind/apps/desktop/playwright.config.mjs`
