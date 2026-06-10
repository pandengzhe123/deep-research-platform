# DeepResearch Platform

> Java 网关 + Python Agent 全栈深度研究智能体 | 2,100 行 | 零框架 | 从 API 底层手写 Agent

## 一句话

输入问题 → Agent 自主搜索网络 → 生成带引用的深度研究报告。

## 新用户使用指南

### 前置条件

- 安装 **Docker Desktop**（https://www.docker.com/products/docker-desktop）
- 注册以下服务的 API Key：
  - DeepSeek：https://platform.deepseek.com（LLM，便宜好用）
  - Tavily：https://app.tavily.com（搜索，免费 1000 次/月）

### 第一步：配置 API Key

将项目根目录下的 `.env.example` 复制为 `.env`，填入你的真实 Key：

```bash
DEEPSEEK_API_KEY=sk-你的Key
TAVILY_API_KEY=tvly-你的Key
```

### 第二步：一键启动

```bash
# 在项目根目录下运行（只需一条命令）
docker compose up

# 浏览器打开
http://localhost:3000
```

首次启动需要下载基础镜像和依赖（约 10-15 分钟），后续启动只需几秒。

### 第三步：开始使用

1. **注册账号** —— 打开页面后输入用户名和密码，点击注册
2. **提问** —— 输入问题，选择 Level，点击发送。普通问题用 Level 1（15-30 秒），复杂问题用 Level 2-4
3. **查看报告** —— AI 报告会自动渲染 Markdown，底部有「📋 复制」和「💾 下载 .md」按钮
4. **追问** —— 无需新开会话，直接在输入框问下一个问题即可，Agent 会上文
5. **知识库（可选）** —— 上传 PDF/TXT/MD 文件，勾选「RAG」后再提问，Agent 会同时搜索网络和你上传的文件

### Level 选择指南

| Level | 名称 | 适合场景 | 速度 |
|-------|------|---------|------|
| 1 | 极速 | 简单事实查询 | 15-30 秒 |
| 2 | 搜索反思 | 一般研究 | 1-3 分钟 |
| 3 | 多路并行 | 多向对比（如"对比 A 和 B"） | 2-5 分钟 |
| 4 | Supervisor | 复杂深度调研 | 3-10 分钟 |

### 常见问题

**Q: 费用多少？**
A: 使用 DeepSeek V4 Flash 模型，一次普通研究约 ¥0.2-2。Tavily 搜索免费额度 1000 次/月。

**Q: 怎么停止？**
A: 终端里按 Ctrl+C，或 `docker compose down`。

**Q: 数据会丢吗？**
A: 会话历史存在 PostgreSQL 里，知识库存在 Chroma 里，都在本地磁盘。`docker compose down` 不会丢数据。

**Q: 我想自己开发怎么跑？**
A: 见下方「开发者启动」。

## 架构

```
浏览器 (Vue 3 :3000)
    │  REST / SSE
    ▼
Java 网关 (Spring Boot WebFlux, :8080)
    │  HTTP
    ▼
Python Agent (FastAPI, :8000)
    │  OpenAI SDK
    ▼
DeepSeek V4 Flash + Tavily / DuckDuckGo + Chroma
    │
PostgreSQL (会话持久化)
```

### 开发者启动（本地开发）

适合需要改代码的开发者。需要安装 Python 3.11 + JDK 21 + Node.js + Maven。

```bash
# 终端 1：Python Agent
cd agent && start.bat               → :8000

# 终端 2：Java 网关  
cd java-gateway && start.bat        → :8080

# 终端 3：Vue 前端
cd frontend && start.bat            → :3000

# 浏览器打开
http://localhost:3000
```

## 四个 Agent Level

| Level | 名称 | 机制 | 特点 |
|-------|------|------|------|
| **1** | 极速搜索 | 跳过规划+跳过摘要 → 1 次 LLM 调用 | 15-30 秒出结果 |
| **2** | 搜索-反思 | while 循环：LLM 决策 → 执行工具 → 追加历史 → 再决策 | Function Calling 驱动 |
| **3** | 多路并行 | LLM 拆题 → asyncio.gather(N 个 Level 2) → 汇总 | 总时间 = 最慢的子任务 |
| **4** | Supervisor 调度 | 外层 while 循环：拆题 → 并行分派 → 评估 → 补拆 → ResearchComplete | 双层 Agent，与原项目同架构 |

## 技术亮点

- **纯 API 实现**：没有 LangChain/LangGraph，直接调 OpenAI SDK + while 循环
- **双层 Agent 循环**：Supervisor（外循环）+ Researcher（内循环），与原项目同构
- **渐进式 Level 1-4**：单次搜索 → Agent 循环 → 多路并行 → Supervisor 调度，每层可独立演示
- **错误恢复三层**：LLM 自动重试 + Tavily→DuckDuckGo 降级 + Agent 循环异常兜底
- **双搜索源**：Tavily 优先，失败自动降级 DuckDuckGo（免费备选）
- **Java 全栈网关**：Spring Boot WebFlux + 虚拟线程 + Semaphore 并发控制
- **深度搜索流水线**：搜索 → URL 去重 → 网页抓取 → LLM 摘要 → 压缩去噪 → 报告
- **多轮对话记忆**：模糊问题自动追问，context 传递上下文

## 代码量

| 子项目 | 语言 | 文件数 | 行数 |
|--------|------|--------|------|
| Python Agent | Python | 6 | 1,759 |
| Java 网关 | Java 21 | 14 | ~700 |
| Vue 前端 | Vue 3 / JS | 11 | 650 |
| 测试 | Python | 2 | 428 |
| 文档 | Markdown | 14 | ~4,500 |
| **合计** | | **62** | **~8,500** |

## 核心文件

```
agent/src/researcher/
├── llm.py         LLM 客户端（chat / Function Calling / 结构化输出）
├── search.py      搜索流水线（Tavily + 去重 + 摘要 + 快速模式）
├── agent.py       核心：Level 1-4 + 压缩 + 澄清 + 所有 Prompt
├── config.py      配置（环境变量）
└── server.py      FastAPI 服务（/research + /research/stream SSE + /health）

java-gateway/src/main/java/.../
├── AgentClient.java         Python HTTP 客户端（WebClient）
├── ResearchController.java  REST API + SSE 流式转发
├── ResearchScheduler.java   并发控制（Semaphore 20）
└── SessionService.java      会话管理
```

## 与原项目的对比

| 维度 | open_deep_research | 本项目 |
|------|-------------------|--------|
| Agent 框架 | LangGraph StateGraph | 纯 while/for + asyncio.gather |
| LLM 调用 | init_chat_model() → bind_tools | OpenAI SDK 原生 tools |
| 工具定义 | Pydantic BaseModel | 手写 JSON dict |
| 状态管理 | StateGraph 自动流转 | Python 变量 + 函数传参 |
| 代码量 | ~3,000 行 Python | 1,003 行 Python + 492 行 Java |
| 依赖 | LangChain/LangGraph 全家桶 | openai + fastapi + tavily-python |
| 多模型 | 8 种供应商 | DeepSeek（可扩展） |
| 搜索后端 | 4 种 | 2 种（Tavily + DuckDuckGo 自动降级） |
| 错误恢复 | 重试+截断 | ✅ LLM 重试 + 搜索降级 + 循环兜底 |
| Java 网关 | 无 | ✅ |
| 自有 UI | 无（依赖外部 Studio） | ✅ |
| 架构文档 | ❌ | ✅（含竞品分析、面试 Q&A、代码对照） |

详见 [docs/code-comparison.md](docs/code-comparison.md)

## 文档

| 文档 | 内容 |
|------|------|
| [docs/java-gateway-guide.md](docs/java-gateway-guide.md) | 架构设计 + 竞品分析 |
| [docs/learning-guide.md](docs/learning-guide.md) | 原项目学习笔记 |
| [docs/code-comparison.md](docs/code-comparison.md) | 与原项目代码层面对比 |
| [docs/interview-qa.md](docs/interview-qa.md) | 面试 25 问 & 标准答案 |
| [docs/file-guide.md](docs/file-guide.md) | 每个文件的作用 |
| [PROGRESS.md](PROGRESS.md) | 开发进度 |
| [docs/prompts_cn.py](docs/prompts_cn.py) | Prompt 中文对照 |

## 后续计划

- [ ] RAG 混合检索（向量数据库 + 实时搜索融合）
- [ ] Docker Compose 一键部署
- [ ] 报告导出 PDF/Word
- [ ] JWT 多用户认证

## License

MIT
