/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// The browser only talks to this dev server; it forwards API calls to the local
// backends, so no CORS change or provider call is needed from the browser.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'RADAR_')
  const real = env.RADAR_API_TARGET || 'http://127.0.0.1:8000'
  const demo = env.RADAR_DEMO_API_TARGET || 'http://127.0.0.1:8001'
  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        // '/api/' (with the slash) never matches '/api-demo/'.
        '/api/': { target: real, changeOrigin: true },
        '/api-demo/': {
          target: demo,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api-demo\//, '/api/'),
        },
      },
    },
    // three.js (~700 kB) is its own chunk, loaded lazily by the intro scene only.
    build: { chunkSizeWarningLimit: 800 },
    test: { environment: 'node', include: ['src/**/*.test.ts'] },
  }
})
