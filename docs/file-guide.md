# 项目文件说明书

> 每一个文件是干嘛的，彼此怎么配合的。

---

## 一、项目全景

```
deep_research/
├── .gitignore                  Git 忽略规则
├── README.md                   项目介绍（GitHub 首页）
├── PROGRESS.md                 开发进度（做完一项勾一项）
│
├── agent/                     ★ 你自己写的 Python Agent
│   ├── .env                    你的 API Key（不提交到 Git）
│   ├── .env.example            环境变量模板（告诉别人要填什么）
│   ├── pyproject.toml          Python 项目配置（依赖、版本）
│   ├── start.bat               双击启动 Agent 服务
│   └── src/researcher/
│       ├── __init__.py          包标记文件
│       ├── config.py            配置（从 .env 读变量）
│       ├── llm.py               LLM 客户端（DeepSeek / OpenAI）
│       ├── search.py            搜索工具（Tavily + 网页抓取 + 摘要）
│       ├── agent.py             核心 Agent（Level 1 + Level 2）
│       └── server.py            FastAPI 服务（HTTP 接口 + SSE 流）
│
├── java-gateway/              待写：Spring Boot 网关
├── frontend/                  待写：Web 前端 UI
│
├── docs/                      文档区
│   ├── java-gateway-guide.md   架构设计文档（含竞品分析）
│   ├── learning-guide.md       源码学习指南
│   ├── prompts_cn.py           Prompt 中文翻译对照
│   └── file-guide.md           你在看的就是这个
│
└── scripts/                   从原项目搬来的脚本
    ├── start.bat               启动原项目
    └── export.bat              导出原项目报告
```

---

## 二、agent/src/researcher/ 逐个说明

### 2.1 `config.py` — 配置中心

**作用**：一次性从 `.env` 文件读取所有配置，变成一个 Python 对象。其他文件 `from .config import config` 就能拿到配置。

```python
config.llm_model          # → "deepseek-v4-flash"
config.tavily_api_key     # → "tvly-xxx..."
config.max_search_rounds  # → 5
```

**为什么单独一个文件**：避免 API Key 硬编码在代码里，也避免每个文件各自去读环境变量。改配置改一个地方就行。

**对应 .env 文件**：

```
LLM_MODEL=deepseek-v4-flash
LLM_PROVIDER=deepseek
TAVILY_API_KEY=tvly-xxx
DEEPSEEK_API_KEY=sk-xxx
```

---

### 2.2 `llm.py` — LLM 调用封装

**作用**：封装所有对 DeepSeek（或 OpenAI）的 API 调用，提供三个方法：

| 方法 | 干什么 | 被谁调用 |
|------|--------|---------|
| `chat()` | 发 system+user 消息，拿回文本 | `agent.py` 生成报告 |
| `chat_with_tools()` | 发多轮对话 + 工具定义，拿回工具调用请求 | `agent.py` Level 2 循环 |
| `structured_output()` | 强制 LLM 返回 JSON | `agent.py` 规划搜索词、`search.py` 摘要网页 |

**关键细节**：每个方法都会自动注入 `extra_body={"thinking": {"type": "disabled"}}`——这就是解决 DeepSeek V4 thinking 模式报错的代码。

---

### 2.3 `search.py` — 搜索工具

**作用**：把"搜索"这件事封装成一个完整流水线：

```
用户输入查询词（如 "量子计算 最新进展"）
  │
  ▼
① tavily.search()          ← 调用 Tavily API，拿到搜索结果列表（含 URL、标题、摘要）
  │
  ▼
② 按 URL 去重              ← 同一个网页只处理一次
  │
  ▼
③ 对每个网页抓取内容       ← httpx 下载 HTML → BeautifulSoup 清洗 → markdownify 转文本
  │
  ▼
④ LLM 摘要                ← 调用 llm.structured_output() 把网页内容压成 300 字摘要
  │
  ▼
⑤ 格式化返回              ← 拼成 "来源1: 标题\nURL: ...\n摘要: ..." 的文本
```

**被谁调用**：`agent.py` 里的 `Level1Agent` 和 `Level2Agent`。

---

### 2.4 `agent.py` — 核心 Agent 逻辑

**作用**：整个项目的心脏。包含两个 Agent 实现：

**Level 1 Agent** — 简单三步走：

```
分析问题 → 一次并行搜索 → 生成报告
```

1. 调 `llm.structured_output()` 分析问题，规划 2-4 个搜索词
2. 调 `search.search()` 执行搜索
3. 调 `llm.chat()` 写报告

**Level 2 Agent** — Agent 循环：

```
搜索 → 反思 → 再搜索 → 再反思 → ... → 够了 → 写报告
```

1. 调 `llm.chat_with_tools()` 让 LLM 决定"该搜什么"
2. 如果 LLM 返回 `search` 调用 → 执行搜索
3. 如果 LLM 返回 `think` 调用 → 记录反思
4. 把结果追加到消息历史，回到步骤 1
5. 直到 LLM 不再调用工具（觉得够了）或达到 5 轮上限
6. 汇总所有搜索结果，调 `llm.chat()` 写报告

**Prompt 模板**（定义在文件顶部）：

| 变量 | 内容 |
|------|------|
| `PLAN_PROMPT` | 让 LLM 分析问题并规划搜索词 |
| `AGENT_SYSTEM` | Level 2 的 System Prompt——"你是研究助手，有搜索和反思两个工具..." |
| `REPORT_PROMPT` | 让 LLM 根据搜索结果写报告 |
| `TOOLS` | 工具的函数定义（`search` + `think`） |

---

### 2.5 `server.py` — FastAPI 服务

**作用**：把 Agent 暴露为 HTTP 接口，让 Java 网关能调用。

**提供的 API**：

| 方法 | 路径 | 底层调用 | 用途 |
|------|------|---------|------|
| GET | `/health` | 无 | Java 网关探测 Agent 是否活着 |
| POST | `/research` | `run_agent_with_sse()` | 同步研究（等完了返回完整结果） |
| POST | `/research/stream` | `run_agent_with_sse()` | **SSE 流式**——实时推送进度 |
| DELETE | `/research/{id}` | 设 cancel flag | 取消正在跑的任务 |
| GET | `/research/active` | 查字典 | 列出当前运行中的任务 |

**核心函数 `run_agent_with_sse()`**：

这个函数把 `agent.py` 里的 Level 2 逻辑重新实现了一遍（因为要在每个关键节点插入 `yield` 来推送事件）。每完成一个步骤就 `yield` 一条 SSE 事件：

```
yield {"event": "status", "data": {"step": "searching", ...}}
yield {"event": "done",   "data": {"report": "..."}}
```

**和 agent.py 的关系**：

```
server.py           agent.py
─────────          ─────────
处理 HTTP 请求  →   调用 Agent 逻辑  →  调用 LLM / 搜索
推送 SSE 进度   ←   返回中间状态    ←  返回结果
```

**为什么不用 agent.py 的类**：为了在 agent.py 的每个步骤之间插入 SSE 推送。原 agent.py 是紧凑的 async 函数，不方便中途插入推送点。

---

## 三、调用链路图

当你打开 `http://localhost:8000/docs` 测试 `/research` 接口时：

```
浏览器 POST /research {"question": "量子计算进展", "level": 2}
  │
  ▼
server.py: research_sync()
  │  创建 cancel event
  │  调 run_agent_with_sse()
  ▼
server.py: run_agent_with_sse()
  │
  ├─[planning]  llm.structured_output(PLAN_PROMPT)   → 搜索计划
  │             yield {"step": "planned", ...}
  │
  ├─[loop]      llm.chat_with_tools(messages, TOOLS) → LLM 决策
  │             │
  │             ├─ tool_calls → search.search(queries)  → Tavily + 摘要
  │             ├─ tool_calls → think(reflection)       → 反思
  │             └─ 没有 tool_calls → 退出循环
  │             yield {"step": "searching", ...} / {"step": "thinking", ...}
  │
  ├─[reporting] llm.chat(REPORT_PROMPT)              → 最终报告
  │             yield {"step": "reporting", ...}
  │
  └─[done]      yield {"event": "done", "data": {"report": "..."}}
```

---

## 四、依赖关系

```
谁依赖谁：

  server.py ──→ agent.py ──→ llm.py
     │              │           │
     │              └──→ search.py ──→ llm.py（摘要时用）
     │                     │
     │                     └──→ config.py（读 Tavily key）
     │
     └──→ config.py（读全局配置）
```

`config.py` 是唯一不依赖其他模块的文件——它是底层，所有人依赖它。

---

## 五、快速定位

| 你想... | 改哪个文件 | 改哪里 |
|---------|-----------|--------|
| 换模型 | `config.py` 或 `.env` | `LLM_MODEL` |
| 改 Agent 行为 | `agent.py` | `AGENT_SYSTEM` prompt |
| 改搜索策略（搜几次） | `agent.py` | `max_rounds` 或 prompt 里的限制 |
| 加新工具（如 RAG） | `agent.py` | `TOOLS` 列表 + Level 2 循环的工具处理 |
| 改报告格式 | `agent.py` | `REPORT_PROMPT` |
| 加新 API 接口 | `server.py` | 加新路由函数 |
| 改 LLM 温度/参数 | `llm.py` | `chat()` 的 `temperature` 参数 |
| 换搜索引擎 | `search.py` | `SearchTool.__init__` |
