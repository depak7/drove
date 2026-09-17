import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('vorfluxDesktop', {
  chooseRepository: () => ipcRenderer.invoke('repository:choose'),
  notify: (title, body) => ipcRenderer.invoke('desktop:notify', title, body),
  updatePulse: (state) => ipcRenderer.send('pulse:update', state),
  onPulse: (listener) => ipcRenderer.on('pulse:state', (_, state) => listener(state)),
})
