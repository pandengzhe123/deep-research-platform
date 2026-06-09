# DeepResearch Platform 开发进度

> 最后更新：2026年6月8日 | 代码 ~3,100 行 + 前端 ~650 行 + 文档 ~3,200 行 | 37 个源文件

---

## 总览

```
整体进度  ████████████████░░  85%

├── Python Agent      ██████████████████  95%
├── Java 网关          ████████████████░░  85%
├── 前端 UI            ██████████████░░░░  75%
├── RAG 集成           █████████████████░  85%
├── 测试系统           ████████████░░░░░░  60%
├── 部署              ████████████░░░░░░  60%
└── 文档              ███████████████████  95%
```

---

## Python Agent（95%｜6 文件 ~1,759 行）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| LLM 客户端 + 重试 | ✅ | DeepSeek V4 Flash，429/5xx/网络 指数退避 |
| 搜索 (Tavily + DDG) | ✅ | Tavily 优先，失败自动降级 DuckDuckGo |
| URL 去重 + 网页摘要 | ✅ | LLM 摘要 + search_fast 跳过摘要 |
| Level 1 Fast | ✅ | 1 次 LLM，15-30s，支持 RAG |
| Level 2 搜索-反思 | ✅ | Function Calling + try/except 兜底 |
| Level 3 多路并行 | ✅ | asyncio.gather，总时间 = 最慢子任务 |
| Level 4 Supervisor | ✅ | ConductResearch / ResearchComplete / think_tool |
| 压缩 + 澄清 | ✅ | 压缩去重去噪，澄清双入口（CLI+浏览器） |
| on_progress 回调 | ✅ | Level 1-4 全线贯通 |
| FastAPI SSE 流式 | ✅ | /research + /research/stream + /health |
| token 超限处理 | | 后续 |

---

## Java 网关（85%｜14 文件 ~700 行）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| Spring Boot WebFlux | ✅ | JDK 21，虚拟线程 |
| AgentClient | ✅ | WebClient 同步+流式+健康检查 |
| ResearchController | ✅ | Level 1-4 + kb_enabled + session_id 复用 |
| SessionService | ✅ | PostgreSQL + JPA + Jackson JSONB |
| ResearchScheduler | ✅ | Semaphore(20) |
| SecurityConfig | ✅ | @EnableWebFluxSecurity + JWT Filter |
| JwtTokenProvider | ✅ | 签发/验证/解析 |
| AuthController | ✅ | 注册/登录/BCrypt |
| 会话持久化 | ✅ | PostgreSQL，history JSONB，重启不丢 |
| 会话按用户隔离 | ✅ | listSessions / getUserSessions |
| 端到端验证 | ✅ | 浏览器→Java→Python→Tavily→报告→PG 全通 |
| KB 多租户隔离 | ✅ | user_id 贯穿 Java→Python→Chroma |
| Spring Security 强制认证 | ⚠️ | 开发模式放行，JWT 基础设施已就绪 |
| SSE 流式 Java 透传 | ⚠️ | Python SSE 正常，Java 缓冲待修 |

---

## 前端（75%｜7 文件 ~650 行）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| Vue 3 + Vite 项目 | ✅ | vue-router + Pinia + axios + marked |
| 登录/注册页 | ✅ | 渐变背景 + 卡片设计 |
| 研究主界面 | ✅ | 全屏聊天布局，左侧栏 + 中间消息 + 右侧 KB |
| 聊天气泡 | ✅ | 用户紫泡右对齐，AI 白卡左对齐，思考动画 |
| 计时器 | ✅ | 提交后实时显示 |
| Markdown 渲染 | ✅ | marked + 自定义样式 |
| KB 面板 | ✅ | 上传/列表/删除，按用户隔离 |
| 会话侧边栏 | ✅ | 按用户过滤，显示问题标题 |
| 多轮追问 | ✅ | contextHistory 累积 + session_id 复用 |
| 刷新恢复 | ✅ | localStorage 缓存 + API fallback |
| 退出确认弹窗 | ✅ | 自定义模态框 |
| 响应式布局 | ⚠️ | 桌面完美，移动端侧栏可隐藏 |
| 路由守卫 | ✅ | 未登录跳转登录页 |
| axios 拦截器 | ✅ | 自动带 JWT，401 跳登录 |

---

## RAG 集成（85%）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| 向量库 Chroma | ✅ | 磁盘持久化，多集合隔离 |
| Embedding 模型 | ✅ | sentence-transformers MiniLM-L12-v2，384 维 |
| 切块策略 | ✅ | 段落→句子→字符三级降级 |
| 文件类型 | ✅ | PDF (PyMuPDF) / TXT / MD |
| search_kb 工具 | ✅ | 不勾 RAG 不注册 |
| Level 1 RAG 并行 | ✅ | Tavily + KB asyncio.gather |
| KB 上传/列表/删除 | ✅ | 前端+后端完整 |
| 前端 KB 面板 | ✅ | 上传按钮 + 文件列表 + 删除 |
| 混合检索 | ✅ | Agent 自主选择 search / search_kb |
| 多租户 KB 隔离 | ✅ | user_id 贯穿全链路 |
| 会话级文档过滤 | | Phase 3 |
| pgvector 迁移 | | 后续 |

---

## 测试系统（60%｜2 文件 428 行）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| test_units.py | ✅ | 14 个用例，纯函数测试，1 秒跑完 |
| test_quality.py | ✅ | 3 题 × 2 Level，4 条正则规则检查 |
| LLM 自评 | ⚠️ | 仅供参考，不计入通过 |
| CI 集成 | | 后续 |

---

## 部署（60%）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| Python Agent 启动 | ✅ | agent/start.bat |
| Java Gateway 启动 | ✅ | start.bat（自动检测 PostgreSQL） |
| Vue 前端启动 | ✅ | frontend/start.bat (npm run dev) |
| PostgreSQL 容器 | ✅ | Docker postgres:16 |
| Dockerfile ×3 | ✅ | agent / gateway / frontend |
| nginx 反向代理 | ✅ | 前端 nginx 代理 API + KB + SSE |
| docker-compose.yml | ✅ | 4 服务编排，`docker compose up` |
| 云服务器部署 | | 后续 |

---

## 文档（95%｜12 文件 ~3,200 行）

| 文档 | 状态 | 内容 |
|------|:--:|------|
| README.md | ✅ | 项目主页，架构、对比表 |
| PROGRESS.md | ✅ | 开发进度（此文件） |
| CHANGELOG.md | ✅ | 工作留痕 |
| docs/code-comparison.md | ✅ | 与原项目对比 |
| docs/file-guide.md | ✅ | 每个文件的作用 |
| docs/interview-qa.md | ✅ | 30 问，标准答案 |
| docs/java-gateway-guide.md | ✅ | 架构设计 + 竞品分析 |
| docs/learning-guide.md | ✅ | 原项目学习笔记 |
| docs/phase2-3-plan.md | ✅ | 会话 + 用户系统计划 |
| docs/roadmap.md | ✅ | 六阶段路线图 |
| docs/frontend-roadmap.md | ✅ | 前端路线图 |
| docs/prompts_cn.py | ✅ | Prompt 中文对照 |

---

## 架构评审（6 个缺陷追踪）

| # | 问题 | 状态 |
|---|------|:--:|
| 1 | agent.py 是上帝类 (862 行) | ⚠️ 接受 |
| 2 | on_progress 回调未全线覆盖 | ✅ 已修 |
| 3 | 没有错误恢复 | ✅ 已修 |
| 4 | 搜索只有 Tavily | ✅ 已修 |
| 5 | Java SSE 透传缓冲 | ⚠️ 已知限制 |
| 6 | Clarify 未融入 Agent | ✅ 已修 |

---

## 下一步

### 高优先级（功能完整度）

1. ✅ **Docker Compose** —— 4 个服务一键启动（已完成）
2. **Spring Security 强制认证** —— 当前 JWT 基础设施已就绪，只差 `authenticated()` 一行
3. **pgvector 迁移** —— Chroma → PostgreSQL，一个 DB 管所有

### 中优先级（性能与效率）

4. **跨轮 URL 去重** —— 目前只在同轮内按 URL 去重，跨轮重复搜索同样网页会浪费 LLM 调用。维护全局 `seen_urls` 集合，新搜索前过滤已知 URL，预计减少 30% 冗余 LLM 摘要调用（~10 行）
5. **搜索结果缓存** —— 相同 search query 短时间重复调用 Tavily，用 TTLCache 缓存结果（~15 行）
6. **搜索源可配置** —— 当前 Tavily 硬编码在 `SearchTool.__init__`，改为支持配置切换 Tavily / DuckDuckGo / OpenAI（~20 行）

### 低优先级

7. **LLM 输出 Schema 校验** —— `structured_output` 只解析 JSON 不验证字段，加 Pydantic 校验（~10 行）
8. **SSE 流式透传** —— 已知限制，暂不开发
