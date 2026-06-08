<template>
  <div class="login-page">
    <div class="login-card">
      <div class="brand">🔬</div>
      <h1>Deep Research</h1>
      <p class="subtitle">AI 深度研究助手</p>

      <div class="form">
        <div class="input-group">
          <input v-model="username" placeholder="用户名" @keyup.enter="doLogin" />
        </div>
        <div class="input-group">
          <input v-model="password" type="password" placeholder="密码" @keyup.enter="doLogin" />
        </div>

        <button class="btn-login" @click="doLogin" :disabled="loading">
          {{ loading ? '...' : '登 录' }}
        </button>

        <div class="divider"><span>或</span></div>

        <button class="btn-register" @click="doRegister" :disabled="loading">
          注册新账号
        </button>

        <p v-if="msg" :class="['msg', msgType]">{{ msg }}</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import api from '../utils/api'

const router = useRouter()
const auth = useAuthStore()
const username = ref('')
const password = ref('')
const msg = ref('')
const msgType = ref('')
const loading = ref(false)

async function doLogin() {
  if (!username.value || !password.value) return
  loading.value = true; msg.value = ''
  try {
    const { data } = await api.post('/auth/login', { username: username.value, password: password.value })
    if (data.token) { auth.login(data.token, data.username); router.push('/') }
    else { msg.value = data.message || '登录失败'; msgType.value = 'error' }
  } catch (e) { msg.value = e.response?.data?.message || '网络错误'; msgType.value = 'error' }
  finally { loading.value = false }
}

async function doRegister() {
  if (!username.value || !password.value) return
  if (password.value.length < 4) { msg.value = '密码至少 4 位'; msgType.value = 'error'; return }
  loading.value = true; msg.value = ''
  try {
    const { data } = await api.post('/auth/register', { username: username.value, password: password.value })
    if (data.token) { auth.login(data.token, data.username); router.push('/') }
    else { msg.value = data.message || '注册失败'; msgType.value = 'error' }
  } catch (e) { msg.value = e.response?.data?.message || '网络错误'; msgType.value = 'error' }
  finally { loading.value = false }
}
</script>

<style scoped>
.login-page { display: flex; align-items: center; justify-content: center; min-height: 100vh; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
.login-card { background: #fff; padding: 48px 44px; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,.15); width: 100%; max-width: 400px; text-align: center; }
.brand { font-size: 48px; margin-bottom: 8px; }
h1 { font-size: 1.6rem; font-weight: 800; color: #1e293b; margin-bottom: 4px; }
.subtitle { color: #94a3b8; font-size: 14px; margin-bottom: 32px; }
.input-group { margin-bottom: 14px; }
.input-group input { width: 100%; padding: 14px 16px; border: 2px solid #e2e8f0; border-radius: 12px; font-size: 15px; outline: none; transition: border-color .15s; }
.input-group input:focus { border-color: #6366f1; }
.btn-login { width: 100%; padding: 14px; background: #6366f1; color: #fff; border: none; border-radius: 12px; font-size: 15px; font-weight: 600; cursor: pointer; transition: all .12s; }
.btn-login:hover { background: #4f46e5; }
.btn-login:disabled { opacity: .6; cursor: not-allowed; }
.divider { margin: 20px 0; display: flex; align-items: center; gap: 16px; color: #94a3b8; font-size: 13px; }
.divider::before, .divider::after { content: ''; flex: 1; height: 1px; background: #e2e8f0; }
.btn-register { width: 100%; padding: 14px; background: #fff; color: #6366f1; border: 2px solid #e2e8f0; border-radius: 12px; font-size: 15px; font-weight: 600; cursor: pointer; transition: all .12s; }
.btn-register:hover { border-color: #6366f1; background: #eef2ff; }
.msg { margin-top: 16px; font-size: 13px; }
.error { color: #dc2626; }
</style>
