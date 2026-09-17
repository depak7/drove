import { app, BrowserWindow, dialog, ipcMain, Notification } from 'electron'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join } from 'node:path'

// electron-vite emits the preload as .mjs for an ESM package, but names it .js in some
// configurations. Resolve it rather than hardcoding an extension: getting this wrong silently
// removes the entire native bridge, and the renderer just quietly loses its desktop features.
const PRELOAD = ['../preload/index.mjs', '../preload/index.js']
  .map((rel) => join(__dirname, rel))
  .find(existsSync) ?? join(__dirname, '../preload/index.mjs')

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
    webPreferences: { preload: PRELOAD, contextIsolation: true, nodeIntegration: false },
  })
  if (process.env.ELECTRON_RENDERER_URL) {
    pulseWindow.loadURL(`${process.env.ELECTRON_RENDERER_URL}?pulse=1`)
  } else {
    pulseWindow.loadFile(join(__dirname, '../renderer/index.html'), { search: 'pulse=1' })
  }
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
    webPreferences: { preload: PRELOAD, contextIsolation: true, nodeIntegration: false },
  })
  if (process.env.ELECTRON_RENDERER_URL) {
    mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    // A packaged app has no dev server; load the built renderer off disk.
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
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
    // Show it only when there is something a person would switch back for.
    const busy = (state?.running ?? 0) > 0 || (state?.waiting ?? 0) > 0
    if (busy) pulseWindow.showInactive()
    else pulseWindow.hide()
  })
  createWindow()
  createPulse()
})
app.on('before-quit', () => engine?.kill())
app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow()
})
app.on('window-all-closed', () => {
  // macOS convention: the app stays alive with no windows. The engine sidecar stays with it, so
  // runs in flight are not killed by closing the window.
  if (process.platform !== 'darwin') app.quit()
})
