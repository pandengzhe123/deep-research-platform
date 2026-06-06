# DeepResearch Platform 开发进度

> 最后更新：2026年6月4日

## 总览

```
整体进度  █████████████░░░░░  70%

├── Python Agent      ██████████████████  95%
├── Java 网关          ██████████░░░░░░░░  50%
├── 前端 UI            ████░░░░░░░░░░░░░░  20%
├── RAG 集成           ░░░░░░░░░░░░░░░░░░   0%
├── 部署              ██░░░░░░░░░░░░░░░░  10%
└── 文档              ███████████████████  95%
```

**代码量：~2,200 行**（Python 1,003 + Java 492 + HTML 120 + 文档 ~600）

---

## Python Agent（95%）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| 项目骨架搭建 | ✅ | `pyproject.toml`、目录结构 |
| LLM 客户端 | ✅ | DeepSeek V4 Flash，chat + 工具调用 + 结构化输出 |
| 搜索工具 | ✅ | Tavily API + 网页抓取 + LLM 摘要 |
| 搜索快速模式 | ✅ | `search_fast()` 跳过 LLM 摘要 |
| 配置管理 | ✅ | 环境变量驱动 |
| Level 1 Fast | ✅ | 极速版，全程 1 次 LLM，15-30 秒 |
| Level 2 | ✅ | 搜索-反思 5 轮循环，Function Calling 驱动 |
| Level 3 | ✅ | LLM 拆题 → asyncio.gather 并行 Level 2 → 汇总 |
| Level 4 | ✅ | Supervisor 循环调度 → ResearchComplete → 汇总 |
| FastAPI 服务 | ✅ | `/research` + `/research/stream`(SSE) + `/health` |
| **架构重构** | ✅ | server.py 从 460 行缩到 218 行，消灭重复 Agent 逻辑，on_progress 回调 |
| 任务取消 | ✅ | `DELETE /research/{id}` |
| 报告导出 | | Markdown → PDF/Word |
| 搜索结果缓存 | | 相同 query 复用 |

## Java 网关（50%）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| Spring Boot 项目初始化 | ✅ | Maven + JDK 21，仅依赖 webflux |
| AgentClient | ✅ | WebClient，同步+流式+健康检查+取消 |
| ResearchController | ✅ | 4 个接口，Level 1-4 全支持 |
| SessionService | ✅ | 内存存储，会话生命周期管理 |
| ResearchScheduler | ✅ | Semaphore(20) 全局并发控制 |
| Web UI | ✅ | 内置 HTML，Level 1-4 下拉 + 计时器 |
| 编译运行 | ✅ | mvn compile && spring-boot:run |
| 端到端验证 | ✅ | 浏览器→Java→Python→Agent→Tavily→报告全通 |
| SSE 流式转发 | ⚠️ | Python SSE 正常，Java→浏览器缓冲待修 |
| 会话持久化 | | H2/MySQL 替换内存 |
| JWT 认证 | | |
| 费用估算 | | |

## 前端 UI（20%）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| 内置 HTML 测试页 | ✅ | `index.html`，同步模式 OK |
| **计时器** | ✅ | 提交后实时显示已运行时间，挂掉立即提示 |
| Markdown 渲染 | ✅ | 基础渲染（标题+链接+粗体） |
| 多轮对话上下文记忆 | ✅ | clarify 追问自动拼接历史 |
| 流式实时进度 | ⚠️ | Python SSE 正常，Java 透传有缓冲待修 |
| 框架选型 | | Vue 3 / React（后续） |
| 报告导出按钮 | | |
| 历史记录页 | | |

## RAG 集成（0%）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| 向量数据库选型 | | Chroma / Milvus / Qdrant |
| 文档摄入流水线 | | 上传 → 分块 → embedding → 入库 |
| 混合检索 | | 实时搜索 + 向量检索 融合 |
| 中文分词优化 | | jieba / 自定义 tokenizer |

## 部署（10%）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| Docker Compose | | 待写 |
| 一键启动脚本 | | 待写 |
| 环境变量模板 | ✅ | `.env.example` |
| GitHub 仓库 | | 待 push |

## 文档（80%）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| README | ✅ | 项目主页 |
| 架构设计文档 | ✅ | `docs/java-gateway-guide.md`（含竞品分析） |
| 学习指南 | ✅ | `docs/learning-guide.md` |
| Prompt 中文对照 | ✅ | `docs/prompts_cn.py` |
| API 文档 | | FastAPI 自动生成 `/docs` |
| 进度文件 | ✅ | 你正在看的就是 |

---

## 下一步（优先级排序）

1.   **RAG 集成** —— 向量数据库 + 混合检索
2.   **SSE 流式 Java 透传修复** —— Python SSE 正常，Java→浏览器缓冲待修
3.   **Docker Compose 一键部署** —— `docker compose up` 一行启动
4.   **会话持久化** —— H2/MySQL 替换内存存储
5.   **前端 UI 升级** —— Vue 3 / React
6.   **用户认证** —— Spring Security + JWT

---

## 架构评审：已知缺陷与改进方向

> 站在竞品/面试官视角审视当前架构的 6 个问题。每个问题都是面试时可聊的"改进方向"。

| # | 问题 | 严重度 | 影响 |
|---|------|--------|------|
| 1 | **agent.py 是上帝类**：785 行塞进一个文件，四个 Level + 全部 Prompt + 压缩 + 澄清 + 工具定义 | 中 | 加新功能只在上面叠，但 2000 行的项目不值得拆 |
| 2 | **on_progress 回调是半拉子工程**：Level 1/2/3 加了回调，Level 4 没有；命令行入口不传回调走空函数；浏览器入口传了但 Java 透传没用上 | 中 | 模式方向正确，没推到全线 |
| 3 | **没有错误恢复机制**：LLM 调一次失败 → 研究挂；Tavily 超时 → 研究挂。没有重试，没有降级。原项目有三次重试 + token 超限渐近截断 | ~~高~~ ✅ 已修复 | 三层防护：LLM 重试（429/5xx/网络）、Tavily 自动重试+降级、Agent 循环 try/except |
| 4 | **搜索只有 Tavily 一条路**：Tavily 挂了或限制连接后，没法兜底。没有 DuckDuckGo 备用搜索、没有本地缓存 | ~~低~~ ✅ 已修复 | `_do_search()` —— Tavily 优先，失败自动降级 DuckDuckGo（免费、无需 API Key） |
| 5 | **Java SSE 透传是个死胡同**：Flux<String> 缓冲 → DataBuffer 透传 → writeAndFlushWith——都不对。正确的做法要么让浏览器直接调 Python（CORS 已开），要么用裸 OutputStream 写字节。当前妥协：同步接口可行，实时性放弃 | 低 | 影响演示体验，不影响核心功能 |
| 6 | **Clarify 没有融入 Agent 本体**：`ClarifyHelper` 只在 server.py 被调。命令行跑 agent.py 不会触发澄清——模糊问题直接搜，返回低质量报告 | ~~高~~ ✅ 已修复 | 在 `main()` 入口加了澄清判断，命令行和浏览器行为一致 |
