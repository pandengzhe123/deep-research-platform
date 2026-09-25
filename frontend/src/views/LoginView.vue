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
        <!-- 邀请码只在「需要邀请码」的模式下出现：完全开放时不需要，关闭时填不了 -->
        <div class="input-group" v-if="inviteRequired">
          <input v-model="inviteCode" placeholder="邀请码（仅注册需要）" @keyup.enter="doRegister" />
        </div>

        <button class="btn-login" @click="doLogin" :disabled="loading">
          {{ loading ? '...' : '登 录' }}
        </button>

        <div class="divider"><span>或</span></div>

        <button class="btn-register" @click="doRegister" :disabled="loading || registerOpen !== true">
          {{ registerBtnText }}
        </button>
        <p v-if="registerOpen === false" class="notice">
          本站已关闭注册。需要账号请联系管理员。
        </p>

        <p v-if="msg" :class="['msg', msgType]">{{ msg }}</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import api from '../utils/api'

const router = useRouter()
const auth = useAuthStore()
const username = ref('')
const password = ref('')
const inviteCode = ref('')
const msg = ref('')
const msgType = ref('')
const loading = ref(false)

// null = 还在查询服务端；true/false = 服务端返回的真实状态
const registerOpen = ref(null)
// 是否需要邀请码（仅 invite 模式为 true）
const inviteRequired = ref(false)

const registerBtnText = computed(() => {
  if (registerOpen.value === null) return '检查中…'
  return registerOpen.value ? '注册新账号' : '注册已关闭'
})

// 空字段必须显式提示：这两个函数原先在字段为空时直接 return，
// 界面上表现为「点了按钮完全没反应」，用户无从判断是没填、坏了、还是被禁用。
function fail(text) {
  msg.value = text
  msgType.value = 'error'
}

onMounted(async () => {
  try {
    const { data } = await api.get('/auth/register-open')
    registerOpen.value = !!data.open
    inviteRequired.value = !!data.inviteRequired
  } catch {
    // 查不到就按「关闭」处理：宁可提示需联系管理员，也不给一个点了必然失败的按钮
    registerOpen.value = false
  }
})

async function doLogin() {
  if (!username.value || !password.value) { fail('请输入用户名和密码'); return }
  loading.value = true; msg.value = ''
  try {
    const { data } = await api.post('/auth/login', { username: username.value, password: password.value })
    if (data.token) { auth.login(data.token, data.username, data.role); router.push('/') }
    else { fail(data.message || '登录失败') }
  } catch (e) { fail(e.response?.data?.message || '网络错误') }
  finally { loading.value = false }
}

async function doRegister() {
  if (!username.value || !password.value) { fail('请先填写用户名和密码'); return }
  // 与服务端 AuthController.MIN_PASSWORD_LENGTH 保持一致
  if (password.value.length < 8) { fail('密码至少 8 位'); return }
  // 只有 invite 模式才要邀请码；open 模式直接注册
  if (inviteRequired.value && !inviteCode.value) { fail('请填写邀请码'); return }
  loading.value = true; msg.value = ''
  try {
    const { data } = await api.post('/auth/register', {
      username: username.value, password: password.value, inviteCode: inviteCode.value,
    })
    if (data.token) { auth.login(data.token, data.username, data.role); router.push('/') }
    else { fail(data.message || '注册失败') }
  } catch (e) { fail(e.response?.data?.message || '网络错误') }
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
.btn-register:disabled { color: #94a3b8; border-color: #e2e8f0; background: #f8fafc; cursor: not-allowed; }
.notice { margin-top: 10px; font-size: 12px; color: #94a3b8; line-height: 1.5; }
.msg { margin-top: 16px; font-size: 13px; }
.error { color: #dc2626; }
</style>
