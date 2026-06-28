import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const username = ref(localStorage.getItem('username') || '')

  const isLoggedIn = computed(() => !!token.value)

  function login(t, user) {
    token.value = t
    username.value = user
    localStorage.setItem('token', t)
    localStorage.setItem('username', user)
  }

  function logout() {
    token.value = ''
    username.value = ''
    localStorage.removeItem('token')
    localStorage.removeItem('username')
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

  return { token, username, isLoggedIn, login, logout, kbUserId }
})
