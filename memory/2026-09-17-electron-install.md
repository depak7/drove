# Electron development startup: repaired installation state

- **Symptom:** `electron-vite dev` built main, preload, and renderer, then failed with `Error: Electron uninstall`.
- **Root cause:** Electron's post-install executable registration was absent or stale after dependencies were installed. `electron-vite` calls `require('electron')`, which throws that error when `node_modules/electron/path.txt` is unavailable.
- **Verification:** `web/node_modules/electron/path.txt` now contains `Electron.app/Contents/MacOS/Electron`; its target exists and is executable. Running `npm run desktop:dev` from `web/` now reaches `starting electron app...` without the error.
- **Recovery:** From `web/`, run `npm install electron --save-dev` (with npm lifecycle scripts enabled), then rerun `npm run desktop:dev`.
