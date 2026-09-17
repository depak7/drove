import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Built assets land in vorflux/web/, which the wheel ships — so `uv tool install` gives the
// end user a working UI with no Node installed.
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../vorflux/web', emptyOutDir: true },
  server: {
    // `npm run dev` talks to a daemon started separately with `vorflux serve`.
    proxy: { '/api': { target: 'http://127.0.0.1:8787', changeOrigin: true } },
  },
})
