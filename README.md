# Deep Research Platform

> 全栈深度研究 AI Agent 平台 | 零框架手写 | Python + Java + Vue + Docker | 完整评测体系

输入问题 → Agent 自主搜索网络+知识库 → SSE 实时推送进度 → 生成带编号引用的深度研究报告。自建完整 RAG 评测体系（4核心指标+A/B对照+LLM-as-Judge），数据驱动优化。

---

## 快速开始

### 1. 配置

```bash
cp agent/.env.example agent/.env
# 编辑 .env，填入你的 API Key：
#   DEEPSEEK_API_KEY=sk-xxx
#   TAVILY_API_KEY=tvly-xxx
```

### 2. 启动

```bash
docker compose up
# 浏览器打开 http://localhost:3000
```

首次启动需下载镜像和依赖（约 10 分钟），后续启动几秒。

### 3. 使用

注册账号 → 输入问题 → 选 Level → 发送。Level 1 适合简单查询（15-30s），Level 2-4 适合深度研究。

---

## 架构

```
浏览器 (Vue 3) → nginx (:80)
                    │
                    ▼
              Java 网关 (WebFlux :8080)
              会话管理 · JWT 认证 · SSE 透传 · PostgreSQL 持久化
                    │
                    ▼
              Python Agent (FastAPI :8000)
              四级 Agent · RAG 知识库 · 搜索工具
                    │
                    ▼
         DeepSeek · Tavily/DuckDuckGo · Chroma · PostgreSQL
```

---

## 四级 Agent

| Level | 架构 | LLM 调用次数 | 特点 |
|:---:|------|:---:|------|
| 1 | 搜索 → 报告（1 次 LLM） | 1 | 极速，适合简单事实查询 |
| 2 | ReAct 循环（while + Function Calling） | 3-10 | search / think / search_kb 三工具 |
| 3 | LLM 拆题 → asyncio.gather 并行 N 个 L2 → 汇总 | 多次 | 总时间 = 最慢子任务 |
| 4 | Supervisor 双层循环 → 分批派遣 L2 → ResearchComplete → 汇总 | 多次 | Skill 包装成 Tool |

---

## 核心工程实践

- **SSE 三层穿透**：Python yield → Java `Flux<ServerSentEvent>` → 前端 `ReadableStream`，实时推送研究进度。nginx 关 buffering，30 分钟全链路超时统一
- **Queue + create_task 模式**：Agent 后台跑（create_task），进度通过回调塞进 asyncio.Queue，主循环取事件 yield 给前端，客户端断开时 task.cancel 停止消耗 token
- **回调解耦**：`self.emit = on_progress or (lambda e: None)`，同一 Agent 类同时支持 SSE 流式、同步接口、命令行测试
- **搜索优化**：Tavily 优先 → DuckDuckGo 降级、跨轮 URL 去重、批量 LLM 摘要（5次合1次）、5 分钟搜索缓存、`structured_output` 强制 JSON
- **记忆机制**：PostgreSQL JSONB 存完整对话历史（含报告全文），超 40 条自动 LLM 压缩，100 万字符三级保护（80%预警→LLM压缩→硬截断）
- **并发控制**：Java Virtual Threads + Semaphore、L3/4 LLMClient 共享复用连接池
- **容错三层**：LLM 重试（429/5xx 指数退避，4xx 不重试）→ Tavily→DDG 自动降级 → Agent 单轮异常跳过继续

### RAG 知识库

自建 ChromaDB 向量检索，5 种检索模式 —— 阿里云 text-embedding-v4 + jieba BM25 + Cross-Encoder 精排 + RRF 混合检索 + LLM 查询改写。双 embedding 管线共存，按 mode 参数切换，每层独立降级。

### 评测体系

完整 RAGAS 四大核心指标自实现（Faithfulness / Answer Relevance / Context Relevance / Answer Correctness），三层评测框架（Retriever 单独测 → Generator 单独测 → E2E 诊断矩阵），LLM-as-Judge 5维10分制打分 + std 噪音判定，40 题黄金测试集（6 种题型中英文）。A/B 对照框架支持 git 版本切换对比，报告一次存档后 judge 无限次免费复用。

---

## 代码量

| 子项目 | 语言 | 行数 |
|--------|------|:---:|
| Python Agent + RAG + 评测 | Python | ~3,100 |
| Java 网关 | Java 21 | ~1,200 |
| Vue 前端 | Vue 3 | ~900 |
| **合计** | | **~5,200** |

### 核心文件

```
agent/src/researcher/
├── agent.py          四级 Agent + 12 Prompt（~1,100 行）
├── kb.py             Chroma + 5 种检索模式 + embedding（~500 行）
├── server.py         FastAPI + run_agent_with_sse()（~360 行）
├── search.py         Tavily + DDG + 批量摘要 + 缓存（~260 行）
├── llm.py            AsyncOpenAI + 重试（~130 行）
├── config.py         环境变量（~50 行）
├── retrievers/       BM25 + RRF + CrossEncoder + 查询改写（~190 行）
└── evaluation/       4 核心指标 + Judge + A/B 框架 + 主控（~700 行）

java-gateway/.../
├── ResearchController.java   SSE 透传 + 会话管理 + JWT
├── AgentClient.java          HTTP 调 Python + 流式 SSE
├── SessionService.java       会话 CRUD + 自动压缩 + 僵尸清理
├── SecurityConfig.java       WebFlux Security + JWT Filter
└── JwtTokenProvider.java     JWT 签发/验证
```

---

## License

MIT
