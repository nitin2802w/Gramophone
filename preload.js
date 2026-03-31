const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electron', {
  platform:   process.platform,
  minimize:   () => ipcRenderer.send('win:minimize'),
  maximize:   () => ipcRenderer.send('win:maximize'),
  close:      () => ipcRenderer.send('win:close'),
  isMaximized:() => ipcRenderer.invoke('win:isMax'),
});
