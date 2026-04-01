const { app, BrowserWindow, ipcMain } = require('electron/main')
const { spawn } = require('child_process')
const crypto = require('crypto')
const net = require('net')
const path = require('path')
const { resolveDesktopRuntimePaths } = require('./runtime_paths')

let pythonProcess = null
let coordinatorProcess = null
let backendConfig = null

function findAvailablePort(startPort = 5001, endPort = 5100) {
  return new Promise((resolve, reject) => {
    const tryPort = (port) => {
      if (port > endPort) {
        reject(new Error('No available backend port found'))
        return
      }

      const server = net.createServer()
      server.once('error', () => tryPort(port + 1))
      server.once('listening', () => {
        server.close(() => resolve(port))
      })
      server.listen(port, '127.0.0.1')
    }

    tryPort(startPort)
  })
}

async function startBackend() {
  const runtimePaths = resolveDesktopRuntimePaths({
    isPackaged: app.isPackaged,
    appDir: __dirname,
    resourcesPath: process.resourcesPath,
    env: process.env,
    platform: process.platform,
  })
  const pythonScript = runtimePaths.pythonScript

  const port = parseInt(process.env.PARAMIND_BACKEND_PORT || '', 10) || await findAvailablePort()
  const instanceId = process.env.PARAMIND_INSTANCE_ID || `peer-${crypto.randomUUID().slice(0, 8)}`
  const instanceName = process.env.PARAMIND_INSTANCE_NAME || `Local Node ${port}`
  const baseAppDataDir = process.env.PARAMIND_APP_DATA_DIR || app.getPath('userData')
  const instanceDataDir =
    process.env.PARAMIND_INSTANCE_DATA_DIR ||
    path.join(baseAppDataDir, 'instances', instanceId)
  const coordinatorPort = process.env.PARAMIND_COORDINATOR_PORT || '9010'

  await ensureCoordinator(runtimePaths, Number(coordinatorPort))

  backendConfig = {
    port,
    url: `http://127.0.0.1:${port}`,
    instanceId,
    instanceName,
    appDataDir: instanceDataDir,
    baseAppDataDir,
    coordinatorPort: Number(coordinatorPort),
  }

  pythonProcess = spawn(runtimePaths.pythonCommand, [...runtimePaths.pythonArgs, pythonScript], {
    cwd: runtimePaths.baseDir,
    env: {
      ...process.env,
      PYTHONPATH: [...runtimePaths.moduleRoots, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
      PARAMIND_BACKEND_PORT: String(port),
      PARAMIND_INSTANCE_ID: instanceId,
      PARAMIND_INSTANCE_NAME: instanceName,
      PARAMIND_APP_DATA_DIR: baseAppDataDir,
      PARAMIND_INSTANCE_DATA_DIR: instanceDataDir,
      PARAMIND_COORDINATOR_PORT: String(coordinatorPort),
      PARAMIND_MODEL: process.env.PARAMIND_MODEL || 'Qwen/Qwen2.5-0.5B-Instruct',
      PARAMIND_FAMILY: process.env.PARAMIND_FAMILY || 'qwen',
      PARAMIND_DEVICE: process.env.PARAMIND_DEVICE || 'cpu',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  })

  pythonProcess.stdout.on('data', (data) => console.log(`[backend] ${data}`))
  pythonProcess.stderr.on('data', (data) => console.error(`[backend] ${data}`))
  pythonProcess.on('exit', (code) => console.log(`[backend] exited with code ${code}`))
}

function canConnect(port) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ port, host: '127.0.0.1' })
    socket.once('connect', () => {
      socket.end()
      resolve(true)
    })
    socket.once('error', () => resolve(false))
  })
}

async function ensureCoordinator(runtimePaths, coordinatorPort) {
  const alreadyRunning = await canConnect(coordinatorPort)
  if (alreadyRunning) return

  coordinatorProcess = spawn(
    runtimePaths.pythonCommand,
    [...runtimePaths.pythonArgs, runtimePaths.coordinatorScript],
    {
      cwd: runtimePaths.baseDir,
      env: {
        ...process.env,
        PYTHONPATH: [...runtimePaths.moduleRoots, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
        PARAMIND_COORDINATOR_PORT: String(coordinatorPort),
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    }
  )

  coordinatorProcess.stdout.on('data', (data) => console.log(`[coordinator] ${data}`))
  coordinatorProcess.stderr.on('data', (data) => console.error(`[coordinator] ${data}`))
  coordinatorProcess.on('exit', (code) => console.log(`[coordinator] exited with code ${code}`))

  for (let i = 0; i < 20; i += 1) {
    if (await canConnect(coordinatorPort)) return
    await new Promise((resolve) => setTimeout(resolve, 250))
  }

  throw new Error(`Coordinator did not become ready on port ${coordinatorPort}`)
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1120,
    minHeight: 760,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'electron', 'preload.js'),
    },
  })

  if (process.env.PARAMIND_DEBUG_RENDERER === '1') {
    win.webContents.on('console-message', (_, level, message, line, sourceId) => {
      console.log(`[renderer:${level}] ${sourceId}:${line} ${message}`)
    })
    win.webContents.on('did-fail-load', (_, errorCode, errorDescription, validatedURL) => {
      console.error(`[renderer] did-fail-load ${errorCode} ${errorDescription} ${validatedURL}`)
    })
    win.webContents.on('render-process-gone', (_, details) => {
      console.error(`[renderer] render-process-gone ${JSON.stringify(details)}`)
    })
    win.webContents.on('did-finish-load', () => {
      setTimeout(() => {
        win.webContents.executeJavaScript(`
          JSON.stringify({
            backendUrl: window.electronAPI?.getBackendUrl?.() || null,
            selfMeta: document.getElementById('selfMeta')?.textContent || null,
            roomTitle: document.getElementById('roomTitle')?.textContent || null,
            roomMeta: document.getElementById('roomMeta')?.textContent || null,
            composerStatus: document.getElementById('composerStatus')?.textContent || null,
            conversationCount: document.querySelectorAll('#conversationList .conversation').length,
            peerCount: document.querySelectorAll('#peerList .peer-card').length
          })
        `)
          .then((snapshot) => console.log(`[renderer] snapshot ${snapshot}`))
          .catch((error) => console.error(`[renderer] snapshot failed ${error.message}`))
      }, 1500)
    })
  }

  win.loadFile(path.join(__dirname, 'renderer', 'index.html'))
}

ipcMain.on('paramind:get-backend-url', (event) => {
  event.returnValue = backendConfig?.url || 'http://127.0.0.1:5001'
})

ipcMain.on('paramind:get-instance-meta', (event) => {
  event.returnValue = backendConfig || null
})

app.whenReady().then(async () => {
  await startBackend()
  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow()
    }
  })
})

app.on('window-all-closed', () => {
  if (pythonProcess) {
    pythonProcess.kill()
    pythonProcess = null
  }
  if (coordinatorProcess) {
    coordinatorProcess.kill()
    coordinatorProcess = null
  }
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
