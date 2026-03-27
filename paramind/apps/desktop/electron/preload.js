const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  ping: () => 'pong',
  getBackendUrl: () => ipcRenderer.sendSync('paramind:get-backend-url'),
  getInstanceMeta: () => ipcRenderer.sendSync('paramind:get-instance-meta'),
})
