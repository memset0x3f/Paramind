const { app, BrowserWindow, ipcMain } = require('electron/main')
const { net } = require('electron')
const path = require('path')
const { spawn } = require('child_process')
const fs = require('fs')

let pythonProcess = null
let serverPort = 5001
let serverHost = 'localhost'

const createWindow = () => {
  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js')
    }
  })

  win.loadFile('index.html')
  
  // Open DevTools for debugging (remove in production)
  // win.webContents.openDevTools()
}

// Start Python backend server
function startPythonServer() {
  const isDev = process.env.NODE_ENV === 'development'
  
  if (isDev) {
    // Development mode - use local venv
    const pythonPath = path.join(__dirname, 'venv', 'bin', 'python')
    const backendPath = path.join(__dirname, 'backend.py')
    
    if (fs.existsSync(pythonPath)) {
      pythonProcess = spawn(pythonPath, [backendPath], {
        cwd: __dirname,
        stdio: 'pipe'
      })
    } else {
      console.error('Python virtual environment not found')
      return false
    }
  } else {
    // Production mode - use bundled Python
    const resourcesPath = process.resourcesPath
    const pythonPath = path.join(resourcesPath, 'python-venv', 'bin', 'python')
    const backendPath = path.join(resourcesPath, 'backend.py')
    
    if (fs.existsSync(pythonPath)) {
      pythonProcess = spawn(pythonPath, [backendPath], {
        cwd: resourcesPath,
        stdio: 'pipe'
      })
    } else {
      console.error('Bundled Python not found')
      return false
    }
  }
  
  if (pythonProcess) {
    pythonProcess.stdout.on('data', (data) => {
      console.log(`Python: ${data}`)
    })
    
    pythonProcess.stderr.on('data', (data) => {
      console.error(`Python Error: ${data}`)
    })
    
    pythonProcess.on('close', (code) => {
      console.log(`Python process exited with code ${code}`)
    })
    
    return true
  }
  
  return false
}

// Stop Python server
function stopPythonServer() {
  if (pythonProcess) {
    pythonProcess.kill()
    pythonProcess = null
  }
}

// IPC handlers for backend communication
ipcMain.handle('api-request', async (event, { method, endpoint, data, host, port }) => {
  try {
    const requestHost = host || serverHost
    const requestPort = port || serverPort
    const request = net.request({
      method: method,
      url: `http://${requestHost}:${requestPort}${endpoint}`,
      headers: {
        'Content-Type': 'application/json'
      }
    })

    return new Promise((resolve, reject) => {
      let responseData = ''

      request.on('response', (response) => {
        response.on('data', (chunk) => {
          responseData += chunk
        })

        response.on('end', () => {
          try {
            const jsonData = JSON.parse(responseData)
            resolve({
              success: true,
              data: jsonData,
              status: response.statusCode
            })
          } catch (error) {
            resolve({
              success: false,
              error: 'Failed to parse response',
              status: response.statusCode
            })
          }
        })
      })

      request.on('error', (error) => {
        resolve({
          success: false,
          error: error.message
        })
      })

      if (data) {
        request.write(JSON.stringify(data))
      }
      request.end()
    })
  } catch (error) {
    return {
      success: false,
      error: error.message
    }
  }
})

// Add network discovery handlers
ipcMain.handle('get-network-info', async () => {
  try {
    const { exec } = require('child_process')
    const os = require('os')
    
    const networkInterfaces = os.networkInterfaces()
    const localIPs = []
    
    for (const interfaceName in networkInterfaces) {
      const interfaces = networkInterfaces[interfaceName]
      for (const iface of interfaces) {
        if (iface.family === 'IPv4' && !iface.internal) {
          localIPs.push(iface.address)
        }
      }
    }
    
    return {
      success: true,
      data: {
        localIPs: localIPs,
        serverPort: serverPort,
        serverHost: serverHost
      }
    }
  } catch (error) {
    return {
      success: false,
      error: error.message
    }
  }
})

ipcMain.handle('start-server', async () => {
  try {
    const success = startPythonServer()
    return {
      success: success,
      message: success ? 'Server started successfully' : 'Failed to start server'
    }
  } catch (error) {
    return {
      success: false,
      error: error.message
    }
  }
})

ipcMain.handle('stop-server', async () => {
  try {
    stopPythonServer()
    return {
      success: true,
      message: 'Server stopped successfully'
    }
  } catch (error) {
    return {
      success: false,
      error: error.message
    }
  }
})

app.whenReady().then(() => {
  // Start Python server automatically
  startPythonServer()
  
  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow()
    }
  })
})

app.on('window-all-closed', () => {
  stopPythonServer()
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('before-quit', () => {
  stopPythonServer()
})