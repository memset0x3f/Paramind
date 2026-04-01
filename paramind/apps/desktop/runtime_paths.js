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
  const baseDir = isPackaged ? resourcesPath : appDir
  const workspaceRoot = path.resolve(appDir, '..', '..', '..')
  const pythonRoot = path.join(baseDir, 'python')
  const pythonScript = path.join(pythonRoot, 'backend.py')
  const coordinatorScript = path.join(pythonRoot, 'app', 'coordinator.py')
  const pythonBinaryName = platform === 'win32' ? 'python.exe' : 'python3'
  const portablePython = path.join(baseDir, 'python-dist', 'bin', pythonBinaryName)
  const fallbackPython = platform === 'win32' ? 'python' : 'python3'
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
