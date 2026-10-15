import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', 'VITE_')
  return {
    plugins: [react()],
    server: {
      proxy: { '/api': env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000' },
    },
    test: { environment: 'jsdom', setupFiles: './src/test-setup.ts' },
  }
})
