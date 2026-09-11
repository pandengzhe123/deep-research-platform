# Deep Research Platform

> 全栈深度研究 AI Agent 平台 | 零框架手写 | Python + Java + Vue + PostgreSQL/Redis + Docker | 完整评测体系

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

> 这一步只影响**能不能真的跑研究**（没有 Key，LLM/搜索会失败）。
> 容器启动本身不再依赖它 —— 缺失时 `docker compose up` 照样能起，只是研究请求会报缺 Key。

### 2. 启动

```bash
docker compose up
# 一键起 5 个服务：nginx(前端) · Java 网关 · Python Agent · PostgreSQL · Redis
# 浏览器打开 http://localhost:3000
# agent/.env 存在时由 compose 自动注入容器（env_file, required: false）
```

首次启动需下载镜像和依赖（约 10 分钟），后续启动几秒。

**不用 Docker 时**，三个 `start.bat` 可直接双击（自动切到脚本所在目录，换机器/换路径都能跑）：

| 脚本 | 起什么 | 端口 |
|------|--------|------|
| `agent/start.bat` | Python Agent | 8000 |
| `java-gateway/start.bat` | Java 网关（含编译） | 8080 |
| `frontend/start.bat` | Vue 前端（缺 node_modules 自动 `npm install`） | 3000 |

### 3. 命令行（不启动 Docker 也能跑）

```bash
cd agent
pip install -e .          # 依赖已全部声明在 pyproject.toml（含 python-multipart / pymupdf / python-docx）
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
      ┌─────────────┴─────────────┐
      ▼                           ▼
PostgreSQL（唯一权威存储）    Redis（加速层 + 并发控制）
sessions：history/report      搜索缓存 · 跨研究员 URL 去重
JSONB · token_usage           会话研究锁 · 上下文热层 · 用量限流
      │
      ▼
DeepSeek · Tavily/DuckDuckGo · Chroma
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
- **上下文管理**：100 万字符三级保护（80% 预警 → LLM 结构化压缩 → 硬截断）。压缩切点必须落在 `tool_calls` 配对边界（`_safe_window_start`，差分数组 O(n)），每轮调 LLM 前清理尾部悬空/残缺 `tool_calls`（`_drop_dangling_tail`）——违反配对协议后端直接 400；压缩摘要用 `user` 角色（对话中段的 `system` 部分后端拒绝）；原始问题锚点独立字段永不丢失
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

Tavily 优先 → DuckDuckGo 降级 · 跨轮 + 跨研究员 URL 去重（Redis Set） · 5 分钟搜索缓存（Redis String + `EX`，缓存 key 含 `max_results`/`include_raw` 维度） · 批量 LLM 摘要（N→1 次调用） · structured_output 强制 JSON

### Redis：加速层 + 并发控制（5 个落点）

每个落点都是「先有缺陷，后有 Redis」：

| 落点 | Redis 用法 | 解决的缺陷 |
|------|-----------|-----------|
| 搜索缓存 | String + `SET EX 300` | 原内存 dict：重启即丢、多进程不共享、TTL 手写清理 |
| 跨研究员 URL 去重 | Set + `SADD`（pipeline 批量） | L3/L4 并行研究员各自独立实例 → 同一 URL 重复抓取 + 重复 LLM 摘要 |
| 会话研究锁 | `SET NX EX` + Lua 释放 + 后台续期 | 同会话并发研究（多标签/超时重试/多实例）→ 报告错乱 + 双倍 token |
| 上下文冷热分层 | List + `RPUSH` + 压缩时 `DEL` | 原缺陷：每轮读 PG 整行（含全部报告全文 JSONB）只为拼 context。⚠️ **净收益目前接近 0** —— `getContextHistory()` 仍以 `findById()` 开场读整行（实体无懒加载），需改投影查询才能兑现，见 `docs/memory-system.md` 3.7 |
| 用量统计 / 限流 | `INCR` + `ZINCRBY` + pipeline `EXPIRE` | 多用户配额与防刷；超限返回 429 + `Retry-After` |

**设计要点**：

- **分场景容错**：缓存类降级放行（只变慢不变错）、锁/限流类放行 + 告警（可用性优先，Redis 故障不该让研究功能整体不可用）
- **一致性模型「镜像或不存在」**：Redis 要么是 PG 的完整镜像、要么不存在（压缩/硬截断时 `DEL` → 下次读从 PG 重建），绝不出现半同步中间态
- **锁要续期**：锁 TTL 1 小时，深研究可能更久 —— 到期后锁自动消失、并发研究趁虚而入，故每 `TTL/3` 用 Lua 校验 token 后续期（锁易主即自行退出）
- **报告全文不进 Redis**（防内存爆炸），需要时按需回 PG
- **部署解耦**：`REDIS_URL`（Python）/ `REDIS_HOST`（Java）环境变量，开发用本地容器、生产换托管 Redis 零代码改动

### 记忆与持久化

PostgreSQL 会话管理：history JSONB 存对话链 + report JSONB 数组存历史报告（永不被截断）。超 40 条自动 LLM 压缩。追问时 report 去重兜底 history 截断。压缩摘要跟随报告持久化，下次追问自动补回。**拼上下文时优先读 Redis 热层**（List，miss 自动从 PG 重建并续期 TTL），PG 仍是唯一权威存储。

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

## 工程质量

| 项 | 内容 |
|---|---|
| 代码审查 | 一次以「找 bug」为目标的三方交叉审查（Python / Java / Vue），产出 **49 项**缺陷（B1–B49）并**全部修复**：Redis 客户端状态机、URL 去重语义、冷热层一致性、越权防护、Netty 事件循环阻塞、分布式锁续期、`tool_calls` 协议不变量、错误码语义、前端缓存跨账号泄漏 |
| 单元 / 集成测试 | `test_units.py`（28 例：压缩配对、悬空 `tool_calls` 清理、KB 注入不变量）+ `test_redis_cache.py`（19 例：缓存 TTL/降级、跨实例去重、锁互斥/续期、限流/错误码）+ `test_quality.py` |
| 一致性验证 | 冷热层「先 PG 后 Redis」+ Lua 原子追加 + 压缩 `DEL` 重建；Redis 故障全链路降级 |
| 部署 | `docker compose up` 一键起 前端 / 网关 / Agent / PostgreSQL / Redis |

---

## 代码量

| 子项目 | 语言 | 行数 |
|--------|------|:---:|
| Python Agent + RAG | Python | ~3,400 |
| 评测体系（四指标 + Judge + A/B + 消融 + 回归） | Python | ~3,100 |
| 测试（单元 + Redis 集成 + 质量） | Python | ~930 |
| Java 网关 | Java 21 | ~1,400 |
| Vue 前端 | Vue 3 | ~1,100 |
| **合计** | | **~9,900** |

### 核心文件

```
agent/src/researcher/
├── agent.py           四级 Agent（L1 Fast / L2 / L3 / L4）+ 19 Prompt（~1,390 行）
├── kb.py              Chroma + 5 种检索模式 + embedding（~455 行）
├── server.py          FastAPI + SSE + 会话锁/限流/用量（~510 行）
├── search.py          Tavily + DDG + Redis 缓存 + 跨实例去重（~440 行）
├── trace.py           JSONL 结构化调用链路（~230 行）
├── llm.py             AsyncOpenAI + 重试（~155 行）
├── config.py          环境变量（~35 行）
├── retrievers/        BM25 + RRF + CrossEncoder + 查询改写（~140 行）
└── evaluation/        四指标 + Judge + A/B + 消融 + 回归（~3,060 行）

java-gateway/.../
├── ResearchController.java   SSE 透传 + 会话管理 + JWT + 虚拟线程调度
├── SessionService.java       会话 CRUD + 自动压缩 + 冷热分层 + 僵尸清理 + 上下文锚点
├── SecurityConfig.java       WebFlux Security + JWT Filter
└── JwtTokenProvider.java     JWT 签发/验证

frontend/src/
├── views/ResearchView.vue    SSE 消费 + 会话切换 + 本地缓存
├── utils/api.js              统一鉴权 + 401 清理
└── utils/session-cache.js    登录痕迹/会话缓存清理（换账号不串数据）
```

---

## License

MIT
