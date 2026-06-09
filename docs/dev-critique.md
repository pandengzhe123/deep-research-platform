# 开发者批判：会导致崩溃或无法正常运行的全部漏洞

> 站在生产运维角度。不管用户体验，只问"项目会不会炸？"

---

## 一、致命级——会导致整个系统瘫痪

### 1. Python Agent 单点故障，Java 无重试

**现状**：Python Agent 崩了，Java 直接返回 500。没有重试、没有降级、没有"请稍候重试"的提示。

**触发条件**：Agent 进程 OOM、无限循环卡死、依赖库升级不兼容。

**后果**：所有研究请求全部失败。

### 2. PostgreSQL 连接失败无回退

**现状**：Spring Boot 启动时如果连不上 PG，直接启动失败。运行中断连也不恢复。

**触发条件**：Docker 里 PG 容器重启、网络抖动、连接池耗尽。

**后果**：Java 网关完全不可用——连健康检查都过不了。

### 3. docker-compose 启动顺序不可靠

**现状**：`depends_on` 只等容器启动，不等服务就绪。Java 可能在 PG 初始化完成前就连数据库——首次启动大概率报错。

**触发条件**：首次 `docker compose up`。

**后果**：Java 启动失败，整个栈不可用。

---

## 二、高风险级——特定条件下崩溃

### 4. Token 超限直接报错

**现状**：DeepSeek V4 Flash 有 1M context 窗口。Agent 多轮循环后 `messages` 列表无限增长，超过上限直接 400 报错。没有渐进截断。

**触发条件**：Level 4 连续跑 5 轮以上，每轮追加大量搜索结果。

**后果**：研究中断，用户看到 500 错误。

### 5. Python Agent OOM（内存溢出）

**现状**：搜索结果不做条数限制、报告全文都在内存、Level 3 跑 4 个并行 Agent 时峰值内存翻四倍。

**触发条件**：多个大报告同时生成、长时间运行不重启。

**后果**：进程被杀，所有进行中的研究丢失。

### 6. 会话状态卡死

**现状**：研究请求中途出错，`sessions` 表的 `status` 永远是 `running`。没有超时清理、没有自动标记失败。

**触发条件**：网络中断、Agent 崩溃、客户端关闭浏览器。

**后果**：僵尸会话越积越多，列表里一堆"运行中"的假数据。

### 7. Chroma 数据损坏

**现状**：`chroma_data/` 目录是二进制文件，没有校验、没有备份。文件系统错误或磁盘满导致数据损坏。

**触发条件**：磁盘满、非正常关机、手动删了 `chroma.sqlite3`。

**后果**：RAG 功能静默失败——搜不到任何结果但不报错。

---

## 三、中风险级——特定场景下出 bug

### 8. asyncio.gather 并发冲突

**现状**：Level 3 里多个 Level2Agent 共享同一个 LLMClient 或 SearchTool 实例。Python asyncio 单线程模型下一般不会 race，但 OpenAI SDK 内部使用 httpx 连接池，高并发时可能触发连接复用 bug。

**触发条件**：Level 3 跑 4 个并行 Agent，每个都在调 LLM。

**后果**：偶发 500 错误，复现困难。

### 9. API Key 硬编码风险

**现状**：`.env` 文件有真实 Key，理论上被 `.gitignore` 但曾有一次 commit 时误包含了 `.env`。

**触发条件**：忘记检查 `git add -A` 提交了什么。

**后果**：Key 泄露到公开仓库。

### 10. 前端 Vite proxy 只开发环境有效

**现状**：`vite.config.js` 里的 proxy 只在 `npm run dev` 开发模式下生效。生产构建（`npm run build`）后走 nginx，之前直接调 Python `:8000` 的 KB 接口全部失效。

**触发条件**：用 `npm run build` 部署生产版本。

**后果**：KB 上传功能完全不可用。

### 11. Session history JSONB 无限膨胀

**现状**：每次追加历史限制保留 50 条。但如果单条消息很长（比如塞进了网页全文），即使 50 条也会撑爆 JSONB。

**触发条件**：多轮追问，每轮追加几千字的搜索结果。

**后果**：JSONB 列超限，写数据库失败。

---

## 四、低风险级——可用的但不爽

### 12. 无优雅关闭

Ctrl+C 杀进程 → 所有进行中的研究丢失 → sessions 表残留 `running` 状态。

### 13. Java 前端和 Vue 前端并存

`java-gateway/src/main/resources/static/index.html` 还保留着旧版 HTML。访问 `localhost:8080` 会看到老界面，而 Vue 在 `:3000`。用户混乱。

### 14. 健康检查不检查数据库

`/api/health` 只看 Agent 是否通，不看数据库是否通。PG 挂了但 Agent 正常，健康检查显示 OK。

### 15. 无日志轮转

Python `print` 和 Java `log.info` 全输出到控制台。Docker 部署后全部进 stdout，没有归档、没有分级。

---

## 严重度排序

| # | 问题 | 严重度 | 后果 | 修的成本 | 修复方案 |
|---|------|--------|------|---------|---------|
| ✅ | Agent 单点故障 | 🔴→已修 | 全站不可用 | 中 | 3 次重试 3s/9s/27s，仅 5xx+连接错误重试 |
| ✅ | PG 连接失败 | 🔴→已修 | 网关不可用 | 低 | HikariCP 自动重连 + SELECT 1 验证 + 健康检查含 DB |
| ✅ | Docker 启动顺序 | 🔴→已修 | 首次部署失败 | 低 | postgres healthcheck |
| ✅ | Token 超限 | 🟠→已修 | 长研究中断 | 中 | 50 万字符截断 |
| ✅ | Agent OOM | 🟠→已修 | 进程被杀 | 中 | 单个结果 30 万截断 + 只保留 3 轮 + 压缩前 50 万截断 |
| ✅ | 会话卡死 | 🟠→已修 | 数据脏 | 低 | @Scheduled 定时清理 |
| ✅ | Chroma 数据损坏 | 🟠→已修 | RAG 静默失效 | 低 | ingest 加 try/except + health_check() |
| 8 | asyncio 并发 | 🟡 中 | 偶发 500 | 高 | |
| ✅ | API Key 泄露 | 🟡→已验证 | 安全事件 | 低 | .gitignore 已保护，.env 未被 Git 跟踪，.env.example 加警告 |
| ✅ | Vite proxy 失效 | 🟡→已验证 | KB 不可用 | 低 | 前端全用相对路径，Vite(dev)+nginx(prod) 双重覆盖 |
| 11 | JSONB 膨胀 | 🟡 中 | 数据库写失败 | 低 | |

---

## 修 P0 要几行代码

| 问题 | 修复 |
|------|------|
| Docker 启动顺序 | `docker-compose.yml` 加 `healthcheck` + `depends_on condition: service_healthy` |
| PG 连接失败 | `application.yml` 加 HikariCP 重连配置 |
| Token 超限 | 参考原项目 `remove_up_to_last_ai_message()` ~20 行 |
| 会话卡死 | `@Scheduled` 定时任务，超过 30 分钟的 running 改为 error |
| Chroma 损坏 | `try/except` 包住 `search()` 返回友好错误 |

总共 ~50 行，消除五个致命/高风险漏洞。
