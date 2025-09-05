const { contextBridge, ipcRenderer } = require('electron')

// Expose protected methods that allow the renderer process to use
// the ipcRenderer without exposing the entire object
contextBridge.exposeInMainWorld('electronAPI', {
  // API request function
  apiRequest: (method, endpoint, data, host, port) => {
    return ipcRenderer.invoke('api-request', { method, endpoint, data, host, port })
  },
  
  // Network functions
  getNetworkInfo: () => {
    return ipcRenderer.invoke('get-network-info')
  },
  
  startServer: () => {
    return ipcRenderer.invoke('start-server')
  },
  
  stopServer: () => {
    return ipcRenderer.invoke('stop-server')
  }
})
