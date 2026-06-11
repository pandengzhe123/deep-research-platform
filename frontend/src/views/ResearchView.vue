<template>
  <div class="app-shell">
    <!-- 侧边栏 -->
    <aside class="sidebar">
      <div class="sidebar-top">
        <div class="brand">
          <div class="brand-icon">🔬</div>
          <span>Deep Research</span>
        </div>
        <button class="btn-new-chat" @click="newChat" title="新建会话">＋</button>
      </div>

      <div class="session-list">
        <div v-for="s in sessions" :key="s.id"
             :class="['session-item', { active: s.id === currentSessionId }]"
             @click="switchSession(s)">
          <div class="session-dot"></div>
          <span class="session-title">{{ (s.question || '新会话').substring(0, 18) }}</span>
        </div>
      </div>

      <div class="sidebar-bottom">
        <div class="user-info" @click="confirmLogout" title="点击退出">
          <div class="avatar">{{ auth.username.charAt(0).toUpperCase() }}</div>
          <span>{{ auth.username }}</span>
        </div>
      </div>
    </aside>

    <!-- 主区域 -->
    <main class="main-area">
      <!-- 空状态 -->
      <div v-if="messages.length === 0 && !running" class="empty-state">
        <div class="empty-icon">✨</div>
        <h1>开始深度研究</h1>
        <p>输入任意问题，AI 会自动搜索网络、分析信息、生成带引用的深度报告。</p>
        <div class="quick-actions">
          <button v-for="q in quickQuestions" :key="q" @click="question = q; start()" class="quick-btn">{{ q }}</button>
        </div>
      </div>

      <!-- 消息流 -->
      <div v-else class="chat-flow" ref="msgContainer">
        <div v-for="(msg, i) in messages" :key="i" :class="['message', msg.role]">
          <div v-if="msg.role === 'user'" class="user-msg">
            <div class="user-bubble">{{ msg.content }}</div>
          </div>
          <div v-else-if="msg.role === 'thinking'" class="thinking-msg">
            <span class="dot-pulse"></span>
            <span>{{ msg.content }}</span>
          </div>
          <div v-else class="ai-msg">
            <div class="ai-badge">AI</div>
            <div class="ai-body" :id="'msg-' + i">
              <div v-if="msg.content.startsWith('❌')" class="error-msg">{{ msg.content }}</div>
              <div v-else-if="msg.content.startsWith('🤔')" class="clarify-msg">
                <div class="clarify-icon">❓</div>
                <div>
                  <strong>需要补充信息</strong>
                  <p>{{ msg.content.replace('🤔 ', '') }}</p>
                  <span class="clarify-hint">请在下方的输入框中补充说明后重新提交</span>
                </div>
              </div>
              <div v-else class="report-body" v-html="renderMarkdown(msg.content)"></div>
              <div v-if="msg.content && msg.content.length > 200" class="export-btns">
                <button @click="copyMD(msg.content)" class="btn-export">📋 复制</button>
                <button @click="downloadMD(msg.content)" class="btn-export">💾 下载 .md</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 输入栏 -->
      <div class="input-bar">
        <div class="input-tools">
          <select v-model="level" class="tool-select">
            <option :value="1">⚡ 极速</option>
            <option :value="2">🔍 搜索反思</option>
            <option :value="3">⚡⚡ 多路并行</option>
            <option :value="4">🧠 Supervisor</option>
          </select>
          <label :class="['tool-toggle', { active: kbEnabled }]">
            <input type="checkbox" v-model="kbEnabled" />
            📁 RAG
          </label>
          <span v-if="running" class="running-timer">⏳ {{ timerText }}</span>
        </div>
        <div class="input-row">
          <textarea ref="inputEl" v-model="question" placeholder="输入研究问题..." rows="1"
                    @keyup.ctrl.enter="start" @keyup.enter.exact.prevent
                    @input="autoResize" :disabled="running"></textarea>
          <button class="send-btn" @click="start" :disabled="running || !question.trim()">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>
          </button>
        </div>
      </div>
    </main>

    <!-- KB 侧面板 -->
    <aside class="kb-panel">
      <div class="kb-header">📁 知识库</div>
      <div class="kb-body">
        <label class="kb-upload-btn">
          <input type="file" ref="fileInput" accept=".txt,.md,.pdf" @change="uploadFile" hidden />
          ＋ 上传文件
        </label>
        <div v-if="kbMsg" class="kb-msg">{{ kbMsg }}</div>
        <div class="kb-files">
          <div v-for="f in kbFiles" :key="f.doc_id" class="kb-file-item">
            <span class="kb-file-icon">📄</span>
            <span class="kb-file-name">{{ f.doc_id }}</span>
            <button class="kb-file-del" @click="deleteFile(f.doc_id)">×</button>
          </div>
          <p v-if="!kbFiles.length" class="kb-empty">暂无文件</p>
        </div>
      </div>
    </aside>

    <!-- 退出确认弹窗 -->
    <div v-if="showLogoutModal" class="modal-overlay" @click.self="showLogoutModal = false">
      <div class="modal-box">
        <p class="modal-text">确定要退出登录吗？</p>
        <div class="modal-actions">
          <button class="modal-btn cancel" @click="showLogoutModal = false">取消</button>
          <button class="modal-btn confirm" @click="doLogout">退出</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import api from '../utils/api'
import { marked } from 'marked'

const router = useRouter()
const auth = useAuthStore()

const question = ref('')
const level = ref(2)
const kbEnabled = ref(false)
const running = ref(false)
const messages = ref([])
const sessions = ref([])
const currentSessionId = ref('')
const msgContainer = ref(null)
const inputEl = ref(null)
const needClarify = ref(false)
const showLogoutModal = ref(false)

const quickQuestions = ['量子计算是什么？', 'Java 21 虚拟线程的优势', '2025年AI领域的重要事件']
let contextHistory = ''  // 累积对话历史，传给后端

// ========== 计时器 ==========
const startTime = ref(0)
const timerText = ref('')
let timerId = null
function startTimer() {
  startTime.value = Date.now()
  timerId = setInterval(() => {
    const s = Math.floor((Date.now() - startTime.value) / 1000)
    timerText.value = `${Math.floor(s/60)}分${String(s%60).padStart(2,'0')}秒`
  }, 1000)
}
function stopTimer() { if (timerId) { clearInterval(timerId); timerId = null } }

// ========== 研究 ==========
async function start() {
  if (!question.value.trim() || running.value) return
  running.value = true

  const q = question.value
  messages.value.push({ role: 'user', content: q })
  // 模拟步骤让用户知道没卡死
  const steps = ['🔍 正在搜索相关信息...', '📖 正在分析网页内容...', '💭 正在整理研究发现...', '📝 正在撰写深度报告...']
  let stepIdx = 0
  const thinkingMsg = { role: 'thinking', content: steps[0] }
  messages.value.push(thinkingMsg)
  const stepTimer = setInterval(() => {
    stepIdx = (stepIdx + 1) % steps.length
    thinkingMsg.content = steps[stepIdx]
  }, 8000)
  question.value = ''
  if (inputEl.value) { inputEl.value.style.height = 'auto' }
  scrollDown()
  startTimer()

  try {
    const { data } = await api.post('/research', {
      question: q, level: level.value, kb_enabled: kbEnabled.value,
      context: contextHistory, session_id: currentSessionId.value || undefined
    })
    messages.value.pop()
    clearInterval(stepTimer)
    if (data.need_clarify) {
      messages.value.push({ role: 'assistant', content: '🤔 ' + data.question })
      contextHistory += (contextHistory ? '\n\n' : '') + '用户: ' + q + '\nAgent: （追问）' + data.question
      if (data.session_id) {
        currentSessionId.value = data.session_id
        localStorage.setItem('activeSession', data.session_id)
        localStorage.setItem('chat_' + data.session_id, JSON.stringify(messages.value))
        loadSessions()
      }
    } else if (data.report) {
      messages.value.push({ role: 'assistant', content: data.report })
      // 将本轮问答追加到对话历史
      contextHistory += (contextHistory ? '\n\n' : '') + '用户: ' + q + '\nAgent: （已回复报告）'
      if (data.session_id) {
        currentSessionId.value = data.session_id
        localStorage.setItem('activeSession', data.session_id)
        localStorage.setItem('chat_' + data.session_id, JSON.stringify(messages.value))
        loadSessions()
      }
    }
  } catch (e) {
    clearInterval(stepTimer)
    messages.value.pop()
    // 用户友好的错误提示
    let errMsg = '请求失败，请重试'
    const status = e.response?.status || 0
    if (status === 401 || status === 403) errMsg = '登录已过期，请重新登录'
    else if (status === 429) errMsg = '请求太频繁，请稍后再试'
    else if (status >= 500) errMsg = '服务暂时不可用，请稍后重试'
    else if (e.message?.includes('timeout') || e.code === 'ECONNABORTED') errMsg = '研究超时，请尝试简化问题或降低 Level'
    else if (e.message?.includes('Network Error')) errMsg = '网络连接失败，请检查网络后重试'
    messages.value.push({ role: 'assistant', content: '❌ ' + errMsg })
  } finally { running.value = false; stopTimer(); scrollDown() }
}

function renderMarkdown(text) {
  if (!text) return ''
  return marked(text)
}

function scrollDown() {
  nextTick(() => {
    if (msgContainer.value) msgContainer.value.scrollTop = msgContainer.value.scrollHeight
  })
}

function autoResize() {
  const el = inputEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 160) + 'px'
}

// ========== 会话 ==========
async function loadSessions() {
  try { const { data } = await api.get('/sessions'); sessions.value = data || [] } catch (e) {}
}

async function switchSession(s) {
  // 先存当前会话的消息
  if (currentSessionId.value) {
    localStorage.setItem('chat_' + currentSessionId.value, JSON.stringify(messages.value))
  }
  currentSessionId.value = s.id
  localStorage.setItem('activeSession', s.id)

  // 优先从 localStorage 恢复，但仅当缓存中包含完整报告时才跳过 API
  const saved = localStorage.getItem('chat_' + s.id)
  if (saved) {
    try {
      const cached = JSON.parse(saved)
      const lastMsg = cached[cached.length - 1]
      if (lastMsg && lastMsg.role === 'assistant' && lastMsg.content && lastMsg.content.length > 500) {
        messages.value = cached; contextHistory = ''; scrollDown(); return
      }
    } catch(e) {}
  }

  // fallback：从 API 加载
  try {
    const { data } = await api.get(`/sessions/${s.id}`)
    const fullReport = data.session?.report || ''
    const msgs = []
    let historyItems = []
    try { historyItems = JSON.parse(data.history || '[]') } catch(e) {}
    for (const item of historyItems) {
      if (item.startsWith('用户: ')) msgs.push({ role: 'user', content: item.slice(4) })
      else if (item !== 'Agent: （已回复报告）') msgs.push({ role: 'assistant', content: item.replace(/^Agent: /, '') })
    }
    if (fullReport) msgs.push({ role: 'assistant', content: fullReport })
    messages.value = msgs.length ? msgs : [
      { role: 'user', content: s.question || '' },
      { role: 'assistant', content: fullReport || '（报告已丢失）' }
    ]
    contextHistory = historyItems.join('\n')
    localStorage.setItem('chat_' + s.id, JSON.stringify(messages.value))  // 缓存到本地
  } catch (e) {
    messages.value = [
      { role: 'user', content: s.question || '' },
      { role: 'assistant', content: s.report || '（报告已丢失）' }
    ]
    contextHistory = `用户: ${s.question || ''}\nAgent: （已回复报告）`
  }
  scrollDown()
}

function newChat() {
  messages.value = []
  currentSessionId.value = ''
  question.value = ''
  contextHistory = ''
  localStorage.removeItem('activeSession')
  localStorage.removeItem('chat_' + currentSessionId.value)
}

// ========== KB ==========
const fileInput = ref(null)
const kbFiles = ref([])
const kbMsg = ref('')

async function loadKB() {
  try { const data = await fetch(`/kb/files?user_id=${auth.kbUserId()}`).then(r => r.json()); kbFiles.value = data.files || [] } catch (e) {}
}

async function uploadFile() {
  const file = fileInput.value?.files?.[0]
  if (!file) return
  kbMsg.value = '上传中...'
  const form = new FormData(); form.append('file', file)
  try {
    const resp = await fetch(`/kb/upload?user_id=${auth.kbUserId()}`, { method: 'POST', body: form })
    const data = await resp.json()
    kbMsg.value = data.status === 'ok' ? `✅ ${data.doc_id}` : `❌ ${data.message || '失败'}`
    if (data.status === 'ok') { fileInput.value.value = ''; loadKB() }
  } catch (e) { kbMsg.value = '❌ ' + e.message }
}

async function deleteFile(docId) {
  if (!confirm(`删除 ${docId}？`)) return
  await fetch(`/kb/files/${docId}?user_id=${auth.kbUserId()}`, { method: 'DELETE' })
  loadKB()
}

function confirmLogout() {
  showLogoutModal.value = true
}

function copyMD(text) {
  navigator.clipboard.writeText(text).then(() => {
    alert('已复制到剪贴板')
  }).catch(() => alert('复制失败'))
}

function downloadMD(text) {
  const blob = new Blob([text], { type: 'text/markdown' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = 'deep_research_report.md'
  a.click()
  URL.revokeObjectURL(a.href)
}

function doLogout() {
  auth.logout()
  router.push('/login')
}

onMounted(async () => {
  await loadSessions()
  loadKB()
  // 从 localStorage 恢复上次会话的聊天记录
  const activeId = localStorage.getItem('activeSession')
  if (activeId) {
    currentSessionId.value = activeId
    const saved = localStorage.getItem('chat_' + activeId)
    if (saved) {
      try { messages.value = JSON.parse(saved) } catch(e) {}
    } else {
      const s = sessions.value.find(s => s.id === activeId)
      if (s) switchSession(s)
    }
  }
})
</script>

<style scoped>
/* ================================================================
   LAYOUT
   ================================================================ */
.app-shell { display: flex; height: 100vh; overflow: hidden; }

/* ================================================================
   SIDEBAR
   ================================================================ */
.sidebar { width: 260px; background: linear-gradient(180deg, #1e1b4b 0%, #312e81 100%); color: #c7d2fe; display: flex; flex-direction: column; flex-shrink: 0; }
.sidebar-top { padding: 20px 16px 12px; display: flex; justify-content: space-between; align-items: center; }
.brand { display: flex; align-items: center; gap: 10px; }
.brand-icon { font-size: 22px; }
.brand span { font-weight: 700; font-size: 15px; color: #e0e7ff; }
.btn-new-chat { width: 34px; height: 34px; border-radius: 50%; border: 2px solid #4f46e5; background: rgba(99,102,241,.15); color: #a5b4fc; font-size: 20px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all .15s; }
.btn-new-chat:hover { background: #4f46e5; color: #fff; }
.session-list { flex: 1; overflow-y: auto; padding: 0 8px 12px; }
.session-item { padding: 10px 14px; border-radius: 10px; cursor: pointer; font-size: 13px; margin-bottom: 3px; display: flex; align-items: center; gap: 10px; color: #a5b4fc; transition: all .12s; }
.session-item:hover { background: rgba(255,255,255,.06); }
.session-item.active { background: #4338ca; color: #fff; }
.session-dot { width: 6px; height: 6px; border-radius: 50%; background: #6366f1; flex-shrink: 0; }
.session-item.active .session-dot { background: #fff; }
.session-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sidebar-bottom { padding: 14px 16px; border-top: 1px solid rgba(255,255,255,.06); }
.user-info { display: flex; align-items: center; gap: 10px; cursor: pointer; padding: 8px; border-radius: 8px; transition: background .12s; }
.user-info:hover { background: rgba(255,255,255,.06); }
.avatar { width: 32px; height: 32px; border-radius: 50%; background: #6366f1; color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 14px; }
.user-info span { font-size: 13px; color: #a5b4fc; }

/* ================================================================
   MAIN AREA
   ================================================================ */
.main-area { flex: 1; display: flex; flex-direction: column; min-width: 0; position: relative; }

/* ================================================================
   EMPTY STATE
   ================================================================ */
.empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; flex: 1; padding: 40px; text-align: center; background: #fff; }
.empty-icon { font-size: 56px; margin-bottom: 16px; }
.empty-state h1 { font-size: 24px; font-weight: 700; color: #1e293b; margin-bottom: 8px; }
.empty-state p { color: #64748b; font-size: 15px; max-width: 420px; margin-bottom: 32px; line-height: 1.6; }
.quick-actions { display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; }
.quick-btn { padding: 10px 20px; border: 1px solid #e2e8f0; border-radius: 99px; background: #fff; color: #475569; font-size: 13px; cursor: pointer; transition: all .12s; }
.quick-btn:hover { border-color: #6366f1; color: #4f46e5; background: #eef2ff; }

/* ================================================================
   CHAT FLOW
   ================================================================ */
.chat-flow { flex: 1; overflow-y: auto; padding: 32px 40px; background: #fff; }
.message { margin-bottom: 24px; }
.user-msg { display: flex; justify-content: flex-end; }
.user-bubble { background: #6366f1; color: #fff; padding: 12px 20px; border-radius: 18px 18px 4px 18px; font-size: 14px; line-height: 1.6; max-width: 70%; word-wrap: break-word; }
.thinking-msg { display: flex; align-items: center; gap: 10px; color: #94a3b8; font-size: 14px; padding: 8px 0; }
.dot-pulse { width: 8px; height: 8px; border-radius: 50%; background: #6366f1; animation: pulse 1.2s ease-in-out infinite; }
@keyframes pulse { 0%,100% { opacity: .3; } 50% { opacity: 1; } }
.ai-msg { display: flex; gap: 14px; }
.ai-badge { width: 32px; height: 32px; border-radius: 8px; background: #eef2ff; color: #4f46e5; font-size: 12px; font-weight: 700; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.ai-body { flex: 1; min-width: 0; }

/* ================================================================
   REPORT STYLES
   ================================================================ */
.report-body { font-size: 15px; line-height: 1.85; color: #334155; }
.report-body :deep(h1) { font-size: 1.4rem; font-weight: 700; margin: 20px 0 14px; padding-bottom: 10px; border-bottom: 2px solid #e2e8f0; color: #0f172a; }
.report-body :deep(h2) { font-size: 1.15rem; font-weight: 700; margin: 24px 0 10px; color: #4338ca; }
.report-body :deep(h3) { font-size: 1rem; font-weight: 600; margin: 16px 0 8px; color: #475569; }
.report-body :deep(p) { margin: 10px 0; }
.report-body :deep(ul), .report-body :deep(ol) { padding-left: 22px; margin: 10px 0; }
.report-body :deep(li) { margin: 4px 0; }
.report-body :deep(a) { color: #4f46e5; text-decoration: none; }
.report-body :deep(a:hover) { text-decoration: underline; }
.report-body :deep(code) { background: #f1f5f9; padding: 2px 6px; border-radius: 4px; font-size: 13px; color: #be185d; }
.report-body :deep(strong) { font-weight: 600; color: #1e293b; }
.report-body :deep(blockquote) { border-left: 3px solid #e2e8f0; padding-left: 14px; color: #64748b; margin: 12px 0; }

/* ================================================================
   SPECIAL MESSAGES
   ================================================================ */
.error-msg { color: #dc2626; background: #fef2f2; padding: 14px 18px; border-radius: 10px; font-size: 14px; }
.clarify-msg { display: flex; gap: 14px; background: #fffbeb; border: 1px solid #fde68a; padding: 16px 18px; border-radius: 12px; font-size: 14px; color: #92400e; }
.clarify-icon { font-size: 24px; flex-shrink: 0; }
.clarify-msg strong { display: block; margin-bottom: 4px; }
.clarify-msg p { margin: 4px 0; }
.clarify-hint { font-size: 12px; color: #a16207; }

/* ================================================================
   INPUT BAR
   ================================================================ */
.input-bar { padding: 14px 24px 18px; background: #fff; border-top: 1px solid #e2e8f0; }
.input-tools { display: flex; gap: 10px; margin-bottom: 10px; align-items: center; flex-wrap: wrap; }
.tool-select { padding: 5px 10px; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 12px; background: #f8fafc; color: #475569; cursor: pointer; }
.tool-toggle { font-size: 12px; padding: 5px 12px; border: 1px solid #e2e8f0; border-radius: 6px; cursor: pointer; display: flex; align-items: center; gap: 4px; color: #94a3b8; transition: all .12s; }
.tool-toggle.active { border-color: #6366f1; color: #4f46e5; background: #eef2ff; }
.tool-toggle input { display: none; }
.running-timer { margin-left: auto; font-size: 12px; color: #6366f1; font-weight: 600; }
.input-row { display: flex; gap: 12px; align-items: flex-end; }
.input-row textarea { flex: 1; border: 2px solid #e2e8f0; border-radius: 14px; padding: 12px 16px; resize: none; font-size: 14px; font-family: inherit; outline: none; max-height: 160px; transition: border-color .15s; }
.input-row textarea:focus { border-color: #6366f1; }
.send-btn { width: 44px; height: 44px; border-radius: 50%; border: none; background: #6366f1; color: #fff; cursor: pointer; display: flex; align-items: center; justify-content: center; flex-shrink: 0; transition: all .12s; }
.send-btn:hover { background: #4f46e5; transform: scale(1.05); }
.send-btn:disabled { background: #cbd5e1; cursor: not-allowed; transform: none; }

/* ================================================================
   KB PANEL
   ================================================================ */
.kb-panel { width: 240px; background: #fff; border-left: 1px solid #e2e8f0; display: flex; flex-direction: column; flex-shrink: 0; }
.kb-header { padding: 20px 16px 12px; font-weight: 700; font-size: 13px; color: #475569; border-bottom: 1px solid #f1f5f9; }
.kb-body { flex: 1; overflow-y: auto; padding: 12px 16px; }
.kb-upload-btn { display: block; padding: 10px; text-align: center; border: 2px dashed #e2e8f0; border-radius: 10px; font-size: 13px; color: #6366f1; cursor: pointer; margin-bottom: 10px; transition: all .12s; }
.kb-upload-btn:hover { border-color: #6366f1; background: #eef2ff; }
.kb-msg { font-size: 12px; color: #64748b; margin-bottom: 8px; }
.kb-files { font-size: 12px; }
.kb-file-item { display: flex; align-items: center; gap: 6px; padding: 6px 0; border-bottom: 1px solid #f8fafc; }
.kb-file-icon { flex-shrink: 0; }
.kb-file-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #475569; }
.kb-file-del { border: none; background: none; color: #94a3b8; cursor: pointer; font-size: 14px; padding: 2px; }
.kb-file-del:hover { color: #dc2626; }
.kb-empty { color: #94a3b8; font-size: 12px; text-align: center; padding: 20px 0; }

/* 导出按钮 */
.export-btns { margin-top: 12px; display: flex; gap: 8px; }
.btn-export { padding: 4px 12px; font-size: 12px; border: 1px solid #d1d5db; border-radius: 6px; background: #fff; color: #6b7280; cursor: pointer; }
.btn-export:hover { border-color: #6366f1; color: #4f46e5; background: #eef2ff; }

@media (max-width: 1024px) { .kb-panel { display: none; } }
@media (max-width: 768px) { .sidebar { display: none; } .chat-flow { padding: 20px 16px; } .user-bubble { max-width: 85%; } }

/* 退出弹窗 */
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.4); display: flex; align-items: center; justify-content: center; z-index: 100; backdrop-filter: blur(4px); }
.modal-box { background: #fff; padding: 28px 32px; border-radius: 16px; box-shadow: 0 20px 60px rgba(0,0,0,.2); text-align: center; min-width: 300px; }
.modal-text { font-size: 15px; color: #1e293b; margin-bottom: 20px; }
.modal-actions { display: flex; gap: 12px; justify-content: center; }
.modal-btn { padding: 10px 32px; border-radius: 10px; font-size: 14px; cursor: pointer; border: none; }
.modal-btn.cancel { background: #f1f5f9; color: #475569; }
.modal-btn.cancel:hover { background: #e2e8f0; }
.modal-btn.confirm { background: #ef4444; color: #fff; }
.modal-btn.confirm:hover { background: #dc2626; }
</style>
