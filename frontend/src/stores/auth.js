import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

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
    localStorage.removeItem('token')
    localStorage.removeItem('username')
    localStorage.removeItem('role')
  }

  function kbUserId() {
    // 从 JWT payload 解析 subject（数字 ID），和 Java extractUserId 保持一致
    try {
      const payload = JSON.parse(atob(token.value.split('.')[1]))
      return payload.sub || username.value || 'default'
    } catch {
      return username.value || 'default'
    }
  }

  return { token, username, role, isLoggedIn, isAdmin, login, logout, kbUserId }
})
