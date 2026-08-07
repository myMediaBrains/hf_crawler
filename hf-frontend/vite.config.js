// hf-frontend/vite.config.js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  },
  // ✅ Dable 차단을 위한 설정
  define: {
    'window.Dable': 'undefined',
    'window.__dable': 'false',
  },
  build: {
    rollupOptions: {
      external: ['dable', 'dable-*']  // Dable 패키지 번들링 제외
    }
  }
})