import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { resolveDesktopRuntimePaths } from '../../paramind/apps/desktop/runtime_paths.js'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const desktopDir = path.resolve(__dirname, '../../paramind/apps/desktop')

test('resolveDesktopRuntimePaths uses desktop resources in dev mode', () => {
  const runtime = resolveDesktopRuntimePaths({
    isPackaged: false,
    appDir: desktopDir,
    resourcesPath: '/tmp/fake-resources',
    env: {},
    platform: 'darwin',
  })

  assert.equal(runtime.baseDir, desktopDir)
  assert.equal(runtime.pythonRoot, path.join(desktopDir, 'python'))
  assert.equal(runtime.workspaceRoot, path.resolve(desktopDir, '..', '..', '..'))
  assert.equal(runtime.pythonScript, path.join(desktopDir, 'python', 'backend.py'))
  assert.equal(runtime.coordinatorScript, path.join(desktopDir, 'python', 'app', 'coordinator.py'))
  assert.equal(runtime.pythonCommand, 'uv')
  assert.deepEqual(runtime.pythonArgs, ['run', '--project', desktopDir, 'python'])
  assert.deepEqual(runtime.moduleRoots, [
    path.join(desktopDir, 'python'),
    path.resolve(desktopDir, '..', '..', '..'),
  ])
})

test('resolveDesktopRuntimePaths uses process.resourcesPath in packaged mode', () => {
  const runtime = resolveDesktopRuntimePaths({
    isPackaged: true,
    appDir: desktopDir,
    resourcesPath: '/Applications/ParaMind.app/Contents/Resources',
    env: {},
    platform: 'darwin',
    existsSync: (candidate) => candidate.endsWith('/python-dist/bin/python3'),
  })

  assert.equal(runtime.baseDir, '/Applications/ParaMind.app/Contents/Resources')
  assert.equal(runtime.pythonRoot, '/Applications/ParaMind.app/Contents/Resources/python')
  assert.equal(runtime.workspaceRoot, path.resolve(desktopDir, '..', '..', '..'))
  assert.equal(runtime.pythonScript, '/Applications/ParaMind.app/Contents/Resources/python/backend.py')
  assert.equal(runtime.coordinatorScript, '/Applications/ParaMind.app/Contents/Resources/python/app/coordinator.py')
  assert.equal(runtime.pythonCommand, '/Applications/ParaMind.app/Contents/Resources/python-dist/bin/python3')
  assert.deepEqual(runtime.pythonArgs, [])
  assert.deepEqual(runtime.moduleRoots, [
    '/Applications/ParaMind.app/Contents/Resources/python',
    '/Applications/ParaMind.app/Contents/Resources',
  ])
})

test('resolveDesktopRuntimePaths prefers python.exe for packaged windows runtime', () => {
  const resourcesRoot = 'C:\\ParaMind\\resources'
  const runtime = resolveDesktopRuntimePaths({
    isPackaged: true,
    appDir: desktopDir,
    resourcesPath: resourcesRoot,
    env: {
      PARAMIND_INSTANCE_ID: 'peer-a',
      PARAMIND_APP_DATA_DIR: 'C:\\ParaMind\\appdata',
    },
    platform: 'win32',
    existsSync: (candidate) => candidate.endsWith('python-dist\\bin\\python.exe'),
  })

  assert.equal(runtime.pythonRoot, path.win32.join(resourcesRoot, 'python'))
  assert.equal(
    runtime.pythonCommand,
    path.win32.join(resourcesRoot, 'python-dist', 'bin', 'python.exe')
  )
  assert.deepEqual(runtime.pythonArgs, [])
  assert.equal(runtime.instanceId, 'peer-a')
  assert.equal(runtime.baseAppDataDir, path.win32.join('C:\\ParaMind\\appdata'))
  assert.equal(runtime.instanceDataDir, path.win32.join('C:\\ParaMind\\appdata', 'instances', 'peer-a'))
  assert.equal(runtime.electronUserDataDir, path.win32.join('C:\\ParaMind\\appdata', 'electron', 'peer-a'))
})

test('desktop package.json packages python runtime resources and no venv directory', () => {
  const packageJsonPath = path.join(desktopDir, 'package.json')
  const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, 'utf8'))
  const extraResources = packageJson.build?.extraResources || []

  assert.ok(extraResources.some((entry) => entry.from === 'python-dist' && entry.to === 'python-dist'))
  assert.ok(extraResources.some((entry) => entry.from === 'python' && entry.to === 'python'))
  assert.ok(extraResources.some((entry) => entry.to === 'inference'))
  assert.ok(!extraResources.some((entry) => String(entry.from || '').includes('venv')))
  assert.ok(JSON.stringify(packageJson.build?.files || []).includes('!.venv/**/*'))
  assert.equal(
    packageJson.scripts?.['start:chat:win'],
    'powershell -ExecutionPolicy Bypass -File scripts/start_chat.ps1'
  )
})

test('desktop ships an experimental Windows multi-instance start script', () => {
  const scriptPath = path.join(desktopDir, 'scripts', 'start_chat.ps1')
  const scriptSource = fs.readFileSync(scriptPath, 'utf8')

  assert.match(scriptSource, /Get-NetTCPConnection/)
  assert.match(scriptSource, /npm\.cmd start/)
  assert.match(scriptSource, /PARAMIND_ELECTRON_USER_DATA_DIR/)
  assert.match(scriptSource, /electron\\\\peer-a/)
  assert.match(scriptSource, /experimental/i)
})
