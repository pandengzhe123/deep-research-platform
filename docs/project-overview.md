# DeepResearch Platform 项目总览

> 约 3,800 行代码、46 个源文件、三个语言、四个服务。完整的 Deep Research Agent 平台。

---

## 一、这是什么

一个全栈 AI 深度研究平台。输入问题 → Agent 自动搜索网络 → 生成带引用的深度研究报告。支持单轮、多轮并行、Supervisor 调度四种研究模式，有用户系统、会话持久化、RAG 知识库。

**一句话**：从 open_deep_research 源码学透之后，用纯 Python（不加 LangChain）+ Java 网关 + Vue 3 前端从头写的。

---

## 二、架构全景

```
浏览器 (Vue 3, :3000)
    │  HTTP / SSE
    ▼
Java 网关 (Spring Boot WebFlux, :8080)
    │  用户认证、会话管理、请求调度、SSE 转发
    │  HTTP
    ▼
Python Agent (FastAPI, :8000)
    │  Agent 循环、搜索、压缩、报告
    │  OpenAI SDK + Tavily/DuckDuckGo
    ▼
DeepSeek V4 Flash（LLM）+ Tavily/DuckDuckGo（搜索）+ Chroma（RAG）
    │
PostgreSQL（用户、会话、对话历史）
```

**四个服务**：前端(:3000) + 网关(:8080) + Agent(:8000) + 数据库(:5432)

**三种语言**：Python（AI 推理）、Java（工程网关）、JavaScript/Vue（前端 UI）

---

## 三、核心链路：一次研究请求的完整旅程

```
① 用户在浏览器输入 "量子计算是什么"，点发送
    │
② Vue → POST /api/research {question, level, kb_enabled, session_id, context}
    │   axios 拦截器自动带 Authorization: Bearer <JWT>
    │
③ Java 网关 ResearchController
    │  - JWT Filter 解析 user_id
    │  - 创建/复用会话 → PostgreSQL sessions 表
    │  - ResearchScheduler.acquire() 拿并发位
    │  - AgentClient.research() → WebClient POST Python
    │
④ Python Agent run_agent_with_sse()
    │  - ClarifyHelper 判断是否需要追问
    │  - 创建 Level1/2/3/4 Agent 实例，注册 on_progress 回调
    │  - Agent 内部: while 循环 + Function Calling + asyncio.gather
    │  - 搜索: Tavily → 失败降级 DuckDuckGo
    │  - 分析、反思、压缩、写报告
    │  - self.emit({"step":"searching",...}) → 回调 → SSE 事件
    │
⑤ 报告返回
    │  - Java: appendReport + appendHistory → PostgreSQL
    │  - Vue: 左侧 AI 白色气泡渲染 Markdown
    │
⑥ 用户追问
    │  - contextHistory 累积之前的对话
    │  - 同一 session_id → 复用会话 → Agent 看到完整上下文
```

---

## 四、四个 Agent Level

| Level | 名称 | 流程 | LLM 调用数 | 适合 |
|-------|------|------|-----------|------|
| **1** | 极速 | 搜索 → 1 次 LLM 写报告 | 1 | 简单事实查询 |
| **2** | 搜索-反思 | while 循环：LLM 决策 → 搜索 → 反思 → 再搜索… | 5-15 | 一般研究 |
| **3** | 多路并行 | LLM 拆题 → N 个 Level 2 同时跑 → 汇总 | 20-60 | 多向对比 |
| **4** | Supervisor | 外层循环：拆题 → 并行分派 → 评估 → 补拆 | 30-100 | 复杂深度调研 |

Level 1-4 是渐进演进的——Level 2 是 Agent 循环的基础，Level 3 加上 `asyncio.gather` 并行，Level 4 在外层再加一个 while 循环。

---

## 五、文件地图

### Python Agent（`agent/src/researcher/`）

| 文件 | 行数 | 做什么 |
|------|------|--------|
| `llm.py` | 125 | LLM 客户端——`chat()` / `chat_with_tools()` / `structured_output()`，含重试 |
| `search.py` | 209 | 搜索工具——Tavily + DuckDuckGo 降级 + URL 去重 + LLM 摘要 |
| `agent.py` | 862 | **核心**——Level 1-4 + 全部 Prompt + 压缩 + 澄清 + TOOLS 定义 |
| `kb.py` | 237 | 知识库——Chroma + sentence-transformers + 切块 + 检索 |
| `config.py` | 37 | 配置——读 `.env` |
| `server.py` | 282 | FastAPI——HTTP 接口 + SSE 流式 + KB 上传 |

### Java 网关（`java-gateway/.../`）

| 文件 | 行数 | 做什么 |
|------|------|--------|
| `AgentClient.java` | 99 | WebClient 调 Python |
| `ResearchController.java` | 145 | REST API + 会话管理 |
| `SessionService.java` | 148 | PostgreSQL JPA 持久化 |
| `ResearchScheduler.java` | 51 | Semaphore(20) 并发控制 |
| `SecurityConfig.java` | 35 | WebFlux Security + JWT Filter |
| `JwtTokenProvider.java` | 60 | JWT 签发/验证 |
| `AuthController.java` | 68 | 注册/登录 |

### Vue 前端（`frontend/src/`）

| 文件 | 行数 | 做什么 |
|------|------|--------|
| `views/LoginView.vue` | 88 | 登录/注册页 |
| `views/ResearchView.vue` | 473 | 主界面——聊天 + 侧栏 + KB |
| `stores/auth.js` | 29 | Pinia token 管理 |
| `router/index.js` | 18 | 路由 + 守卫 |
| `utils/api.js` | 22 | axios 拦截器 + JWT |

---

## 六、关键设计决策

| 决策 | 为什么 |
|------|--------|
| **不用 LangChain/LangGraph** | 为了理解底层——Function Calling 本质就是 while + tool_calls |
| **Java + Python 分层** | AI 推理和工程网关解耦，各用各的优势 |
| **Level 1-4 渐进** | 不是一步到位，每层可独立演示——面试时有清晰的演进故事 |
| **PostgreSQL 做会话** | 比 Redis 更持久、比 H2 更生产 |
| **Jackson ObjectMapper** 做 JSONB | 不用手写解析——手写版曾导致历史丢失 |
| **localStorage 做聊天缓存** | 刷新页面的秒级恢复，不需要每次调数据库 |
| **Docker Compose 部署** | 一行命令启动所有服务——项目交付感 |

---

## 七、当前状态（2026-06-08）

```
整体进度: 85%

Python Agent: 95% — Level 1-4 + 错误恢复 + 双搜索 + RAG
Java 网关:   85% — JWT + 会话持久化 + 并发控制
前端:        75% — Vue 3 聊天界面 + 会话管理 + KB 面板
RAG:         85% — Chroma + embedding + 多租户隔离
测试:        60% — 单元 14 例 + 回归 6 例
部署:        60% — Docker Compose 4 服务编排
文档:        95% — 12 个文件 ~3,200 行
```

**已知局限**：
- Spring Security 强制认证未开启（JWT 基础设施已就绪）
- SSE 流式在 Java 侧有缓冲（Python 正常）
- `agent.py` 862 行偏大（暂不拆分）
- 端到端测试靠手工跑

---

## 八、性能与成本

| 指标 | 数值 |
|------|------|
| Level 1 速度 | 15-30 秒 |
| Level 2 速度 | 1-3 分钟 |
| Level 4 速度 | 3-10 分钟 |
| 单次研究费用 | ¥0.2-2（DeepSeek V4 Flash） |
| 并发上限 | 20（Java 网关 Semaphore） |
| Agent LLM 调用数 | Level 2: 5-15 次，Level 4: 30-100 次 |

---

## 九、与其他方案的对比

| | ChatGPT | Perplexity | open_deep_research | 本项目 |
|---|---|---|---|---|
| Agent 自主搜索 | ❌ 用户驱动 | 部分 | ✅ | ✅ |
| 多路并行 | ❌ | ❌ | ✅ | ✅ |
| 引用追踪 | 部分 | ✅ | ✅ | ✅ |
| 本地知识库 | ❌ | ❌ | ❌ | ✅ |
| 多轮追问 | ✅ | 部分 | ✅ | ✅ |
| 用户系统 | ✅ | ✅ | ❌ | ✅ |
| 自研架构 | — | — | LangGraph | 纯 API |
| 部署难度 | — | — | 高 | docker compose up |
