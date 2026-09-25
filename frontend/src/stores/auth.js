import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { clearSessionCache } from '../utils/session-cache'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const username = ref(localStorage.getItem('username') || '')
  const role = ref(localStorage.getItem('role') || 'user')

  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => role.value === 'admin')

  function login(t, user, r) {
    token.value = t
    username.value = user
    role.value = r || 'user'
    localStorage.setItem('token', t)
    localStorage.setItem('username', user)
    localStorage.setItem('role', r || 'user')
  }

  function logout() {
    token.value = ''
    username.value = ''
    role.value = 'user'
    // 清空登录凭据 + 本账号的会话痕迹，避免换账号登录后看到上一账号的聊天记录（信息泄露）
    // 与 axios 401 拦截器共用同一实现，保证两条退出路径行为一致
    clearSessionCache()
  }

  // 这里原有 kbUserId()：把 JWT payload 里的 sub 解出来，再由前端当 user_id 传给 /kb/*。
  // 已删除 —— 那正是知识库越权的根源（user_id 由客户端提供，服务端无从验证）。
  // 现在 user_id 统一由网关从 JWT 解析，见 KbController / RequestUserResolver。

  return { token, username, role, isLoggedIn, isAdmin, login, logout }
})
