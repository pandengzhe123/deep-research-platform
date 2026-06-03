# DeepResearch Platform 开发进度

> 最后更新：2026年6月3日

## 总览

```
整体进度  ██████████░░░░░░░░  50%

├── Python Agent      ████████████████░░  80%
├── Java 网关          ████████░░░░░░░░░░  40%
├── 前端 UI            ██░░░░░░░░░░░░░░░░  10%
├── RAG 集成           ░░░░░░░░░░░░░░░░░░   0%
├── 部署              ██░░░░░░░░░░░░░░░░  10%
└── 文档              ██████████████████  90%
```

---

## Python Agent（80%）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| 项目骨架搭建 | ✅ | `pyproject.toml`、目录结构 |
| LLM 客户端 | ✅ | DeepSeek V4 Flash，chat + 工具调用 + 结构化输出 |
| 搜索工具 | ✅ | Tavily API + 网页抓取 + LLM 摘要 |
| 搜索快速模式 | ✅ | `search_fast()` 跳过 LLM 摘要，直接返回 Tavily 原始结果 |
| 配置管理 | ✅ | 环境变量驱动 |
| Level 1 Agent | ✅ | 原版：分析→搜索→报告（~22次LLM调用） |
| Level 1 Fast | ✅ | **极速版**：跳过规划+跳过摘要，全程仅1次LLM调用，15-30秒出结果 |
| Level 2 Agent | ✅ | 搜索-反思 5 轮循环，SSE 已验证 |
| FastAPI 服务 | ✅ | `/research` + `/research/stream`(SSE) + `/health` + `/research/{id}` |
| Level 3 Agent | | 多路并行搜索 |
| Level 4 Agent | | Supervisor-Researcher 双层架构 |
| 报告导出 | | Markdown → PDF/Word |
| 搜索结果缓存 | | 相同 query 复用 |

## Java 网关（40%）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| Spring Boot 项目初始化 | ✅ | Maven + JDK 21，仅依赖 webflux |
| AgentClient | ✅ | WebClient 封装，同步+流式+健康检查+取消 |
| ResearchController | ✅ | 4 个接口：同步/流式/取消/会话查询 |
| SessionService | ✅ | 内存存储，会话创建→报告写入→查询 |
| ResearchScheduler | ✅ | Semaphore(20) 并发控制 |
| Web UI（内置HTML） | ✅ | `index.html`，同步模式已验证通过 |
| 编译运行 | ✅ | `mvn compile` 成功，`mvn spring-boot:run` 正常 |
| 端到端验证 | ✅ | **浏览器→Java→Python→Tavily→报告→浏览器**，12秒出结果 |
| SSE 流式转发 | | Java Flux→浏览器 SSE 解析还有 bug |
| 会话持久化 | | 当前内存，需换 H2/MySQL |
| JWT 认证 | | |
| 费用估算 | | |

| 任务 | 状态 | 说明 |
|------|:--:|------|
| Spring Boot 项目初始化 | | |
| LangGraph HTTP Client | | 封装对 Python Agent 的调用 |
| 会话管理 | | 用户 → thread_id 映射 |
| JWT 认证 | | Spring Security + JWT |
| SSE 流式转发 | | WebFlux 订阅 Python SSE → 转发前端 |
| 并发调度 | | Semaphore + 虚拟线程 |
| 限流控制 | | 全局 + 每用户 |
| 负载均衡 | | Round-Robin 多 Agent 实例 |
| 费用估算 | | 提交前预估 token 量 |

## 前端 UI（10%）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| 内置 HTML 测试页 | ✅ | `index.html`，同步模式 OK |
| SSE 流式进度展示 | ⚠️ | 解析有 bug，当前用同步模式 |
| Markdown 渲染 | ✅ | 基础渲染（标题+链接+粗体） |
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

1.   **修复 SSE 流式推送** —— Java Flux → 浏览器 EventSource 解析
2.   **实现 Python Agent Level 3** —— 多路并行搜索
3.   **实现 Python Agent Level 4** —— Supervisor-Researcher 双层架构
4.   **前端 UI 升级** —— Vue 3 / React 取代内置 HTML
5.   **RAG 集成** —— 向量数据库 + 混合检索
6.   **用户认证** —— Spring Security + JWT
7.   **Docker Compose 一键部署**
