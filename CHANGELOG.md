# 工作留痕

> 每次改动记录：改了什么文件、为什么、怎么测试的。

---

## 2026-06-08 — Vue 3 前端重构

### 做了什么

- 用 Vue 3 + Vite 完全重建前端，替代旧的单文件 HTML
- 路由：`/login`（登录页）+ `/`（研究主界面）
- 左侧栏：品牌 + 会话列表 + 用户信息（点击可退出）
- 中间聊天区：空状态快速提问 → 用户紫色气泡（右边）→ AI 白色卡片（左边）→ 思考动画
- 右侧 KB 面板：上传/列表/删除知识库文件
- 底部输入栏：Level 选择 + RAG 开关 + 计时器 + 发送按钮
- 路由守卫：未登录自动跳转登录页
- axios 拦截器：自动带 JWT，401 跳登录
- 退出确认弹窗：自定义模态框
- 多轮追问 contextHistory 从数据库 + localStorage 双恢复
- 刷新页面自动恢复活跃会话

### 新增文件

| 文件 | 行数 | 作用 |
|------|------|------|
| `frontend/package.json` | 20 | 依赖：vue/vue-router/pinia/axios/marked |
| `frontend/vite.config.js` | 12 | Vite + 代理配置 |
| `frontend/index.html` | 14 | HTML 入口 |
| `frontend/src/main.js` | 9 | Vue 初始化 |
| `frontend/src/App.vue` | 11 | 根组件 |
| `frontend/src/router/index.js` | 18 | 路由 + 守卫 |
| `frontend/src/stores/auth.js` | 29 | Pinia：token/user 管理 |
| `frontend/src/utils/api.js` | 22 | axios：拦截器 + JWT |
| `frontend/src/views/LoginView.vue` | 88 | 登录/注册页 |
| `frontend/src/views/ResearchView.vue` | 473 | 主界面：聊天 + 侧栏 + KB |
| `frontend/start.bat` | 13 | 启动脚本 |

### 修复的会话问题

- 追问丢失上下文 → Agent 收到 `contextHistory` 完整对话链
- 每次追问开新会话 → `session_id` 传给后端复用
- 刷新后聊天丢失 → localStorage 缓存 + 数据库 API fallback
- 历史会话报告不显示 → Jackson ObjectMapper 修复 JSONB 序列化
- Clarify 追问不存 session → 追问后保存 session_id


## 2026-06-08 — Step 3 收尾：多租户隔离 + 会话/文件按用户过滤

### 做了什么

- KB 文件按 user_id 隔离：`list_docs` + `delete_doc` 过滤当前用户
- 会话列表按 user_id 隔离：`listSessions` → `getUserSessions(uid)`
- 解决 `ReactiveSecurityContextHolder` 不能跨线程的问题——`listSessions` 改回 `Mono` 响应式链
- `@AuthenticationPrincipal` → `ReactiveSecurityContextHolder` 直接在响应式方法里拿 Principal
- 前端会话列表改为显示问题前 15 字而非 session ID
- 登录/注册/退出后自动刷新会话列表和文件列表
- 清除了旧的 Chroma 数据（切换 embedding 时遗留的无 user_id 数据）
- SSL 绕过从函数内提前到模块加载时，`local_files_only=True` 防止上传文件时联网

### 修改文件

| 文件 | 改动 |
|------|------|
| `kb.py` | SSL 绕过提前到模块级 + local_files_only + list_docs/delete_doc 加 user_id 过滤 |
| `ResearchController.java` | listSessions 改响应式 + Principal 直接取 |
| `server.py` | ResearchRequest 加 user_id 字段 + 传给 Agent |
| `agent.py` | FastLevel1Agent + Level2Agent 加 user_id 参数 + kb.search 带 user_id |
| `ResearchModels.java` | ResearchRequest +user_id, ResearchResponse +session_id |
| `index.html` | kbUserId() + 登录/退出刷新列表 + 会话标题显示问题 |

### 当前效果

- 注册/登录后 session.user_id = 真实用户 ID
- 用户 A 看不到用户 B 的文件和会话
- 切换用户自动刷新
- 会话列表显示问题标题

### 已知限制

- Spring Security 强制认证未开启（all permitted），等前端适配完成
- KB upload 走的是直接跨域调 Python（:8000），user_id 从 localStorage 取用户名


## 2026-06-08 — Step 3 完成：JWT 用户系统

### 做了什么

- 新增 JwtTokenProvider + AuthController + UserEntity + UserRepository（4 个文件，~170 行）
- SecurityConfig 重写为 @EnableWebFluxSecurity + JWT 过滤器
- ResearchController 不再硬编码 `"anonymous"`，改为从 JWT 提取 user_id
- 前端加登录栏：注册/登录/存 token/自动恢复

### 新增文件

| 文件 | 作用 |
|------|------|
| `security/JwtTokenProvider.java` | JWT 签发/验证/解析 |
| `security/UserEntity.java` | JPA 实体映射 users 表 |
| `security/UserRepository.java` | 数据访问 |
| `controller/AuthController.java` | /api/auth/register + /api/auth/login |

### 修改文件

| 文件 | 改动 |
|------|------|
| `config/SecurityConfig.java` | 重写 |
| `controller/ResearchController.java` | `"anonymous"` → `getUserId()` |
| `index.html` | 登录栏 + authToken 管理 |

### 当前效果

- 注册/登录返回 JWT，前端存 localStorage，所有请求自动带 token
- session.user_id 从 `"anonymous"` 变成真实用户 ID
- SecurityConfig 当前全部放行（未登录兼容），JWT Filter 已就绪待开启


## 2026-06-07 — Phase 2 完成：PostgreSQL 持久化验证通过

### 做了什么

- 数据库连接验证成功，`docker exec` 查到 sessions 表已有数据
- 修了 Hibernate JSONB 类型映射 bug（`@JdbcTypeCode(SqlTypes.JSON)`）
- 修了 Spring Security + WebFlux 冲突（Servlet→Reactive 配置切换）
- 修了 ResearchRequest 构造函数参数数量不匹配
- `start.bat` 更新——启动前自动检测并启动 PostgreSQL

### 踩过的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| 编译失败 `ResearchRequest(...)` 参数不匹配 | record 加了 `kbEnabled` 字段后工厂方法没更新 | 构造函数加 `null` 默认值 |
| 启动报 `NoClassDefFoundError: jakarta/servlet/Filter` | `spring-boot-starter-security` 默认 Servlet 模式，WebFlux 没有 Servlet API | 改为 `@EnableWebFluxSecurity` + 排除 Servlet 自动配置 |
| 数据写入报 `column "history" is of type jsonb but expression is of type character varying` | Hibernate 默认把 Java String 映射为 VARCHAR | `@JdbcTypeCode(SqlTypes.JSON)` 指定映射为 JSONB |
| Docker `postgres:16-alpine` 下载不动 | Docker Hub 网络不稳定 | 换 `postgres:16` 镜像，下载成功 |

### 当前状态

- PostgreSQL 运行中（Docker `deepresearch-pg` 端口 5432）
- 会话数据持久化已验证（SELECT 查到数据）
- 前端提交问题正常生成报告
- 用户系统（Step 3）待开发

---

## 2026-06-07 — Phase 2 完成：PostgreSQL 持久化验证通过

### 做了什么

- 数据库连接验证成功，`docker exec` 查到 sessions 表已有数据
- 修了 Hibernate JSONB 类型映射 bug（`@JdbcTypeCode(SqlTypes.JSON)`）
- 修了 Spring Security + WebFlux 冲突（Servlet→Reactive 配置切换）
- 修了 ResearchRequest 构造函数参数数量不匹配
- `start.bat` 更新——启动前自动检测并启动 PostgreSQL

### 踩过的坑

| 问题 | 原因 | 解决 |
|------|------|------|
| 编译失败 `ResearchRequest(...)` 参数不匹配 | record 加了 `kbEnabled` 字段后工厂方法没更新 | 构造函数加 `null` 默认值 |
| 启动报 `NoClassDefFoundError: jakarta/servlet/Filter` | `spring-boot-starter-security` 默认 Servlet 模式，WebFlux 没有 Servlet API | 改为 `@EnableWebFluxSecurity` + 排除 Servlet 自动配置 |
| 数据写入报 `column "history" is of type jsonb but expression is of type character varying` | Hibernate 默认把 Java String 映射为 VARCHAR | `@JdbcTypeCode(SqlTypes.JSON)` 指定映射为 JSONB |
| Docker `postgres:16-alpine` 下载不动 | Docker Hub 网络不稳定 | 换 `postgres:16` 镜像，下载成功 |

### 当前状态

- PostgreSQL 运行中（Docker `deepresearch-pg` 端口 5432）
- 会话数据持久化已验证（SELECT 查到数据）
- 前端提交问题正常生成报告
- 用户系统（Step 3）待开发


## 2026-06-07 — Phase 2 启动：PostgreSQL 替换内存存储

### 做了什么

- 启动 PostgreSQL（Docker `postgres:16-alpine`，端口 5432）
- 改造 SessionService 从 `ConcurrentHashMap`（内存）→ Spring Data JPA（PostgreSQL）
- 建两张表：`users`（用户认证）+ `sessions`（会话持久化）
- 会话历史改 JSONB 列存完整对话链，报告后不清空——支持后续追问
- Agent 请求加 `context` 字段传递对话历史

### 新增文件

| 文件 | 作用 |
|------|------|
| `java-gateway/src/main/resources/schema.sql` | users + sessions 建表语句 |
| `java-gateway/.../model/SessionEntity.java` | JPA 实体，映射 sessions 表 |
| `java-gateway/.../service/SessionRepository.java` | Spring Data JPA 接口 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `java-gateway/pom.xml` | +JPA + PostgreSQL + Spring Security + jjwt（4 个依赖） |
| `java-gateway/src/main/resources/application.yml` | +数据源、JPA 配置、schema.sql 自动执行 |
| `java-gateway/.../service/SessionService.java` | 内存 → JPA 持久化，history JSONB 管理，报告后不清空 |
| `java-gateway/.../controller/ResearchController.java` | 报告生成后 `appendHistory()` |

### 验证方式

```bash
# 启动 PostgreSQL
docker run -d --name deepresearch-pg -e POSTGRES_PASSWORD=deepresearch -e POSTGRES_DB=deepresearch -p 5432:5432 postgres:16-alpine

# 启动 Java 网关 → 提交一个问题 → 报告出
# 查数据库
docker exec deepresearch-pg psql -U postgres -d deepresearch -c "SELECT id, status, length(report) FROM sessions;"
# → 看到会话记录表示持久化成功

# 再追问一次 → 新消息追加到同一 history
```

### 下一步

- 编译通过（依赖下载中）
- 数据库连接验证
- Step 3：JWT 用户系统（SecurityConfig + JwtTokenProvider + AuthController）

---

## 2026-06-07 — embedding 模型升级

### 做了什么

- 从 HashingVectorizer（哈希）升级到 sentence-transformers（语义）
- 解决 SSL 证书验证失败问题（`ssl._create_unverified_context`）
- 加模型缓存（首次 3 秒加载，之后复用）

### 修改文件

| 文件 | 改动 |
|------|------|
| `agent/src/researcher/kb.py` | `_embed_hash` → `_embed_semantic`，加 SSL bypass + 模型缓存 |

---

## 2026-06-07 — RAG 工具完善

### 做了什么

- Level 1 支持 RAG：网络搜索 + 知识库检索并行
- Level 2 RAG 开关：不勾 RAG 时不注册 `search_kb` 工具
- 前端文件列表：页面加载时显示已有文档，上传后自动刷新
- 文档删除功能：每个文件旁边有 `[删]` 按钮

### 修改文件

| 文件 | 改动 |
|------|------|
| `agent/src/researcher/agent.py` | Level 1 `kb_enabled`，Level 2 TOOLS 动态过滤 |
| `agent/src/researcher/server.py` | Level 1 传 `kb_enabled`，`/kb/files` + `/kb/delete` |
| `java-gateway/.../static/index.html` | 文件列表、删除按钮、上传自动刷新 |

---

## 2026-06-04 — 架构重构：消灭山寨版 Agent

### 做了什么

- server.py 里有重复的 Agent 逻辑（为了推送进度重写了一遍）
- 给每个 Agent 类加 `on_progress` 回调参数，内部关键步骤调 `self.emit()`
- server.py 只负责把回调事件转成 SSE 格式
- server.py 从 460 行缩到 218 行

### 修改文件

| 文件 | 改动 |
|------|------|
| `agent/src/researcher/agent.py` | Level 1/2/3/4 全部加 `on_progress` 回调 |
| `agent/src/researcher/server.py` | 删掉 242 行重复逻辑，`run_agent_with_sse` 从头重写 |

---

## 2026-06-04 — Phase 1 RAG 完成

### 做了什么

- 新增 `kb.py`：Chroma 向量库 + HashingVectorizer embedding + 三级切块
- Agent 加 `search_kb` 工具：同时搜网络 + 本地知识库
- 知识库上传/列表/删除 API
- 前端 RAG 复选框 + 文件上传按钮

### 新增文件

- `agent/src/researcher/kb.py`（~170 行）

### 修改文件

- `agent/src/researcher/agent.py`：+`search_kb` 工具 + 路由
- `agent/src/researcher/server.py`：+3 个 KB API
- `agent/src/researcher/config.py`：修复 `.env` 路径
- `java-gateway/.../static/index.html`：RAG 开关 + 上传按钮
- `java-gateway/.../model/ResearchModels.java`：+`kb_enabled`

---

## 2026-06-03 — 错误恢复 + 搜索降级

### 做了什么

- LLM 层：`_call_with_retry()` 包装器（429/5xx/网络重试）
- 搜索层：Tavily 优先 → 失败自动降级 DuckDuckGo
- Agent 循环层：Level 2 while 循环 try/except 兜底

### 修改文件

- `agent/src/researcher/llm.py`：+`_call_with_retry` + 三个方法重写
- `agent/src/researcher/search.py`：+`_safe_tavily_search` + `_ddg_search`
- `agent/src/researcher/agent.py`：Level 2 循环 try/except

---

## 2026-06-03 — 澄清 + 压缩加入 Agent

### 做了什么

- 压缩：Level 2 写报告前先压缩原始搜索结果
- 澄清：ClarifyHelper 判断模糊问题并追问
- 前端多轮追问：contextHistory 累积

### 修改文件

- `agent/src/researcher/agent.py`：+COMPRESS_PROMPT + ClarifyHelper + Level 2 压缩
- `agent/src/researcher/server.py`：澄清入口 + ResearchRequest +context

---

## 2026-06-03 — Level 1-4 全部完成

### 做了什么

- Level 1 Fast：跳过规划+摘要，1 次 LLM，15-30 秒
- Level 2：搜索-反思 Agent 循环，Function Calling 驱动
- Level 3：多路并行，asyncio.gather
- Level 4：Supervisor-Researcher 双层循环

### 新增文件

- `agent/src/researcher/agent.py`（4 级 Agent）
- `agent/src/researcher/llm.py`
- `agent/src/researcher/search.py`
- `agent/src/researcher/config.py`
- `agent/src/researcher/server.py`

---

## 2026-06-03 — 项目初始化

### 做了什么

- 分析 open_deep_research 源码
- 搭建项目骨架
- Java 网关（Spring Boot WebFlux + SSE）
- 基础 HTML 前端
- 文档体系建立

### 新增文件

- 全部项目文件（README, PROGRESS, docs/*）
