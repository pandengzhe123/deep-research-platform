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
      <div v-if="messages.length === 0 && !isViewingResearch" class="empty-state">
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
              <div v-if="msg.content.startsWith('错误：')" class="error-msg">{{ msg.content }}</div>
              <div v-else-if="msg.content.startsWith('需要澄清：')" class="clarify-msg">
                <div class="clarify-icon">❓</div>
                <div>
                  <strong>需要补充信息</strong>
                  <p>{{ msg.content.replace('需要澄清：', '') }}</p>
                  <span class="clarify-hint">请在下方的输入框中补充说明后重新提交</span>
                </div>
              </div>
              <div v-else class="report-body" v-html="renderMarkdown(msg.content)"></div>
              <div v-if="msg.content && msg.content.length > 200" class="export-btns">
                <button @click="copyMD(msg.content)" class="btn-export">复制</button>
                <button @click="downloadMD(msg.content)" class="btn-export">下载 .md</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 输入栏 -->
      <div class="input-bar">
        <div class="input-tools">
          <select v-model="level" class="tool-select">
            <option :value="1">L1 · 极速</option>
            <option :value="2">L2 · 搜索反思</option>
            <option :value="3">L3 · 多路并行</option>
            <option :value="4">L4 · Supervisor</option>
          </select>
          <label :class="['tool-toggle', { active: kbEnabled }]">
            <input type="checkbox" v-model="kbEnabled" />
            RAG
          </label>
          <span v-if="isViewingResearch" class="running-timer"><span class="timer-spinner"></span>研究中 {{ timerText }}</span>
        </div>
        <div class="input-row">
          <textarea ref="inputEl" v-model="question" placeholder="输入研究问题..." rows="1"
                    @keyup.ctrl.enter="start" @keyup.enter.exact.prevent
                    @input="autoResize" :disabled="isViewingResearch"></textarea>
          <button v-if="isViewingResearch" class="stop-btn" @click="stopResearch">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>
          </button>
          <button v-else class="send-btn" @click="start" :disabled="running || !question.trim()">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>
          </button>
        </div>
      </div>
    </main>

    <!-- KB 侧面板 -->
    <aside class="kb-panel">
      <div class="kb-header">知识库</div>
      <div class="kb-body">
        <label class="kb-upload-btn">
          <input type="file" ref="fileInput" accept=".txt,.md,.pdf" @change="uploadFile" hidden />
          ＋ 上传文件
        </label>
        <div v-if="kbMsg" class="kb-msg">{{ kbMsg }}</div>
        <div class="kb-files">
          <div v-for="f in kbFiles" :key="f.doc_id" class="kb-file-item">
            <input type="checkbox" :value="f.doc_id" v-model="selectedDocs" class="kb-file-check">
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
import { ref, computed, onMounted, nextTick } from 'vue'
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
let _activeResearch = null  // { sessionId, messages } 正在进行的研究，供 switchSession 识别
let _abortController = null  // 用于中断 fetch 连接
const researchSessionId = ref(null)  // 响应式：正在研究的会话 ID，控制 UI 显示
const isViewingResearch = computed(() => running.value && researchSessionId.value === currentSessionId.value)

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
  const thinkingMsg = { role: 'thinking', content: '正在启动研究...' }
  // 研究用独立的消息列表，不被 switchSession 覆盖
  const myMessages = [...messages.value, { role: 'user', content: q }, thinkingMsg]
  messages.value = myMessages
  let mySessionId = currentSessionId.value || null  // 研究绑定的会话 ID
  researchSessionId.value = mySessionId
  _activeResearch = { sessionId: mySessionId, messages: myMessages }
  const icons = { searching: '搜索', kb_searching: 'RAG', thinking: '反思', planned: '就绪', planning: '规划', decided: '完成', reporting: '撰写' }

  question.value = ''
  if (inputEl.value) { inputEl.value.style.height = 'auto' }
  scrollDown()
  startTimer()

  // 辅助函数：是否正在查看研究会话
  const isViewing = () => currentSessionId.value === mySessionId

  // 辅助函数：处理一条 SSE 事件
  function handleEvent(eventName, eventData) {
    if (!eventData) return

    if (eventName === 'session') {
      try {
        const d = JSON.parse(eventData)
        if (d.id) {
          mySessionId = d.id
          _activeResearch.sessionId = d.id
          researchSessionId.value = d.id
          currentSessionId.value = d.id
          localStorage.setItem('activeSession', d.id)
          loadSessions()
        }
      } catch(e) {}

    } else if (eventName === 'status') {
      try {
        const d = JSON.parse(eventData)
        const round = d.round ? `第${d.round}轮 · ` : ''
        const label = icons[d.step] || '处理'
        thinkingMsg.content = `${round}${label} — ${d.message || ''}`
        if (isViewing()) scrollDown()
      } catch(e) {}

    } else if (eventName === 'done') {
      try {
        const d = JSON.parse(eventData)
        // 从研究消息列表中移除 thinking，加入报告
        const thinkIdx = myMessages.findIndex(m => m.role === 'thinking')
        if (thinkIdx >= 0) myMessages.splice(thinkIdx, 1)
        if (d.need_clarify) {
          myMessages.push({ role: 'assistant', content: '需要澄清：' + (d.question || '') })
          contextHistory += (contextHistory ? '\n\n' : '') + '用户: ' + q + '\nAgent: （追问）' + (d.question || '')
        } else if (d.report) {
          const elapsed = Math.floor((Date.now() - startTime.value) / 1000)
          const timeStr = `${Math.floor(elapsed/60)}分${String(elapsed%60).padStart(2,'0')}秒`
          myMessages.push({ role: 'assistant', content: `> 研究耗时 ${timeStr}\n\n${d.report}` })
          contextHistory += (contextHistory ? '\n\n' : '') + '用户: ' + q + '\nAgent: ' + (d.report || '')
        }
        // 无论用户是否在看，都存 localStorage
        if (mySessionId) {
          localStorage.setItem('chat_' + mySessionId, JSON.stringify(myMessages))
          loadSessions()
        }
        // 如果用户正在看这个会话，刷新 UI
        if (isViewing()) { messages.value = myMessages; scrollDown() }
      } catch(e) {}

    } else if (eventName === 'error') {
      const thinkIdx = myMessages.findIndex(m => m.role === 'thinking')
      if (thinkIdx >= 0) myMessages.splice(thinkIdx, 1)
      try {
        const d = JSON.parse(eventData)
        myMessages.push({ role: 'assistant', content: '错误：' + (d.message || '研究失败') })
      } catch(e) {
        myMessages.push({ role: 'assistant', content: '错误：研究失败' })
      }
      if (mySessionId) localStorage.setItem('chat_' + mySessionId, JSON.stringify(myMessages))
      if (isViewing()) { messages.value = myMessages; scrollDown() }
    }
  }

  try {
    _abortController = new AbortController()
    const token = localStorage.getItem('token')
    const response = await fetch('/api/research/stream', {
      method: 'POST',
      signal: _abortController.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        question: q, level: level.value, kb_enabled: kbEnabled.value,
        context: contextHistory, session_id: currentSessionId.value || undefined,
        rag_doc_ids: kbEnabled.value ? selectedDocs.value : [],
      }),
    })

    if (!response.ok) throw new Error(`HTTP ${response.status}`)

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop()

      for (const part of parts) {
        if (!part.trim()) continue
        let eventName = 'message', eventData = ''
        for (const line of part.split('\n')) {
          if (line.startsWith('event:')) eventName = line.slice(6).trim()
          else if (line.startsWith('data:')) eventData += line.slice(5).trim()
        }
        handleEvent(eventName, eventData)
      }
    }

    // 处理 buffer 残留
    if (buffer.trim()) {
      let eventName = 'message', eventData = ''
      for (const line of buffer.split('\n')) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim()
        else if (line.startsWith('data:')) eventData += line.slice(5).trim()
      }
      handleEvent(eventName, eventData)
    }

    // 流结束但没收到 done（异常断开）
    if (myMessages.some(m => m.role === 'thinking')) {
      const idx = myMessages.findIndex(m => m.role === 'thinking')
      myMessages.splice(idx, 1)
      myMessages.push({ role: 'assistant', content: '错误：连接中断，请重试' })
      if (isViewing()) messages.value = myMessages
    }

  } catch (e) {
    const idx = myMessages.findIndex(m => m.role === 'thinking')
    if (idx >= 0) myMessages.splice(idx, 1)
    if (e.name === 'AbortError') {
      // 用户主动停止
      myMessages.push({ role: 'assistant', content: '研究已停止。如需继续，请重新提问。' })
    } else {
      let errMsg = '请求失败，请重试'
      if (e.message?.includes('401') || e.message?.includes('403')) errMsg = '登录已过期，请重新登录'
      else if (e.message?.includes('timeout')) errMsg = '研究超时，请尝试简化问题或降低 Level'
      else if (e.message?.includes('Failed to fetch')) errMsg = '网络连接失败，请检查网络后重试'
      myMessages.push({ role: 'assistant', content: '错误：' + errMsg })
    }
    if (mySessionId) localStorage.setItem('chat_' + mySessionId, JSON.stringify(myMessages))
    if (isViewing()) messages.value = myMessages
  } finally { running.value = false; _activeResearch = null; researchSessionId.value = null; _abortController = null; stopTimer(); scrollDown() }
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

function stopResearch() {
  if (_abortController) _abortController.abort()
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
  // 先存当前会话的消息（但不覆盖正在研究的会话，它有自己的 myMessages）
  if (currentSessionId.value && (!_activeResearch || currentSessionId.value !== _activeResearch.sessionId)) {
    localStorage.setItem('chat_' + currentSessionId.value, JSON.stringify(messages.value))
  }
  currentSessionId.value = s.id
  localStorage.setItem('activeSession', s.id)

  // 如果切回正在研究的会话，直接用实时数据（含 thinking 进度 + 计时器）
  if (_activeResearch && s.id === _activeResearch.sessionId) {
    messages.value = _activeResearch.messages
    scrollDown()
    return
  }

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
      if (typeof item === 'string') {
        // 兼容旧格式纯文本
        if (item.startsWith('用户: ')) msgs.push({ role: 'user', content: item.slice(4) })
        else if (item.startsWith('Agent: ')) msgs.push({ role: 'assistant', content: item.slice(7) })
      } else if (typeof item === 'object' && item.role) {
        // 新格式结构化消息
        msgs.push({
          role: item.role === 'user' ? 'user' : 'assistant',
          content: item.content || '',
          time: item.time || ''
        })
      }
    }
    if (fullReport && !msgs.some(m => m.role === 'assistant' && m.content === fullReport)) {
      msgs.push({ role: 'assistant', content: fullReport })
    }
    messages.value = msgs.length ? msgs : [
      { role: 'user', content: s.question || '' },
      { role: 'assistant', content: fullReport || '（报告已丢失）' }
    ]
    contextHistory = msgs.map(m => `[${m.time || ''}] ${m.role === 'user' ? '用户' : 'Agent'}: ${m.content}`).join('\n')
    localStorage.setItem('chat_' + s.id, JSON.stringify(messages.value))  // 缓存到本地
  } catch (e) {
    messages.value = [
      { role: 'user', content: s.question || '' },
      { role: 'assistant', content: s.report || '（报告已丢失）' }
    ]
    contextHistory = `用户: ${s.question || ''}\nAgent: ${s.report || ''}`
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
const selectedDocs = ref([])  // 勾选的文档 ID

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
    kbMsg.value = data.status === 'ok' ? `已上传：${data.doc_id}` : `失败：${data.message || '未知错误'}`
    if (data.status === 'ok') { fileInput.value.value = ''; loadKB() }
  } catch (e) { kbMsg.value = '失败：' + e.message }
}

async function deleteFile(docId) {
  if (!confirm(`删除 ${docId}？`)) return
  await fetch(`/kb/files/${docId}?user_id=${auth.kbUserId()}`, { method: 'DELETE' })
  selectedDocs.value = selectedDocs.value.filter(id => id !== docId)
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
.empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; flex: 1; padding: 40px; text-align: center; background: linear-gradient(135deg, #fafbff 0%, #f0f4ff 100%); }
.empty-icon { font-size: 56px; margin-bottom: 16px; filter: drop-shadow(0 4px 8px rgba(99,102,241,.2)); }
.empty-state h1 { font-size: 24px; font-weight: 700; color: #1e293b; margin-bottom: 8px; }
.empty-state p { color: #64748b; font-size: 15px; max-width: 420px; margin-bottom: 32px; line-height: 1.6; }
.quick-actions { display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; }
.quick-btn { padding: 10px 20px; border: 1px solid #e2e8f0; border-radius: 99px; background: #fff; color: #475569; font-size: 13px; cursor: pointer; transition: all .2s; box-shadow: 0 1px 3px rgba(0,0,0,.04); }
.quick-btn:hover { border-color: #6366f1; color: #4f46e5; background: #eef2ff; transform: translateY(-1px); box-shadow: 0 4px 12px rgba(99,102,241,.15); }

/* ================================================================
   CHAT FLOW
   ================================================================ */
.chat-flow { flex: 1; overflow-y: auto; padding: 32px 40px; background: linear-gradient(180deg, #f8faff 0%, #fff 40%, #fff 100%); }
.message { margin-bottom: 24px; animation: msg-in .3s ease-out; }
@keyframes msg-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.user-msg { display: flex; justify-content: flex-end; }
.user-bubble { background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%); color: #fff; padding: 12px 20px; border-radius: 18px 18px 4px 18px; font-size: 14px; line-height: 1.6; max-width: 70%; word-wrap: break-word; box-shadow: 0 2px 12px rgba(99,102,241,.25); }
.thinking-msg { display: flex; align-items: center; gap: 12px; color: #6366f1; font-size: 14px; padding: 12px 16px; background: linear-gradient(135deg, #eef2ff, #f5f3ff); border-radius: 12px; border: 1px solid #e0e7ff; }
.dot-pulse { width: 8px; height: 8px; border-radius: 50%; background: #6366f1; animation: pulse 1.2s ease-in-out infinite; box-shadow: 0 0 6px rgba(99,102,241,.4); }
@keyframes pulse { 0%,100% { opacity: .3; transform: scale(.8); } 50% { opacity: 1; transform: scale(1.2); } }
.ai-msg { display: flex; gap: 14px; }
.ai-badge { width: 34px; height: 34px; border-radius: 10px; background: linear-gradient(135deg, #6366f1, #4f46e5); color: #fff; font-size: 12px; font-weight: 700; display: flex; align-items: center; justify-content: center; flex-shrink: 0; box-shadow: 0 2px 8px rgba(99,102,241,.3); }
.ai-body { flex: 1; min-width: 0; }

/* ================================================================
   REPORT STYLES
   ================================================================ */
.report-body { font-size: 15px; line-height: 1.85; color: #334155; background: #fff; padding: 20px 24px; border-radius: 14px; border: 1px solid #f0f0f5; box-shadow: 0 1px 4px rgba(0,0,0,.03); }
.report-body :deep(h1) { font-size: 1.4rem; font-weight: 700; margin: 20px 0 14px; padding-bottom: 10px; border-bottom: 2px solid #e0e7ff; color: #0f172a; }
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
.error-msg { color: #dc2626; background: linear-gradient(135deg, #fef2f2, #fff1f2); padding: 14px 18px; border-radius: 12px; font-size: 14px; border: 1px solid #fecaca; }
.clarify-msg { display: flex; gap: 14px; background: #fffbeb; border: 1px solid #fde68a; padding: 16px 18px; border-radius: 12px; font-size: 14px; color: #92400e; }
.clarify-icon { font-size: 24px; flex-shrink: 0; }
.clarify-msg strong { display: block; margin-bottom: 4px; }
.clarify-msg p { margin: 4px 0; }
.clarify-hint { font-size: 12px; color: #a16207; }

/* ================================================================
   INPUT BAR
   ================================================================ */
.input-bar { padding: 14px 24px 18px; background: #fff; border-top: 1px solid #f0f0f5; box-shadow: 0 -2px 12px rgba(0,0,0,.03); }
.input-tools { display: flex; gap: 10px; margin-bottom: 10px; align-items: center; flex-wrap: wrap; }
.tool-select { padding: 5px 10px; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 12px; background: #f8fafc; color: #475569; cursor: pointer; }
.tool-toggle { font-size: 12px; padding: 5px 12px; border: 1px solid #e2e8f0; border-radius: 6px; cursor: pointer; display: flex; align-items: center; gap: 4px; color: #94a3b8; transition: all .12s; }
.tool-toggle.active { border-color: #6366f1; color: #4f46e5; background: #eef2ff; }
.tool-toggle input { display: none; }
.running-timer {
  margin-left: auto;
  font-size: 12px;
  color: #fff;
  font-weight: 600;
  background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
  border-radius: 20px;
  padding: 5px 14px;
  display: flex;
  align-items: center;
  gap: 6px;
  box-shadow: 0 2px 10px rgba(99,102,241,.35);
  letter-spacing: .3px;
}
.timer-spinner {
  width: 12px;
  height: 12px;
  border: 2px solid rgba(255,255,255,.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin .8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.input-row { display: flex; gap: 12px; align-items: flex-end; }
.input-row textarea { flex: 1; border: 2px solid #e2e8f0; border-radius: 14px; padding: 12px 16px; resize: none; font-size: 14px; font-family: inherit; outline: none; max-height: 160px; transition: border-color .15s; }
.input-row textarea:focus { border-color: #6366f1; }
.send-btn { width: 44px; height: 44px; border-radius: 50%; border: none; background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%); color: #fff; cursor: pointer; display: flex; align-items: center; justify-content: center; flex-shrink: 0; transition: all .2s; box-shadow: 0 2px 8px rgba(99,102,241,.3); }
.send-btn:hover { transform: scale(1.08); box-shadow: 0 4px 16px rgba(99,102,241,.4); }
.send-btn:disabled { background: #e2e8f0; cursor: not-allowed; transform: none; box-shadow: none; }
.stop-btn { width: 44px; height: 44px; border-radius: 50%; border: none; background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%); color: #fff; cursor: pointer; display: flex; align-items: center; justify-content: center; flex-shrink: 0; transition: all .2s; box-shadow: 0 2px 8px rgba(239,68,68,.3); }
.stop-btn:hover { transform: scale(1.08); box-shadow: 0 4px 16px rgba(239,68,68,.4); }

/* ================================================================
   KB PANEL
   ================================================================ */
.kb-panel { width: 240px; background: #fafbff; border-left: 1px solid #f0f0f5; display: flex; flex-direction: column; flex-shrink: 0; }
.kb-header { padding: 20px 16px 12px; font-weight: 700; font-size: 13px; color: #475569; border-bottom: 1px solid #eef2ff; }
.kb-body { flex: 1; overflow-y: auto; padding: 12px 16px; }
.kb-upload-btn { display: block; padding: 10px; text-align: center; border: 2px dashed #e2e8f0; border-radius: 10px; font-size: 13px; color: #6366f1; cursor: pointer; margin-bottom: 10px; transition: all .12s; }
.kb-upload-btn:hover { border-color: #6366f1; background: #eef2ff; }
.kb-msg { font-size: 12px; color: #64748b; margin-bottom: 8px; }
.kb-files { font-size: 12px; }
.kb-file-item { display: flex; align-items: center; gap: 8px; padding: 7px 0; border-bottom: 1px solid #f0f0f5; }
.kb-file-check { accent-color: #6366f1; cursor: pointer; flex-shrink: 0; }
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
