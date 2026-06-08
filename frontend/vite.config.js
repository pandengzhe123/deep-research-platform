import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://localhost:8080',       // Java 网关
      '/kb': 'http://localhost:8000',          // Python KB 接口
      '/research': 'http://localhost:8000',    // Python 研究接口（SSE 用）
    }
  }
})
