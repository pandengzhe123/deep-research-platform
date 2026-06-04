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

1.   **修 SSE 流式 + 前端优化** —— 流式体验是演示时的加分项
2.   **RAG 集成** —— 向量数据库 + 混合检索，简历差异化卖点
3.   **会话持久化** —— H2/MySQL 替换内存存储
4.   **前端 UI 升级** —— Vue 3 / React
5.   **用户认证** —— Spring Security + JWT
6.   **Docker Compose 一键部署**
