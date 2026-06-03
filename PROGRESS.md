# DeepResearch Platform 开发进度

> 最后更新：2026年6月3日

## 总览

```
整体进度  █████████░░░░░░░░░  45%

├── Python Agent      ██████████████░░░░  70%
├── Java 网关          ░░░░░░░░░░░░░░░░░░   0%
├── 前端 UI            ░░░░░░░░░░░░░░░░░░   0%
├── RAG 集成           ░░░░░░░░░░░░░░░░░░   0%
├── 部署              ██░░░░░░░░░░░░░░░░  10%
└── 文档              ████████████████░░  80%
```

---

## Python Agent（70%）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| 项目骨架搭建 | ✅ | `pyproject.toml`、目录结构 |
| LLM 客户端 | ✅ | DeepSeek V4 Flash，chat + 工具调用 + 结构化输出 |
| 搜索工具 | ✅ | Tavily API + 网页抓取 + LLM 摘要 |
| 配置管理 | ✅ | 环境变量驱动 |
| Level 1 Agent | ✅ | 分析→搜索→报告，已验证 |
| Level 2 Agent | ✅ | 搜索-反思 5 轮循环，SSE 已验证 |
| FastAPI 服务 | ✅ | `/research` + `/research/stream`(SSE) + `/health` |
| 任务取消 | ✅ | `DELETE /research/{id}` |
| Level 3 Agent | | 多路并行搜索 |
| Level 4 Agent | | Supervisor-Researcher 双层架构 |
| 报告导出 | | Markdown → PDF/Word |
| 搜索结果缓存 | | 相同 query 复用 |
| WebSocket 推送 | | 替代 SSE，支持双向通信 |

## Java 网关（0%）

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

## 前端 UI（0%）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| 框架选型 | | Vue 3 / React |
| 研究输入页 | | |
| 实时进度展示 | | SSE 消费 |
| 报告渲染 | | Markdown → HTML |
| 历史记录 | | |
| 报告导出按钮 | | |

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

1.   **安装 Agent 依赖并跑通第一个报告** —— `uv sync && uv run python -m researcher.server`
2.   **搭建 Java 网关 Spring Boot 项目** —— 先写一个 `/api/research` 透传接口
3.   **实现 SSE 流式转发** —— Java WebFlux → Python `/research/stream`
4.   **实现 Python Agent Level 3** —— 多路并行搜索
5.   **实现 Python Agent Level 4** —— Supervisor-Researcher
6.   **前端 UI**
7.   **RAG 集成**
8.   **Docker Compose 部署**
