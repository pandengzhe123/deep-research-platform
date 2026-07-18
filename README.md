# Deep Research Platform

> 全栈深度研究 AI Agent 平台 | 零框架手写 | Python + Java + Vue + Docker | 完整评测体系

输入问题 → Agent 自主搜索网络+知识库 → SSE 实时推送进度 → 生成带引用的深度研究报告。自建 RAGAS 四大指标+A/B 对照+LLM-as-Judge+消融实验+回归测试，数据驱动优化。

---

## 快速开始

### 1. 配置

```bash
cp agent/.env.example agent/.env
# 编辑 .env，填入 API Key：
#   DEEPSEEK_API_KEY=sk-xxx        (LLM)
#   TAVILY_API_KEY=tvly-xxx        (搜索)
#   DASHSCOPE_API_KEY=sk-xxx       (阿里云 embedding)
```

### 2. 启动

```bash
docker compose up
# 浏览器打开 http://localhost:3000
```

首次启动需下载镜像和依赖（约 10 分钟），后续启动几秒。

### 3. 命令行（不启动 Docker 也能跑）

```bash
cd agent
pip install -e .
python -m src.researcher.agent "量子计算对密码学的影响" 2
```

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
              四级 Agent · RAG 知识库 · 搜索工具 · Trace 追踪
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

## 核心特性

### Agent Runtime

- **协议底层手写**：while 循环 + OpenAI 原生 tool_calls 消息往返，零 LangChain/LangGraph
- **回调解耦**：`self.emit = on_progress or (lambda e: None)`，同一 Agent 类同时支持 SSE 流式、同步接口、命令行测试
- **全异步**：AsyncOpenAI + asyncio.gather 并行 + Queue + create_task 后台任务
- **容错三层**：LLM 重试（429/5xx 指数退避，4xx/超时不重试）→ Tavily→DDG 自动降级 → Agent 单轮异常跳过继续
- **上下文管理**：100 万字符三级保护（80%预警→LLM结构化压缩→硬截断），原始问题锚点独立字段永不丢失
- **Trace 追踪**：自研 JSONL 调用链路记录，每次 LLM 调用自动捕获 token/耗时/模型，每次搜索记录 query/结果数/去重数/耗时

### RAG 知识库

5 种检索模式，按 mode 参数切换，每层独立降级：

| 模式 | 管线 | 特点 |
|------|------|------|
| v2 | 阿里云 text-embedding-v4 纯向量 | 基线，1024 维，0.22s |
| hybrid | 向量 + BM25（jieba 分词 + RRF 融合） | 关键词补语义盲区 |
| rerank | 向量粗召回 + CrossEncoder（bge-reranker-base）精排 | 排序精度提升 |
| full | 查询改写（LLM）→ 双路混合 → CrossEncoder | 全链路最优但最慢 |
| default | MiniLM 本地 384 维 | 兼容旧数据，零费用 |

双 Embedding 管线共存（MiniLM + 阿里云），多租户 per-user Collection 物理隔离。

### 搜索流水线

Tavily 优先 → DuckDuckGo 降级 · 跨轮 URL 去重 · 5 分钟搜索缓存 · 批量 LLM 摘要（N→1 次调用） · structured_output 强制 JSON

### 记忆与持久化

PostgreSQL 会话管理：history JSONB 存对话链 + report JSONB 数组存历史报告（永不被截断）。超 40 条自动 LLM 压缩。追问时 report 去重兜底 history 截断。压缩摘要跟随报告持久化，下次追问自动补回。

---

## 评测体系

### 三层评测框架

| 层级 | 测什么 | 指标 | 调 LLM |
|------|--------|------|:--:|
| Retriever 单独测 | 检索器能否找回正确文档 | Precision@5 / Recall@5 / MRR | 否 |
| Generator 单独测 | LLM 生成质量（跳过检索） | Faithfulness / Answer Relevance / Context Relevance / Answer Correctness | 是 |
| E2E 诊断矩阵 | 真实场景端到端 | LLM-as-Judge 五维度 + 按题型定位 | 是 |

### 数据驱动优化故事

**消融实验**（112 题 × 4 模式）发现 ChromaDB 余弦距离被当成相似度算 → Recall 17% → 修复后 80% → 调优阈值后 v2 命中率 88%，MRR 0.903。

**A/B 对照实验**发现三个连锁 Bug：max_tokens 默认截断 → Judge 12000 字盲评 → Prompt 穷举爆炸。连归因都被数据推翻三轮，最终总分 +0.70 首次超出噪音。

### 评测工具

```bash
# 消融实验：对比四种检索模式
python -m src.researcher.evaluation.ablation

# LLM-as-Judge 五维度报告打分
python -m src.researcher.evaluation.run_eval --mode report

# A/B 对比（Prompt 改动前后）
python -m src.researcher.evaluation.ab_compare gen   # 生成新旧报告
python -m src.researcher.evaluation.ab_compare judge  # 打分对比

# 回归测试：改代码后快速验证无退化
python -m src.researcher.evaluation.run_regression --mode retriever  # 检索层 25s
python -m src.researcher.evaluation.run_regression --mode format     # 格式层 2min
```

---

## 代码量

| 子项目 | 语言 | 行数 |
|--------|------|:---:|
| Python Agent + RAG + 评测 + Trace | Python | ~3,500 |
| Java 网关 | Java 21 | ~1,200 |
| Vue 前端 | Vue 3 | ~900 |
| **合计** | | **~5,600** |

### 核心文件

```
agent/src/researcher/
├── agent.py           四级 Agent + 19 Prompt（~1,430 行）
├── kb.py              Chroma + 5 种检索模式 + embedding（~530 行）
├── server.py          FastAPI + SSE（~380 行）
├── search.py          Tavily + DDG + 批量摘要 + 缓存（~300 行）
├── llm.py             AsyncOpenAI + 重试（~170 行）
├── trace.py           JSONL 结构化调用链路（~200 行）
├── config.py          环境变量（~50 行）
├── retrievers/        BM25 + RRF + CrossEncoder + 查询改写（~190 行）
└── evaluation/        四指标 + Judge + A/B + 消融 + 回归（~1,500 行）

java-gateway/.../
├── ResearchController.java   SSE 透传 + 会话管理 + JWT
├── SessionService.java       会话 CRUD + 自动压缩 + 僵尸清理 + 上下文锚点
├── SecurityConfig.java       WebFlux Security + JWT Filter
└── JwtTokenProvider.java     JWT 签发/验证
```

---

## License

MIT
