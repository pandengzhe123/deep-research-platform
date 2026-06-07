# DeepResearch Platform 开发进度

> 最后更新：2026年6月7日 | 代码 3,153 行 + 文档 3,100 行

---

## 总览

```
整体进度  ██████████████░░░░  75%

├── Python Agent      ██████████████████  95%
├── Java 网关          ██████████████░░░░  70%
├── 前端 UI            █████░░░░░░░░░░░░░  25%
├── RAG 集成           ████████████████░░  80%
├── 测试系统           ████████████░░░░░░  60%
├── 部署              █████░░░░░░░░░░░░░  25%
└── 文档              ███████████████████  95%
```

---

## Python Agent（95%｜6 文件 1,759 行）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| LLM 客户端 | ✅ | DeepSeek V4 Flash，chat / chat_with_tools / structured_output |
| LLM 重试 | ✅ | `_call_with_retry()` — 429/5xx/网络 指数退避重试，4xx 不重试 |
| 搜索工具 | ✅ | Tavily + LLM 网页摘要 + search_fast（跳过摘要） |
| DuckDuckGo 降级 | ✅ | Tavily 失败自动降级 DDG，Agent 不感知 |
| URL 去重 | ✅ | 相同 URL 只保留第一次出现 |
| 配置管理 | ✅ | `.env` 环境变量，兼容不同 CWD |
| Level 1 Fast | ✅ | 极速版，1 次 LLM，15-30 秒。支持 RAG 并行 |
| Level 2 | ✅ | 搜索-反思 Agent 循环，Function Calling 驱动，try/except 兜底 |
| Level 3 | ✅ | LLM 拆题 → asyncio.gather 并行 Level 2 → 汇总 |
| Level 4 | ✅ | Supervisor 循环 → ConductResearch / ResearchComplete / think_tool |
| 压缩研究结果 | ✅ | Level 2 写报告前先清洗搜索结果（去重去噪） |
| 澄清用户意图 | ✅ | ClarifyHelper，命令行 + 浏览器双入口 |
| FastAPI 服务 | ✅ | /research + /research/stream SSE + /health + /research/{id} |
| on_progress 回调 | ✅ | Level 1/2/3 已加，Level 4 独立实现 |
| Token 超限处理 | | 后续 |
| 报告导出 | | 后续 |

---

## Java 网关（70%｜10 文件 659 行）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| Spring Boot 初始化 | ✅ | Maven + JDK 21，WebFlux 响应式 |
| AgentClient | ✅ | WebClient，同步+流式+健康检查+取消 |
| ResearchController | ✅ | Level 1-4 + kb_enabled + 历史追加 |
| SessionService | ✅ | PostgreSQL + JPA，JSONB 存对话链，报告后不清空 |
| SessionEntity + Repository | ✅ | @JdbcTypeCode 解决 JSONB 类型映射 |
| ResearchScheduler | ✅ | Semaphore(20) 全局并发控制 |
| SecurityConfig | ✅ | @EnableWebFluxSecurity，当前开发模式全部放行 |
| Web UI | ✅ | Level 1-4 下拉 + RAG 复选框 + 文件上传 + 计时器 + 追问 |
| 编译运行 | ✅ | mvn compile && spring-boot:run |
| 端到端验证 | ✅ | 浏览器→Java→Python→Agent→Tavily→报告→PostgreSQL 全通 |
| PostgreSQL 持久化 | ✅ | Docker postgres:16，sessions 表已验证有数据 |
| start.bat | ✅ | 自动检测/启动 PG → 编译 → 启动网关 |
| SSE 流式转发 | ⚠️ | Python SSE 正常，Java→浏览器缓冲待修（已知限制） |
| JWT 用户认证 | 🚧 | Step 3 待开发 |
| 费用估算 | | 后续 |

---

## 前端 UI（25%｜1 文件 179 行）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| 问题输入 + Level 选择 | ✅ | 下拉切换 Level 1-4 |
| RAG 复选框 | ✅ | 勾选后 Agent 可用 search_kb 工具 |
| 文件上传 | ✅ | 支持 PDF/TXT/MD，调 Python /kb/upload |
| 文件列表 | ✅ | 页面上传后自动刷新，支持删除 |
| 计时器 | ✅ | 提交后实时计时，知道没卡死 |
| 多轮追问上下文 | ✅ | contextHistory 累积 |
| 报告渲染 | ✅ | Markdown 转 HTML（标题+链接+粗体） |
| 登录/注册页 | 🚧 | Step 3 要做 |
| 会话列表 | 🚧 | Step 3 要做 |
| Vue/React 框架 | | 后续 |

---

## RAG 集成（80%｜1 文件 237 行 + 配置）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| 向量库 | ✅ | Chroma，PersistentClient 磁盘持久化 |
| Embedding 模型 | ✅ | sentence-transformers（MiniLM-L12-v2），384 维，中英文 |
| SSL 证书修复 | ✅ | `ssl._create_unverified_context` 解决企业网络下载问题 |
| 模型缓存 | ✅ | 首次加载 3s，之后复用内存实例 |
| 切块策略 | ✅ | 段落→句子→字符三级降级，chunk_size=500 + overlap=100 |
| HashingVectorizer 踩坑 | ✅ | 维度不一致导致 Chroma 报错，最终换语义模型 |
| 文件类型 | ✅ | PDF（PyMuPDF）+ TXT + MD |
| search_kb 工具 | ✅ | Agent TOOLS 动态注册，不勾 RAG 不注册 |
| Level 1 RAG 并行 | ✅ | Tavily + KB 同时搜，asyncio.gather |
| KB Upload API | ✅ | POST /kb/upload（支持原始文件名） |
| KB 文件列表 | ✅ | GET /kb/files |
| KB 删除 | ✅ | DELETE /kb/files/{id} |
| 前端文件管理 | ✅ | 上传按钮 + 文件列表 + 删除 |
| 混合检索 | ✅ | Agent 同时调 search + search_kb |
| 数据持久化 | ✅ | chroma_data/ 目录，重启不丢 |
| check_kb.py | ✅ | 开发者查看知识库内容的脚本 |
| 会话级文档过滤 | | Phase 3 做 |
| pgvector 迁移 | | 后续 |

---

## 测试系统（60%｜2 文件 428 行）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| 单元测试 | ✅ | test_units.py，14 个用例，1 秒跑完 |
| 回归测试 | ✅ | test_quality.py，3 题 × 2 Level，4 条规则检查 |
| 规则检查 | ✅ | 内容/标题/引用/语言，纯正则，0 LLM 依赖 |
| LLM 自评 | ⚠️ | 仅供参考，不计入通过（自己打分有偏见） |
| CI 集成 | | 后续 |

---

## 部署（25%）

| 任务 | 状态 | 说明 |
|------|:--:|------|
| Python Agent 启动脚本 | ✅ | agent/start.bat |
| Java Gateway 启动脚本 | ✅ | java-gateway/start.bat（自动检测 PG） |
| PostgreSQL 容器 | ✅ | Docker postgres:16，start.bat 自动拉起 |
| Docker Compose | | 后续 |
| Nginx 反向代理 | | 后续 |
| 云服务器部署 | | 后续 |

---

## 文档（95%｜11 文件 ~3,100 行）

| 文档 | 行数 | 状态 | 内容 |
|------|------|:--:|------|
| README.md | 121 | ✅ | 项目主页，架构、亮点、对比表 |
| PROGRESS.md | 140 | ✅ | 开发进度（此文件） |
| CHANGELOG.md | 222 | ✅ | 工作留痕，每次改了什么 |
| docs/code-comparison.md | 204 | ✅ | 与原项目代码层面对比 |
| docs/file-guide.md | 245 | ✅ | 每个文件的作用 |
| docs/interview-qa.md | 545 | ✅ | 30 问，十类，附标准答案 |
| docs/java-gateway-guide.md | 484 | ✅ | 架构设计 + 竞品分析 |
| docs/learning-guide.md | 542 | ✅ | 原项目学习笔记 |
| docs/phase2-3-plan.md | 256 | ✅ | 会话持久化 + 用户系统开发计划 |
| docs/roadmap.md | 333 | ✅ | 六阶段完整路线图 |
| docs/prompts_cn.py | — | ✅ | Prompt 中文对照 |
| agent/pyproject.toml | — | ✅ | Python 依赖配置 |
| java-gateway/pom.xml | 77 | ✅ | Maven 依赖配置 |

---

## 架构评审（已知缺陷追踪）

| # | 问题 | 状态 |
|---|------|:--:|
| 1 | agent.py 是上帝类（862 行） | ⚠️ 接受——2,500 行项目不值得拆 |
| 2 | on_progress 回调未全线覆盖 | ✅ 已修复——Level 1/2/3/4 全部贯通 |
| 3 | 没有错误恢复 | ✅ 已修复——LLM 重试 + Tavily→DDG + Agent 循环兜底 |
| 4 | 搜索只有 Tavily | ✅ 已修复——DuckDuckGo 自动降级 |
| 5 | Java SSE 透传 | ⚠️ 已知限制——暂不开发 |
| 6 | Clarify 未融入 Agent | ✅ 已修复——命令行+浏览器双入口 |

---

## 下一步

1. **Step 3: JWT 用户系统** —— JwtTokenProvider + AuthController + 前端登录
2. **Docker Compose** —— 一键启动所有服务
3. **SSE 流式** —— 已知限制，暂不开发
4. **前端框架升级** —— Vue/React
