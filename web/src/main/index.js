import { app, BrowserWindow, dialog, ipcMain, Notification } from 'electron'
import { spawn } from 'node:child_process'
import { join } from 'node:path'

let mainWindow
let pulseWindow
let engine

function startEngine() {
  const command = app.isPackaged ? join(process.resourcesPath, 'vorflux-sidecar') : 'uv'
  // The desktop shell owns the user experience. Never let the daemon open a browser tab.
  const args = app.isPackaged ? ['serve', '--no-open'] : ['run', 'vorflux', 'serve', '--no-open']
  engine = spawn(command, args, { cwd: join(app.getAppPath(), '..'), stdio: 'ignore', detached: false })
  engine.on('exit', () => { engine = undefined })
}

function createPulse() {
  pulseWindow = new BrowserWindow({
    width: 360, height: 58, show: false, frame: false, transparent: true, resizable: false,
    alwaysOnTop: true, skipTaskbar: true, type: 'panel',
    webPreferences: { preload: join(__dirname, '../preload/index.js'), contextIsolation: true, nodeIntegration: false },
  })
  pulseWindow.loadURL(`${process.env.ELECTRON_RENDERER_URL || 'http://localhost:5173'}?pulse=1`)
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1040,
    minHeight: 700,
    titleBarStyle: 'hiddenInset',
    vibrancy: 'under-window',
    backgroundColor: '#0a0d12',
    webPreferences: { preload: join(__dirname, '../preload/index.js'), contextIsolation: true, nodeIntegration: false },
  })
  mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL || 'http://localhost:5173')
}

app.whenReady().then(() => {
  startEngine()
  ipcMain.handle('repository:choose', async () => {
    const result = await dialog.showOpenDialog(mainWindow, { properties: ['openDirectory'] })
    return result.canceled ? null : result.filePaths[0]
  })
  ipcMain.handle('desktop:notify', (_, title, body) => new Notification({ title, body }).show())
  ipcMain.on('pulse:update', (_, state) => {
    if (!pulseWindow) return
    pulseWindow.webContents.send('pulse:state', state)
    if (state?.state === 'idle') pulseWindow.hide()
    else pulseWindow.showInactive()
  })
  createWindow()
  createPulse()
})
app.on('before-quit', () => engine?.kill())
app.on('window-all-closed', () => {})
