import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        timeout: 1800000,  // 30 分钟，匹配 Level 3/4 超时
      },
      '/kb': 'http://localhost:8000',
      '/research': {
        target: 'http://localhost:8000',
        timeout: 1800000,  // 30 分钟，SSE 流式连接
      },
    }
  }
})
