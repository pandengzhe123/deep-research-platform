# Phase 2+3 开发计划：会话持久化 + 用户系统

> 两个阶段一起做才完整，~220 行，~2 小时。

---

## 技术栈

| 组件 | 选型 | 为什么 |
|------|------|--------|
| 主数据库 | **PostgreSQL** | 用户、会话、对话历史全在一个实例。加 pgvector 插件后 RAG 向量也迁移过来——一个 DB 管所有 |
| JSONB | PostgreSQL 内置 | 对话历史用 JSONB 列存储，比 Redis List 灵活——能按时间、用户、话题查询 |
| pgvector（暂不加） | PostgreSQL 扩展 | 后续从 Chroma 迁过来，当前先用 Chroma |
| 密码加密 | **BCrypt** | Spring Security 内置，不可逆哈希 |
| 认证协议 | **JWT**（jjwt 库） | 无状态令牌，浏览器存 localStorage |
| 安全框架 | **Spring Security** | 声明式配置哪些接口要认证 |
| Redis（可选） | 仅并发控制和缓存 | 把现在的 `ResearchScheduler` 从 Java 内存 Semaphore 换成 Redis——分布式场景需要。单机开发阶段可有可无 |
| Maven 依赖 | `spring-boot-starter-data-jpa` + `postgresql` + `spring-boot-starter-security` + `jjwt` | 4 个 |

### 为什么是 PostgreSQL 不是 Redis

| 生产需求 | Redis 能吗 | PostgreSQL 能吗 |
|---------|-----------|----------------|
| 会话不丢、重启不灭 | ⚠️ 内存型，宕机丢数据 | ✅ 磁盘持久化 |
| 按时间查询历史会话 | ❌ List 不支持条件查询 | ✅ `WHERE created_at > ...` |
| 报告全文搜索 | ❌ | ✅ `ILIKE '%量子计算%'` |
| 备份恢复 | ⚠️ RDB/AOF 不够直观 | ✅ pg_dump / pg_restore |
| 多用户并发写同一会话 | ⚠️ List 不是事务型的 | ✅ ACID 事务 |
| 将来加 pgvector | ❌ | ✅ 一个扩展就搞定 |

**Redis 存会话是快速原型的选择。生产级持久化必须走数据库。** PostgreSQL + JSONB 是最短路径——不需要 Redis + H2 + Chroma 三件套。

---

## 整体改造路径

```
现在                                完成后
────                                ────

所有用户混为 "anonymous"            ↓ 每个用户独立登录
会话存内存，重启全丢                ↓ 存 PostgreSQL 磁盘，重启不丢
报告结束上下文清空                  ↓ 对话历史持久化，追问不断
知识库多用户混用                    ↓ user_id 隔离，各看各的
```

分三步落地，每一步都是完整可运行的状态。

---

## 第一步：PostgreSQL 配置 + 建表（15 分钟）

**做什么**：起 PG 实例，建两张表。

**启动 PostgreSQL**：

```bash
docker run -d --name postgres \
  -e POSTGRES_PASSWORD=deepresearch \
  -e POSTGRES_DB=deepresearch \
  -p 5432:5432 \
  postgres:16-alpine
```

**建表（`resources/schema.sql`，Spring Boot 启动时自动执行）**：

```sql
-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(64) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 会话表
CREATE TABLE IF NOT EXISTS sessions (
    id VARCHAR(8) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL DEFAULT 'anonymous',
    question TEXT,
    report TEXT,
    history JSONB DEFAULT '[]',         -- JSON 数组，存完整对话链
    search_mode VARCHAR(20) DEFAULT 'hybrid',
    rag_docs JSONB DEFAULT '[]',        -- JSON 数组: ["report.pdf"]
    status VARCHAR(20) DEFAULT 'running',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, created_at DESC);
```

**为什么 `history` 用 JSONB 而不是外键表**：

```
一个会话的对话链:
  ["用户: 研究AI安全", "Agent: 追问...", "用户: 技术层面", "Agent: （报告）..."]

三个理由:
1. 永远整存整取——不需要 "查某一轮对话"，不需要外键
2. JSONB 支持索引和查询——WHERE history @> '[{"role":"user"}]' 也能走索引
3. 应用层只做 JSON.parse → append → JSON.stringify，简单
```

**加依赖**：`pom.xml` 加 `spring-boot-starter-data-jpa` + `postgresql`。

**改配置**：`application.yml` 加数据源。

**验证**：启动网关 → `docker exec postgres psql -U postgres -d deepresearch -c "\dt"` → 看到两张表。

---

## 第二步：改造 SessionService（30 分钟）

**做什么**：把 `ConcurrentHashMap` 替换成 PostgreSQL。

```java
// 现在
private final Map<String, ResearchSession> sessions = new ConcurrentHashMap<>();

// 改成
public interface SessionRepository extends JpaRepository<SessionEntity, String> {}
```

`SessionEntity` 是一个 `@Entity` 类，映射到 sessions 表。`history` 字段用 `@Column(columnDefinition = "jsonb")`。

**改的三个方法**：

| 方法 | 现在 | 改成 |
|------|------|------|
| `createSession()` | `sessions.put(id, obj)` | `sessionRepository.save(entity)` |
| `getSession()` | `sessions.get(id)` | `sessionRepository.findById(id)` |
| `getUserSessions()` | 遍历 filter | `sessionRepository.findByUserIdOrderByCreatedAtDesc(userId)` |

**history 的管理（关键区别——报告后不清空）**：

```
用户第一次问 "研究 AI 安全"
  → createSession() → history = ["用户: 研究AI安全"]

Agent 追问
  → history 追加 → ["用户: 研究AI安全", "Agent: （追问）"]

用户回复 "技术层面"
  → 前端带 session_id
  → Java 从 PG 读完整 history → 拼成 context → 传 Python

报告生成
  → appendReport(sessionId, report)
  → history 追加 "Agent: 报告已完成" → status = "done"
  → history 不清空！

用户再追问 "详细说说加密部分"
  → 同一 session_id
  → Java 读 history → Agent 看到完整上下文
  → 继续追加到同一 history
```

**前端改动**：

- 报告出来后 `contextHistory` 不清空
- 页面顶部显示会话列表（从 PG 读）
- 加一个"新会话"按钮 → 新建 session → 旧历史不影响新会话

---

## 第三步：加用户系统（1 小时）

**做什么**：注册/登录 + JWT 认证。

**新增文件**：

| 文件 | 行数 | 作用 |
|------|------|------|
| `SecurityConfig.java` | ~25 | Spring Security：哪些接口放行，哪些要认证 |
| `JwtTokenProvider.java` | ~35 | JWT 签发、验证、解析 user_id |
| `AuthController.java` | ~20 | POST /api/auth/register、POST /api/auth/login |

**数据流向**：

```
注册 → POST /api/auth/register → BCrypt → INSERT INTO users
登录 → POST /api/auth/login → 查 PG 验证密码 → 返回 JWT

后续任何请求:
  Header: Authorization: Bearer <JWT>
    → JwtTokenProvider 解析 JWT → 解出 user_id
    → ResearchController → session 带 user_id
    → Python Agent → 请求带 X-User-ID 头
    → kb.py 按 user_id 隔离
```

**Python 侧改动**：

- Java 在请求 Python 时加 `X-User-ID` HTTP 头
- Python server.py 从请求头取 user_id，传给 `kb.search(user_id=...)`

---

## 存储层全景图（完成后）

```
PostgreSQL (一个实例)
├── users       (BIGSERIAL, username, password_hash)
├── sessions    (VARCHAR, user_id, question, report, history JSONB, status)
└── 未来: pgvector 替代 Chroma

Redis (可选，rate limiting)
└── 当前开发阶段不需要

Chroma (本地，以后迁移到 pgvector)
├── kb_user_1  (user A 的文档)
└── kb_user_2  (user B 的文档)
```

---

## 每一步完成后的验证

### 第一步完成后

```bash
# 启动 PG 和网关，查数据库
docker exec postgres psql -U postgres -d deepresearch -c "SELECT * FROM sessions;"
# → 0 rows（表存在，数据库连接正常）

curl http://localhost:8080/api/sessions
# → []（空列表）
```

### 第二步完成后

```
浏览器: "研究 AI 安全" → 报告出
浏览器: 同一页面 "详细说说加密部分"
  → Agent 看到完整历史 → 直接深入加密 → 报告出
  → Ctrl+C 关 Java 网关 → 重启
  → 刷新页面 → 旧会话在 PG 里没丢 → 继续追问
```

### 第三步完成后

```
注册 → 登录 → 上传文件 → 提问 → 会话保存
  → 退出 → 换账号 → 看不到上一个账号的任何东西
```

---

## 工作量总结

| 步骤 | 新增文件 | 修改文件 | 行数 | 时间 |
|------|---------|---------|------|------|
| 1. PG + 建表 | schema.sql | pom.xml, application.yml | ~25 | 15m |
| 2. 会话持久化 | SessionRepository.java, SessionEntity.java | SessionService.java, ResearchController.java, index.html | ~90 | 30m |
| 3. 用户系统 | SecurityConfig, JwtTokenProvider, AuthController | 无 | ~100 | 1h |
| **合计** | **6 新文件** | **5 个** | **~215** | **~2h** |
