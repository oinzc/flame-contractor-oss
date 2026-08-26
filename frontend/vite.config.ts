import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(() => {
  const apiTarget = process.env.FLAME_API_TARGET ?? 'http://127.0.0.1:8000'
  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      // Windows 下原子写入会触发文件监听的 EBUSY 崩溃，改用轮询监听
      watch: { usePolling: true, interval: 400 },
      proxy: {
        '/api': { target: apiTarget, changeOrigin: true },
        '/health': { target: apiTarget, changeOrigin: true },
      },
    },
  }
})
