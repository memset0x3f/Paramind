const fs = require('fs')
const path = require('path')

function resolveDesktopRuntimePaths({
  isPackaged,
  appDir,
  resourcesPath,
  env = process.env,
  platform = process.platform,
  existsSync = fs.existsSync,
} = {}) {
  const pathApi = platform === 'win32' ? path.win32 : path
  const baseDir = isPackaged ? resourcesPath : appDir
  const workspaceRoot = pathApi.resolve(appDir, '..', '..', '..')
  const pythonRoot = pathApi.join(baseDir, 'python')
  const pythonScript = pathApi.join(pythonRoot, 'backend.py')
  const coordinatorScript = pathApi.join(pythonRoot, 'app', 'coordinator.py')
  const instanceId = env.PARAMIND_INSTANCE_ID || `peer-${Math.random().toString(16).slice(2, 10)}`
  const defaultBaseAppDataDir = pathApi.join(baseDir, '.paramind-local')
  const pythonBinaryName = platform === 'win32' ? 'python.exe' : 'python3'
  const portablePython = pathApi.join(baseDir, 'python-dist', 'bin', pythonBinaryName)
  const fallbackPython = platform === 'win32' ? 'python' : 'python3'
  const baseAppDataDir = env.PARAMIND_APP_DATA_DIR || defaultBaseAppDataDir
  const instanceDataDir =
    env.PARAMIND_INSTANCE_DATA_DIR ||
    pathApi.join(baseAppDataDir, 'instances', instanceId)
  const electronUserDataDir =
    env.PARAMIND_ELECTRON_USER_DATA_DIR ||
    pathApi.join(baseAppDataDir, 'electron', instanceId)
  let pythonCommand = ''
  let pythonArgs = []

  if (env.PARAMIND_PYTHON_BIN) {
    pythonCommand = env.PARAMIND_PYTHON_BIN
  } else if (isPackaged && existsSync(portablePython)) {
    pythonCommand = portablePython
  } else if (isPackaged) {
    pythonCommand = fallbackPython
  } else {
    pythonCommand = 'uv'
    pythonArgs = ['run', '--project', appDir, 'python']
  }

  return {
    baseDir,
    workspaceRoot,
    instanceId,
    baseAppDataDir,
    instanceDataDir,
    electronUserDataDir,
    pythonRoot,
    pythonScript,
    coordinatorScript,
    portablePython,
    pythonCommand,
    pythonArgs,
    moduleRoots: isPackaged ? [pythonRoot, baseDir] : [pythonRoot, workspaceRoot],
  }
}

module.exports = {
  resolveDesktopRuntimePaths,
}
