import { app, ipcMain, dialog, Notification, BrowserWindow } from "electron";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import __cjs_mod__ from "node:module";
const __filename = import.meta.filename;
const __dirname = import.meta.dirname;
const require2 = __cjs_mod__.createRequire(import.meta.url);
const PRELOAD = ["../preload/index.mjs", "../preload/index.js"].map((rel) => join(__dirname, rel)).find(existsSync) ?? join(__dirname, "../preload/index.mjs");
let mainWindow;
let pulseWindow;
let engine;
const PORT = 8787;
const EXTRA_PATHS = [
  join(homedir(), ".local/bin"),
  "/opt/homebrew/bin",
  "/usr/local/bin",
  join(homedir(), ".cargo/bin")
];
function resolveBin(name) {
  for (const dir of EXTRA_PATHS) {
    const candidate = join(dir, name);
    if (existsSync(candidate)) return candidate;
  }
  return null;
}
function engineCommand() {
  if (app.isPackaged) {
    return { command: join(process.resourcesPath, "vorflux-sidecar"), args: ["serve", "--no-open", "--port", String(PORT)] };
  }
  const cli = resolveBin("vorflux");
  if (cli) return { command: cli, args: ["serve", "--no-open", "--port", String(PORT)] };
  const uv = resolveBin("uv");
  if (uv) return { command: uv, args: ["run", "vorflux", "serve", "--no-open", "--port", String(PORT)] };
  return null;
}
let engineError = "";
function startEngine() {
  const resolved = engineCommand();
  if (!resolved) {
    engineError = "Could not find the vorflux engine. Install it with:  uv tool install --force .";
    return;
  }
  engine = spawn(resolved.command, resolved.args, {
    cwd: join(app.getAppPath(), ".."),
    // Never ignore stderr. A silent sidecar death is indistinguishable from a hung one.
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, PATH: [...EXTRA_PATHS, process.env.PATH ?? ""].join(":") }
  });
  engine.stderr?.on("data", (chunk) => {
    const text = String(chunk);
    engineError = text.slice(-500);
    process.stderr.write(`[engine] ${text}`);
  });
  engine.on("error", (err) => {
    engineError = `Failed to start the engine: ${err.message}`;
  });
  engine.on("exit", (code) => {
    engine = void 0;
    if (code) engineError = engineError || `The engine exited with code ${code}.`;
  });
}
async function waitForEngine(timeoutMs = 2e4) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/api/health`);
      if (res.ok) return true;
    } catch {
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  return false;
}
function createPulse() {
  pulseWindow = new BrowserWindow({
    width: 360,
    height: 58,
    show: false,
    frame: false,
    transparent: true,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    type: "panel",
    webPreferences: { preload: PRELOAD, contextIsolation: true, nodeIntegration: false }
  });
  if (process.env.ELECTRON_RENDERER_URL) {
    pulseWindow.loadURL(`${process.env.ELECTRON_RENDERER_URL}?pulse=1`);
  } else {
    pulseWindow.loadFile(join(__dirname, "../renderer/index.html"), { search: "pulse=1" });
  }
}
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1040,
    minHeight: 700,
    titleBarStyle: "hiddenInset",
    vibrancy: "under-window",
    backgroundColor: "#0a0d12",
    webPreferences: { preload: PRELOAD, contextIsolation: true, nodeIntegration: false }
  });
  if (process.env.ELECTRON_RENDERER_URL) {
    mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL);
  } else {
    mainWindow.loadFile(join(__dirname, "../renderer/index.html"));
  }
}
app.whenReady().then(async () => {
  startEngine();
  ipcMain.handle("repository:choose", async () => {
    const result = await dialog.showOpenDialog(mainWindow, { properties: ["openDirectory"] });
    return result.canceled ? null : result.filePaths[0];
  });
  ipcMain.handle("desktop:notify", (_, title, body) => new Notification({ title, body }).show());
  ipcMain.on("pulse:update", (_, state) => {
    if (!pulseWindow) return;
    pulseWindow.webContents.send("pulse:state", state);
    const busy = (state?.running ?? 0) > 0 || (state?.waiting ?? 0) > 0;
    if (busy) pulseWindow.showInactive();
    else pulseWindow.hide();
  });
  createWindow();
  createPulse();
  const up = await waitForEngine();
  if (!up) {
    dialog.showErrorBox(
      "The engine did not start",
      engineError || `Nothing is listening on 127.0.0.1:${PORT}.

Try running \`vorflux serve\` in a terminal to see the error.`
    );
  }
});
app.on("before-quit", () => engine?.kill());
app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
