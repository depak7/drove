import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  main: {},
  preload: {},
  renderer: {
    root: '.',
    plugins: [react()],
    build: { rollupOptions: { input: 'index.html' } },
    server: {
      host: '127.0.0.1',
      proxy: { '/api': 'http://127.0.0.1:8787', '/events': 'http://127.0.0.1:8787' },
    },
  },
})
