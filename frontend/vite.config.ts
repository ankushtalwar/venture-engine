import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The API runs separately (paindisc serve / python -m paindisc.api); in dev, Vite proxies /api to it.
const apiTarget = process.env.VITE_API_TARGET || 'http://127.0.0.1:8101'

export default defineConfig({
  base: '/',
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: apiTarget } },
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
    proxy: { '/api': { target: apiTarget } },
  },
  build: { outDir: 'dist', emptyOutDir: true },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    restoreMocks: true,
  },
})
