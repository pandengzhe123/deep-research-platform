import axios from 'axios'
import { clearSessionCache } from './session-cache'

// 普通接口（会话列表 / 单会话 / 知识库 / 控制台）都是秒级响应：
// 60s 足够，且能尽早暴露挂死的连接。研究接口是 SSE，走原生 fetch，不受此超时影响。
const api = axios.create({ baseURL: '/api', timeout: 60000 })

api.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  res => res,
  err => {
    if (err.response?.status === 401) {
      // 与 auth.logout() 共用同一套清理：只删 token 会让下一个登录的用户
      // 看到上一个用户的本地聊天缓存（B42）
      clearSessionCache()
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

export default api
