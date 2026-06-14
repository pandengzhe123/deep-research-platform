# DeepResearch Platform 开发进度

> 最后更新：2026年6月11日 | Python ~1,800 行 + Java ~700 行 + 前端 ~650 行 + 文档 ~5,000 行

---

## 总览

```
整体进度  █████████████████░  90%

├── Python Agent      ██████████████████  95%
├── Java 网关          ████████████████░░  85%
├── 前端 UI            ████████████████░░  85%
├── RAG 集成           ██████████████████  95%
├── 测试系统           ████████████░░░░░░  60%
├── 部署              ████████████████░░  80%
└── 文档              ███████████████████  95%
```

---

## Python Agent（95%｜6 文件 ~1,800 行）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| LLM 客户端 + 重试 | ✅ | DeepSeek V4，429/5xx/网络 指数退避，超时直抛不重试 |
| 搜索 (Tavily + DDG) | ✅ | Tavily 优先，失败自动降级 DuckDuckGo（`ddgs` 包） |
| URL 去重 + 网页摘要 | ✅ | LLM 摘要 + search_fast 跳过摘要 |
| Level 1 Fast | ✅ | 1 次 LLM，15-30s，支持 RAG |
| Level 2 搜索-反思 | ✅ | Function Calling + 两层异常处理（per-tool + LLM） |
| Level 3 多路并行 | ✅ | asyncio.gather + 标题降级 + 失败过滤 + 连续编号 |
| Level 4 Supervisor | ✅ | ConductResearch / ResearchComplete / think_tool |
| 压缩 + 澄清 | ✅ | 压缩去重去噪，澄清双入口（CLI+浏览器） |
| on_progress 回调 | ✅ | Level 1-4 全线贯通，含子课题失败通知 |
| FastAPI SSE 流式 | ✅ | /research + /research/stream + /health |
| Token 超限保护 | ✅ | 消息历史 >50 万字符截断，保留原始问题 |
| OOM 保护 | ✅ | 单结果 30 万截断 + 按轮保留 3 轮 + 压缩前 50 万截断 |
| 空结果兜底 | ✅ | 全部搜索失败时返回有意义提示，不走 LLM |
| JSON 容错 | ✅ | LLM 生成未转义换行时自动修复后重试 |
| LLMClient 共享 | ✅ | Level 3/4 子 Agent 共享父级客户端，减少连接洪峰 |

---

## Java 网关（85%｜14 文件 ~700 行）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| Spring Boot WebFlux | ✅ | JDK 21，虚拟线程 |
| AgentClient | ✅ | WebClient 同步+流式+健康检查，30 分钟超时 |
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

## 前端（85%｜11 文件 ~650 行）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| Vue 3 + Vite 项目 | ✅ | vue-router + Pinia + axios + marked |
| 登录/注册页 | ✅ | 渐变背景 + 卡片设计 |
| 研究主界面 | ✅ | 全屏聊天布局，左侧栏 + 中间消息 + 右侧 KB |
| 聊天气泡 | ✅ | 用户紫泡右对齐，AI 白卡左对齐，思考动画 |
| 步骤轮播 | ✅ | 🔍搜索→📖分析→💭整理→📝撰写四状态动画 |
| 计时器 | ✅ | 提交后实时显示 |
| Markdown 渲染 | ✅ | marked + 自定义样式 |
| 报告导出 | ✅ | 📋 复制 Markdown + 💾 下载 .md |
| KB 面板 | ✅ | 上传/列表/删除，按用户隔离 |
| 会话侧边栏 | ✅ | 按用户过滤，显示问题标题 |
| 多轮追问 | ✅ | contextHistory 累积 + session_id 复用 |
| 刷新恢复 | ✅ | localStorage 缓存 + API fallback（报告穿透） |
| 退出确认弹窗 | ✅ | 自定义模态框 |
| 错误提示 | ✅ | 401/429/500/超时/网络断开 六种用户友好提示 |
| 路由守卫 | ✅ | 未登录跳转登录页 |
| axios 拦截器 | ✅ | 自动带 JWT，401 跳登录，30 分钟超时 |
| 响应式布局 | ⚠️ | 桌面完美，移动端侧栏可隐藏 |

---

## RAG 集成（95%）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| 向量库 Chroma | ✅ | 磁盘持久化，多集合隔离 |
| Embedding 模型 | ✅ | sentence-transformers MiniLM-L12-v2，384 维 |
| 切块策略 | ✅ | 段落→句子→字符三级降级 |
| 文件类型 | ✅ | PDF (PyMuPDF) / TXT / MD |
| search_kb 工具 | ✅ | 按名称过滤注册，Prompt 明确描述 |
| search_kb 结果入报告 | ✅ | KB 结果进 all_search_results → 压缩 → 报告 |
| kb.search 异步 | ✅ | asyncio.to_thread 避免阻塞事件循环 |
| Level 1 RAG 并行 | ✅ | Tavily + KB asyncio.gather |
| Level 3/4 RAG 贯通 | ✅ | kb_enabled + user_id 全链路传递 |
| KB 上传/列表/删除 | ✅ | 前端+后端完整 |
| 前端 KB 面板 | ✅ | 上传按钮 + 文件列表 + 删除 |
| 多租户 KB 隔离 | ✅ | user_id 贯穿全链路 |
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

## 部署（80%）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| Python Agent 启动 | ✅ | agent/start.bat |
| Java Gateway 启动 | ✅ | start.bat（自动检测 PostgreSQL） |
| Vue 前端启动 | ✅ | frontend/start.bat (npm run dev) |
| PostgreSQL 容器 | ✅ | Docker postgres:16 |
| Dockerfile ×3 | ✅ | agent / gateway / frontend |
| nginx 反向代理 | ✅ | 前端 nginx 代理 API + KB + SSE |
| docker-compose.yml | ✅ | 4 服务编排，`docker compose up` |
| 全链路超时对齐 | ✅ | Netty/Java/axios/Vite/nginx 统一 30 分钟 |
| 云服务器部署 | | 后续 |

---

## 文档（95%｜14 文件 ~5,000 行）

| 文档 | 状态 | 内容 |
|------|:--:|------|
| README.md | ✅ | 项目主页，架构、对比表 |
| docs/PROGRESS.md | ✅ | 开发进度（此文件） |
| docs/CHANGELOG.md | ✅ | 工作留痕（含 4 轮代码审查） |
| docs/code-comparison.md | ✅ | 与原项目对比 |
| docs/file-guide.md | ✅ | 每个文件的作用 |
| docs/interview-qa.md | ✅ | 30 问，标准答案 |
| docs/java-gateway-guide.md | ✅ | 架构设计 + 竞品分析 |
| docs/learning-guide.md | ✅ | 原项目学习笔记 |
| docs/phase2-3-plan.md | ✅ | 会话 + 用户系统计划 |
| docs/roadmap.md | ✅ | 六阶段路线图 |
| docs/frontend-roadmap.md | ✅ | 前端路线图 |
| docs/project-overview.md | ✅ | 项目全貌（面试前必读） |
| docs/dev-critique.md | ✅ | 开发者漏洞批判（15 个问题） |
| docs/ux-critique.md | ✅ | 用户痛点批判（12 个问题） |
| docs/prompts_cn.py | ✅ | Prompt 中文对照 |

---

## 代码审查（2026-06-11，4 轮，24 个问题）

| # | 问题 | 严重度 | 状态 |
|---|------|:--:|:--:|
| 1 | RAG 结果不进最终报告 | 🔴 | ✅ |
| 2 | AGENT_SYSTEM 未提及 search_kb | 🟡 | ✅ |
| 3 | tool_call_id 造假 + 异常粒度粗 | 🟡 | ✅ |
| 4 | JSON 解析孤儿 tool_call | 🟢 | ✅ |
| 5 | TOOLS[:-1] 位置依赖 | 🟢 | ✅ |
| 6 | all_search_results 按条数截断非按轮 | 🔴 | ✅ |
| 7 | kb.search 阻塞事件循环 | 🟡 | ✅ |
| 8 | 消息截断丢原始问题 | 🟡 | ✅ |
| 9 | 空结果无兜底 | 🟡 | ✅ |
| 10 | Level 3/4 不传 kb_enabled/user_id | 🔴 | ✅ |
| 11 | 子课题失败污染汇总报告 | 🟡 | ✅ |
| 12 | 子报告标题破坏 Markdown 层级 | 🟡 | ✅ |
| 13 | 失败子课题编号断层 | 🟢 | ✅ |
| 14 | DECOMPOSE_PROMPT 自相矛盾 | 🟢 | ✅ |
| 15 | Level 3/4 冗余 self.kb 导入 | 🟢 | ✅ |
| 16 | 失败子课题无前端通知 | 🟢 | ✅ |
| 17 | 全链路超时 10→30 分钟 | 🔴 | ✅ |
| 18 | 子 Agent 连接洪峰 | 🟡 | ✅ |
| 19 | JSON 未转义换行解析失败 | 🟡 | ✅ |
| 20 | DuckDuckGo 包更名 | 🟢 | ✅ |
| 21 | 前端 localStorage 阻碍报告加载 | 🟡 | ✅ |
| 22 | Level1Agent 死代码 | 🟢 | ⏳ |
| 23 | emit 信息泄露风险 | 🟢 | 接受 |
| 24 | 用户对话历史无截断 | 🟢 | 接受 |

---

## 下一步

### 高优先级

1. **Spring Security 强制认证** —— JWT 基础设施已就绪，只差 `authenticated()` 一行
2. **Level1Agent 死代码清理** —— ~50 行未调用代码

### 中优先级

3. **任务取消功能接通** —— 骨架已有（cancel Event + DELETE 端点 + Java 转发），需接线：注册 task_id、循环中检查 cancel、前端加取消按钮
4. **SSE 流式透传** —— Python 端 emit 已就绪，需 Java 透传 + 前端 EventSource 替代同步 POST
5. **跨轮 URL 去重** —— 全局 `seen_urls` 集合，减少 ~30% 冗余 LLM 摘要调用
6. **搜索结果缓存** —— TTLCache 缓存相同 query 的 Tavily 结果

### 低优先级

7. **pgvector 迁移** —— Chroma → PostgreSQL，一个 DB 管所有
8. **报告导出 PDF/Word** —— 当前已支持 Markdown 复制/下载
