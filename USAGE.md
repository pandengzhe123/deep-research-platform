# 使用说明

> 这份文档讲**怎么用、怎么配、怎么上线**。项目是什么、架构怎么设计、评测体系怎么做的，
> 见 [README.md](README.md)。两份文档分工明确，不重复。

---

## 目录

1. [项目是什么](#1-项目是什么)
2. [前置条件](#2-前置条件)
3. [首次启动](#3-首次启动)
4. [建第一个账号 —— 注册三态与邀请码](#4-建第一个账号--注册三态与邀请码)
5. [环境变量完整清单](#5-环境变量完整清单)
6. [功能怎么用](#6-功能怎么用)
7. [成本控制](#7-成本控制)
8. [生产部署清单](#8-生产部署清单)
9. [安全模型](#9-安全模型)
10. [排查常见问题](#10-排查常见问题)

---

## 1. 项目是什么

输入一个研究问题 → Agent 自主搜索网络与知识库 → SSE 实时推送进度 → 生成带引用的研究报告。

**5 个服务，一个 compose 起完：**

```
浏览器 (Vue 3)
    │  只有这一个端口对宿主/公网开放
    ▼
nginx (:80)  ←── 静态文件 + /api/ 反向代理
    │
    ▼
Java 网关 (:8080)  ←── JWT 鉴权 · 会话持久化 · SSE 透传 · 入口唯一的鉴权点
    │
    ▼
Python Agent (:8000)  ←── 四级 Agent · RAG 检索 · 无自身鉴权（所以绝不对外暴露）
    │
    ├──▶ PostgreSQL (5432)  权威数据（会话 / 报告 / 历史 / token 用量）
    └──▶ Redis     (6379)   加速层 + 并发控制（缓存 / 去重 / 锁 / 配额 / 热层）
```

| 服务 | 宿主端口 | 说明 |
|---|---|---|
| frontend | `${FRONTEND_PORT:-3000}:80` | **唯一暴露的端口** |
| gateway | 仅 compose 内网 | `expose`，不发布 |
| agent | 仅 compose 内网 | `expose`，不发布 |
| postgres | `127.0.0.1:${PG_PORT:-5432}` | 只绑回环，需要直连走 SSH 隧道 |
| redis | `127.0.0.1:${REDIS_PORT:-6379}` | 同上 |

> ⚠️ **agent 和 gateway 故意不发布端口。** agent 自身没有任何认证（`user_id` 由调用方传入），
> 一旦发布到公网就等于绕过网关的全部鉴权、限流与配额。

---

## 2. 前置条件

| 需要什么 | 说明 |
|---|---|
| Docker + Docker Compose | 唯一硬依赖 |
| **DeepSeek API Key** | LLM。缺了研究请求会失败 |
| **Tavily API Key** | 联网搜索。缺了搜索会降级到 DuckDuckGo |
| **阿里云 DashScope API Key** | embedding + 精排。缺了知识库功能不可用 |

三个 Key 都填在 `agent/.env`，**不要**填在根目录 `.env`。

> 容器**启动本身不依赖任何 Key** —— 缺 Key 也能 `docker compose up` 成功，只是发起研究时会报错。

---

## 3. 首次启动

```bash
git clone <repo> && cd deep_research

# 1) 填 API Key
cp agent/.env.example agent/.env
#    编辑 agent/.env，至少填这三个：
#      DEEPSEEK_API_KEY=sk-xxx
#      TAVILY_API_KEY=tvly-xxx
#      DASHSCOPE_API_KEY=sk-xxx

# 2) 起服务
docker compose up -d --build
```

首次构建约 2~3 分钟，之后几秒。

**验证是否正常：**

```bash
docker compose ps                                    # 5 个服务都应是 Up，pg/redis 为 healthy
curl -s http://localhost:3000/api/health             # {"status":"ok",...}
```

浏览器打开 **http://localhost:3000** —— 你会看到登录页，**并且没有账号**。下一节解决这个。

> 本地开发**不需要**根目录 `.env`：compose 里所有 `${VAR:-默认值}` 的默认值就是给本地用的。

---

## 4. 建第一个账号 —— 注册三态与邀请码

### 注册有三种状态

用两个变量组合，每种状态各表达一件事：

| 配置 | 模式 | 效果 |
|---|---|---|
| `REGISTER_OPEN=true` | **open** | 任何人都能注册，不需要邀请码 |
| `REGISTER_OPEN=false` + `REGISTER_INVITE_CODE=xxx` | **invite** | 必须填对邀请码 |
| `REGISTER_OPEN=false` + 邀请码留空 | **closed** | 注册接口整体关闭（**代码默认**） |

登录页会调 `GET /api/auth/register-open` 拿到 `{"open":…,"mode":…,"inviteRequired":…}`，
据此如实渲染（关闭时按钮置灰并说明原因、open 模式不显示邀请码框）。

```bash
# 看当前是什么模式
curl -s http://localhost:3000/api/auth/register-open
# 启动日志里也会打印：  注册模式: open / invite / closed
docker compose logs gateway | grep 注册模式
```

### 邀请码是什么

**邀请码是 invite 模式下的「注册许可」** —— 只有填对这个串才放行。它**不是**用户分组、
不是权限等级、不是发给用户的福利码。

用**常量时间比较**，不会因为响应耗时差异被逐字符猜出来。填错或没填都返回同一句话
「邀请码不正确」，不区分两者。

### 该选哪一态

| 你的场景 | 建议 |
|---|---|
| 只是自己/面试官用，账号可控 | `closed` + 手动建号（见下），或 `invite` |
| 想让访客自助注册（**公开演示站**） | `open` —— 但**先把成本闸门收紧**（见下） |

**开放注册等于把付费额度对外开放**，量级参考：

- 单次 **L4** 研究实测 **232 万 prompt token**
- 单次 L2 约 2.3 万、L3 约 3 万 token

所以 `open` 之前请确认这三项：

```bash
USER_DAILY_RESEARCH_LIMIT=5      # 默认 20，公开站建议收紧
GLOBAL_DAILY_RESEARCH_LIMIT=50   # 默认 100
ALLOW_LEVEL4_NON_ADMIN=false     # 保持 false：L4 只给管理员
```

> ⚠️ **知识库目前只有「单文件 20MB」限制，没有单用户容量/数量上限。** 开放注册后，
> 任何人都能反复上传把磁盘占满（且每次上传都会调阿里云 embedding 产生费用）。
> 如果长期开放，建议再加一层单用户配额。

### 怎么切换模式

```bash
# 例：改成完全开放
echo "REGISTER_OPEN=true" >> .env        # 根目录 .env，不入库
docker compose up -d gateway

# 例：改成需要邀请码
#   把 .env 写成：  REGISTER_INVITE_CODE=你的邀请码
#                   REGISTER_OPEN=false
docker compose up -d gateway

# 例：关掉注册
#   把 .env 写成：  REGISTER_OPEN=false
#                   REGISTER_INVITE_CODE=
docker compose up -d gateway
```

> **改值时请「把值改成空」而不是「删掉整行」。** 两种都能达到效果，但删行在不同 shell 下
> 容易出错 —— 例如 PowerShell 里 `Set-Content` 收到空管道输入时**是空操作**，文件根本不会
> 被改写，你会以为改了其实没改。改成空值没有这个问题，且一眼能看出当前状态。

### 怎么建管理员

注册出来的账号角色都是 `user`。要管理员权限就改角色：

```bash
docker compose exec postgres psql -U postgres -d deepresearch \
  -c "UPDATE users SET role='admin' WHERE username='你的用户名';"
```

改完**必须重新登录** —— 角色写在 JWT 里，旧 token 仍带旧角色。

也可以用后台改：管理员登录后进 `/admin/users`，表格里的「角色」下拉框可以直接切。

### 其他建账号方式

不想开注册，也可以直接写库（bcrypt 哈希用 agent 容器生成，已验证 Java 端能验证通过）：

```bash
# 生成哈希
docker compose exec agent python -c \
  "import bcrypt; print(bcrypt.hashpw(b'你的密码', bcrypt.gensalt(rounds=10, prefix=b'2a')).decode())"

# 写库
docker compose exec postgres psql -U postgres -d deepresearch -c \
  "INSERT INTO users (username, password_hash, role, enabled) VALUES ('用户名','<上一步输出>','admin',true);"
```

### 注册相关的其他规则

| 规则 | 值 | 在哪里改 |
|---|---|---|
| 密码最短长度 | 8 位 | `AuthController.MIN_PASSWORD_LENGTH` |
| 认证端点限流 | 每 IP 每分钟 20 次 | `AUTH_RATE_LIMIT_PER_MINUTE` |
| 被停用的账号 | 登录返回 403「账号已被禁用」 | 后台 `/admin/users` 或改 `users.enabled` |
| 登录失败提示 | 一律「用户名或密码错误」 | 不区分用户不存在与密码错，避免枚举用户名 |
| 注册是否开放 | `GET /api/auth/register-open` → `{"open":true/false}` | 由 `REGISTER_INVITE_CODE` 是否为空决定 |

### 登录页会如实反映注册状态

登录页在挂载时会调 `/api/auth/register-open`，据此决定「注册」按钮的形态：

| 服务端状态 | 登录页表现 |
|---|---|
| 注册开放 | 显示「邀请码」输入框，按钮为可点的「注册新账号」 |
| 注册关闭 | **隐藏**邀请码输入框，按钮变为禁用的「注册已关闭」，并提示「本站已关闭注册。需要账号请联系管理员。」 |
| 查询失败 | 一律按「关闭」处理 —— 宁可提示联系管理员，也不给一个点了必然失败的按钮 |

> 之所以做这个：注册默认关闭，而登录页原本一直摆着可点的「注册新账号」按钮，用户只能靠
> 点下去拿到 403 才知道关着 —— 体验上就是「点了没反应 / 莫名其妙失败」。让界面反映服务端
> 真实状态，比事后解释省事得多。
>
> 另外按钮在用户名/密码为空时会**明确提示**，不再静默返回。

---

## 5. 环境变量完整清单

### 5.1 根目录 `.env`（生产必填，本地可全部不建）

由 docker compose 读取，用于变量替换。

| 变量 | 默认值 | 作用 | 生产 |
|---|---|---|---|
| `JWT_SECRET` | 空 | JWT 签发密钥，**至少 32 字节** | **必填** |
| `POSTGRES_PASSWORD` | `deepresearch` | 数据库密码 | **必填** |
| `REDIS_PASSWORD` | `deepresearch-dev-redis` | Redis 密码 | **必填** |
| `REGISTER_OPEN` | `false` | `true` = 任何人可注册（**open**） | 公开演示站按需 |
| `REGISTER_INVITE_CODE` | 空 | 邀请码；`REGISTER_OPEN=false` 时：设了=**invite**，留空=**closed** | 按需 |
| `FRONTEND_PORT` | `3000` | 前端对外端口 | 改成 `80` |
| `PG_PORT` / `REDIS_PORT` | `5432` / `6379` | 回环绑定端口 | 一般不动 |
| `LOG_LEVEL` | `INFO` | 网关日志级别 | 保持 `INFO` |
| `GLOBAL_DAILY_RESEARCH_LIMIT` | `100` | 全站每日研究次数上限 | 按预算定 |
| `USER_DAILY_RESEARCH_LIMIT` | `20` | 单用户每日上限 | 按预算定 |
| `QUOTA_FAIL_CLOSED` | `true` | Redis 挂时是否拒绝研究 | 见 §7 |
| `RATE_LIMIT_PER_MINUTE` | `10` | 单用户每分钟研究次数 | 一般不动 |
| `AUTH_RATE_LIMIT_PER_MINUTE` | `20` | 认证端点每 IP 每分钟尝试上限 | 一般不动 |
| `ALLOW_LEVEL4_NON_ADMIN` | `false` | 非管理员能否用 L4 | 保持 `false` |
| `MAX_UPLOAD_BYTES` | `20971520` | 单文件上传上限（20MB） | 按需 |
| `MAX_IN_MEMORY_BYTES` | `20971520` | 网关请求体内存上限 | 与上行一致 |
| `MAX_COMPRESS_CHARS` | `400000` | 上下文压缩输入上限 | 一般不动 |
| `REPORT_MAX_TOKENS` | `384000` | 报告生成的输出上限 | 见下方说明 |

> **关于 `REPORT_MAX_TOKENS`**：显式设置它是为了不依赖服务端默认值 —— 实测默认值
> 并不稳定（同一个 prompt 有时自然结束在 9.7K tokens，有时 8192 就被硬切，报告断在
> 半句而无人察觉）。
>
> **上限 ≠ 目标**：模型写完即停（`finish_reason=stop`），本项目报告的自然长度约
> 9.7K tokens / 23K 字符，384000 只是 40 倍余量。
>
> 被截断时系统会**显式告知**：报告末尾追加 Markdown 提示块（前端可见），
> trace 的 `llm_call` 记录 `finish_reason`，`run_end.summary.truncated_calls` 计数。
> 调大上限后要留意：报告会进入对话历史，单份超大报告会触发上下文压缩。

**`JWT_SECRET` 生成方式：**

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

> ⚠️ **绝不能留空，也不能用仓库里出现过的任何字符串。** 本仓库是公开的 —— 硬编码的默认
> 密钥等于公开的万能签发密钥，知道它的人可以自签一个 `role=admin` 的 token 直接进后台。
>
> 留空的后果不是"不安全"而是"不可用"：网关每次启动随机生成密钥，**重启后所有人被踢下线**。

> ⚠️ **`POSTGRES_PASSWORD` 只在数据卷首次初始化时生效。** 若 `pgdata` 卷已存在，改这个变量
> 不会改掉真实密码，需要进容器执行：
> `docker exec -it deepresearch-postgres-1 psql -U postgres -c "ALTER USER postgres WITH PASSWORD '新密码';"`

### 5.2 `agent/.env`（API Key 与 Agent 调参）

由 compose 的 `env_file` 注入 agent 容器。

| 变量 | 默认值 | 作用 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 空 | **必填**，LLM |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | LLM 端点 |
| `LLM_MODEL` | `deepseek-v4-flash` | 模型名 |
| `LLM_PROVIDER` | `deepseek` | 提供商标识 |
| `TAVILY_API_KEY` | 空 | **必填**，联网搜索 |
| `DASHSCOPE_API_KEY` | 空 | **必填**，embedding + 精排 |
| `EMBED_MODEL` | `text-embedding-v4` | 阿里云 embedding 模型 |
| `EMBED_BASE_URL` | DashScope 兼容模式端点 | embedding 端点 |
| `RERANK_MODEL` | `gte-rerank-v2` | 精排模型（不要退回已下线的 `gte-rerank`） |
| `MAX_SEARCH_WORKERS` | `3` | 搜索并发 |
| `MAX_SEARCH_ROUNDS` | `10` | 单次研究最多搜索轮数 |
| `MAX_SUPERVISOR_ROUNDS` | `6` | L4 Supervisor 最多轮数 |
| `MAX_PARALLEL_RESEARCHERS` | `5` | L4 并行研究员数 |
| `MAX_CONTENT_LENGTH` | `20000` | 单页正文截断长度 |
| `MAX_HISTORY_CHARS` | `1000000` | 对话历史字符上限 |
| `MAX_RESULTS_CHARS` | `300000` | 单轮搜索结果字符上限 |
| `MAX_ROUND_RESULTS` | `3` | 保留最近几轮搜索结果 |
| `SEARCH_CACHE_TTL` | `300` | 搜索缓存秒数 |
| `REDIS_URL` | `redis://localhost:6379/0` | **compose 会覆盖它**，容器内连 `redis` 主机名 |

> `REDIS_URL` 由 compose 的 `environment` 显式设置（`environment` 优先级高于 `env_file`），
> 所以你**不需要**在 `agent/.env` 里配它 —— 写了也会被覆盖。

---

## 6. 功能怎么用

### 6.1 研究界面（`/`）

- **提问** → 报告以 SSE 流式返回，实时显示搜索/规划/精排进度
- **追问** → 同一会话继续提问，后端自动带上历史上下文（前端只需传 `session_id`）
- **四级 Agent** —— 会话里可切换：

| 级别 | 行为 | 实测耗时 | 实测成本量级 |
|---|---|---|---|
| L1 | 快速：单轮搜索 → 直接写报告 | ~6s | 小 |
| L2 | 澄清判断 → 多轮搜索 | ~135s | 约 2.3 万 token |
| L3 | 单 Agent 深度多轮 + 跨轮 URL 去重 | ~343s | 约 3 万 token |
| L4 | Supervisor 双层 + 并行研究员 | 分钟级 | **约 232 万 token** |

> **L4 默认只允许管理员**（`ALLOW_LEVEL4_NON_ADMIN=false`）。非管理员请求会在**跑之前**
> 就被 403 拒绝，不会产生费用。

- 会话左侧可切换历史会话，报告支持复制 Markdown
- 报告渲染经过 **DOMPurify 净化**（报告内容来自抓取到的任意网页，存在注入风险）

### 6.2 知识库

在会话内操作，全部走 `/api/kb/*`（**`user_id` 由网关从 JWT 解析，前端不传、也传不了**）。

| 操作 | 支持格式 | 限制 |
|---|---|---|
| 上传 | `.txt` / `.md` / `.pdf` / `.docx` | 单文件 ≤ 20MB（nginx / 网关 / agent 三层都限） |
| 列表 | 只列自己的 | 多租户物理隔离，per-user Collection |
| 删除 | 按 `doc_id` | |

上传后用「检索模式」控制怎么用知识库：

| 模式 | 含义 |
|---|---|
| `web_only` | 只搜网络 |
| `hybrid` | 网络 + 知识库 |
| `rag_only` | 只查知识库 |

还可以勾选**指定文档**（`rag_doc_ids`），只在这几篇里检索。

### 6.3 管理后台（`/admin`、`/admin/users`）

**仅管理员可见**（前端路由守卫 + 服务端 403 双重校验）。

- **仪表盘**：累计/今日研究次数、活跃会话、失败率、注册用户数、Token 消耗（含输入/输出拆分）、
  近 7/30 天研究趋势、近 30 天 Token 趋势、用户研究次数 Top 10、状态分布
- **用户管理**：用户列表（含各自研究次数）、切换角色、启用/禁用账号

> **演示前注意**：近 7 天 / 近 30 天图表统计的是**真实活动**。如果最近没有研究记录，
> 图表会是空的 —— 这不是坏了，先跑几次研究就有内容。

### 6.4 两种研究接口

| 接口 | 用途 | token 记录 |
|---|---|---|
| `POST /api/research/stream` | **前端用这个**，SSE 流式 | ✅ |
| `POST /api/research` | 同步，等跑完返回 JSON | ✅（2026-09 已补） |

两者的参数一致：`question`、`level`、`session_id`、`language`、`search_mode`、`rag_doc_ids`、`max_rounds`。

---

## 7. 成本控制

四道闸，从外到内：

| 闸门 | 粒度 | 默认 | 超限响应 |
|---|---|---|---|
| 认证端点限流 | 每 IP / 分钟 | 20 | 429 |
| **注册关闭** | 全站 | 关闭 | 403 |
| 每分钟研究限流 | 每用户 / 分钟 | 10 | 429 `rate_limited` |
| **每日研究配额** | 全站 + 每用户 / 天 | 100 / 20 | 429 `daily_limited` |
| **L4 闸门** | 每请求 | 非管理员禁用 | 403 |

**配额在「真正开始研究前」检查** —— 被限流/被锁/被配额拒绝的请求**不消耗额度，也不产生任何 API 费用**。

### 关于 `QUOTA_FAIL_CLOSED`

默认 `true`：**Redis 不可用时拒绝研究**，而不是放行。

这是刻意的取舍。每分钟限流降级只是"这一分钟没人管"，每日配额降级却是"成本完全无上限" ——
两者代价差了数量级，所以不共用失败姿态。

代价是：Redis 抖动时站点会拒绝研究请求（返回「配额服务暂时不可用」）。
如果你的站点**可用性优先于成本可控**，改成 `false`。

> 对照：作为加速层的搜索缓存、URL 去重、并发锁在 Redis 挂掉时是**放行**的（可用性优先），
> 只有配额这一处是 fail-closed。见 README 的「Redis 5 个落点」。

### 看实际花了多少

后台仪表盘有累计 Token 消耗；也可以直接查库：

```bash
docker compose exec postgres psql -U postgres -d deepresearch -c \
  "SELECT sum((token_usage->>'total_prompt_tokens')::bigint) AS prompt,
          sum((token_usage->>'total_completion_tokens')::bigint) AS completion
   FROM sessions WHERE token_usage <> '{}'::jsonb;"
```

---

## 8. 生产部署清单

### 8.1 上线前必做

```bash
cp .env.example .env
```

然后**至少**填这四项（其余按需）：

```bash
JWT_SECRET=<python -c "import secrets; print(secrets.token_urlsafe(48))">
POSTGRES_PASSWORD=<强密码>
REDIS_PASSWORD=<强密码>
REGISTER_INVITE_CODE=        # 留空即关闭注册 —— 演示站就该留空
FRONTEND_PORT=80
```

外加 `agent/.env` 里的三个 API Key。

> **建议重新申请 API Key**，不要在服务器上沿用本地开发用的那几把 —— 它们已在本地明文使用过。
> 并在三个平台各设消费上限。

### 8.2 端口与访问

```bash
docker compose up -d --build
```

- 只有 `FRONTEND_PORT`（生产改成 80）对外开放
- **云服务器安全组也要只放行 80/443** —— compose 绑了回环只是第一层，安全组是第二层
- 需要直连数据库调试时用 SSH 隧道：
  `ssh -L 5432:127.0.0.1:5432 user@your-server`

### 8.3 HTTPS（**目前唯一未完成的安全项**）

现在只有 `listen 80`，**token 明文过网**。要上 TLS：

1. 域名解析到服务器
2. 证书放进 frontend 容器（或前面再套一层宿主 nginx/caddy 做终止）
3. nginx 加 `listen 443 ssl` + 证书路径
4. **然后**打开 `frontend/nginx.conf` 里那行注释掉的 HSTS

> **国内服务器 + 域名必须 ICP 备案**（1~3 周，通过云厂商提交）。这是整个流程里耗时最长的
> 一步，**应该最先启动**，而不是等一切就绪再办。香港/海外服务器不需要备案，代价是延迟与稳定性。

### 8.4 上线后

```bash
docker compose ps                    # 全 Up
docker compose logs gateway | tail   # 无 ERROR
```

- **确认注册是关闭的**：`curl -X POST .../api/auth/register -d '{...}'` 应返回 403
- **确认旧账号已处理**：如果沿用旧库，先看 `SELECT id, username, role, enabled FROM users;`，
  把不需要的账号删掉或改掉弱密码（见 §10 第一条）
- 配一个 PG 定时备份

---

## 9. 安全模型

### 已做的加固

| 项 | 做法 |
|---|---|
| 认证 | JWT（HS256）。密钥必须由环境变量注入，**无硬编码默认值**；未配置则随机生成 |
| 鉴权边界 | **唯一入口是 Java 网关**。agent 不发布端口，`/kb/` 不再直连 |
| 用户身份 | `user_id` **一律**由网关从 JWT 解析（`RequestUserResolver`），客户端传什么都不作数 |
| 会话归属 | `/api/sessions/{id}` 校验归属，不匹配返回 **404**（不用 403，避免确认 ID 是否存在） |
| 会话 ID | 32 位十六进制（128 bit）。原先取 UUID 前 8 位只有 32 bit，可被爆破枚举 |
| 注册 | 默认关闭；邀请码常量时间比较 |
| 密码 | 最短 8 位，bcrypt 存储；登录失败不区分「用户不存在」与「密码错」 |
| 限流 | 认证端点每 IP 每分钟；研究端点每用户每分钟 + 每日配额 |
| 上传 | 三层体积限制（nginx / 网关 / agent）；类型白名单 |
| XSS | 报告经 DOMPurify 净化 **+** CSP（`script-src 'self'`，不含 `unsafe-inline`） |
| 响应头 | CSP / X-Frame-Options / X-Content-Type-Options / Referrer-Policy |
| 错误信息 | 5xx 一律换成通用文案，不回传内部异常与路径 |
| 密钥管理 | 真实密钥只在 `.env`（gitignored）；仓库内无任何默认密钥 |

### 已知边界

- **HTTPS 未配** —— 目前 token 明文过网。这是唯一未闭环的项
- **知识库没有单用户容量上限** —— 只有单文件 20MB 限制。开放注册（`REGISTER_OPEN=true`）后，
  任何人可反复上传占满磁盘，且每次上传都会调阿里云 embedding 产生费用。长期开放建议补一层配额
- **agent 的 `user_id` 参数不二次校验** —— 靠"不发布端口"这一层保证；若将来暴露 agent，必须补
- **限流是单实例内存态**（`AuthRateLimiter`）—— 多实例部署时每个实例各算各的，阈值会被放大 N 倍，届时需换成 Redis 计数
- **`/api/research/{taskId}` 取消端点前端未接线** —— 端点在且归属校验已做，但界面上没有取消按钮

---

## 10. 排查常见问题

### 打开页面卡在登录页，注册按钮是灰的

说明当前是 `closed` 模式。看你想要哪种：

```bash
# 想让访客自助注册
echo "REGISTER_OPEN=true" >> .env && docker compose up -d gateway

# 或走邀请码
#   .env 写：  REGISTER_INVITE_CODE=你的邀请码
#             REGISTER_OPEN=false
#   docker compose up -d gateway
```

改完**刷新浏览器**（前端会重新查一次注册状态）。详见 §4。

### 点了「注册」没反应

已修复：早前版本在用户名/密码为空时**静默返回，不给任何提示**。现在会明确提示
「请先填写用户名和密码」。若仍无反应，按 `Ctrl+F5` 强制刷新，确认加载的是新前端产物。

### 改了 `.env` 但没生效

compose 的变量替换在**容器创建时**发生，必须重建：

```bash
docker compose up -d --force-recreate gateway
```

### 登录后过一会儿被踢下线

`JWT_SECRET` 没设置 —— 网关每次重启都会随机生成新密钥，所有旧 token 立即失效。
设置一个固定值即可（见 §5.1）。

### 提示「配额服务暂时不可用」

Redis 挂了，且 `QUOTA_FAIL_CLOSED=true`（fail-closed）。检查：

```bash
docker compose ps redis
# 下面把 <redis密码> 换成你 .env 里 REDIS_PASSWORD 的实际值
# （根目录 .env 不会自动进 shell 环境，所以这里必须手填，不能写 $REDIS_PASSWORD）
docker compose exec redis redis-cli --no-auth-warning -a '<redis密码>' ping
```

想改成可用性优先：`QUOTA_FAIL_CLOSED=false`。

### 提示「你今日的研究次数已达上限」

每日配额用完了。查当前用量（把日期换成今天，格式 `YYYYMMDD`，例如 `20260925`）：

```bash
docker compose exec redis redis-cli --no-auth-warning -a '<redis密码>' \
  MGET "quota:global:20260925" "quota:user:<用户ID>:20260925"
```

需要立即放行就调大 `GLOBAL_DAILY_RESEARCH_LIMIT` / `USER_DAILY_RESEARCH_LIMIT`，或直接删掉对应 key：

```bash
docker compose exec redis redis-cli --no-auth-warning -a '<redis密码>' DEL "quota:user:<用户ID>:20260925"
```

### 知识库上传成功但检索不到

1. 确认走了知识库模式：`search_mode` 需要是 `rag_only` 或 `hybrid`
2. 确认勾选的文档包含目标文档（`rag_doc_ids`）
3. **确认 chroma 数据卷还在** —— 没有它，每次 `docker compose up` 重建容器都会丢失知识库。
   （本仓库已给 agent 挂了 `chroma` 命名卷，若你自己改了 compose 要留意这点）
4. 看 agent 日志有没有 embedding 相关报错

### 后台「累计 Token 消耗」看着偏低

该统计只累计**有 `token_usage` 记录**的会话。历史数据里 2026-09 之前的同步研究没有记录
（已修复，见 `5b650d3`），所以旧数据天然偏低。新产生的数据是准的。

### 后台 Token 趋势图 / 研究趋势图是空的

统计的是真实活动。最近没有研究记录时图表就是空的。先跑几次研究。

### 构建失败在 `huggingface.co`

**不应该再出现** —— 本地模型已于 2026-09 全部移除，构建不再访问 HuggingFace。
如果你在旧代码上遇到这个，升级到最新提交即可（镜像也从 9.08GB 降到 1.04GB）。

### 想看某个服务在干嘛

```bash
docker compose logs -f gateway      # 网关（鉴权 / 会话 / 403 / 404 记录）
docker compose logs -f agent        # Agent（搜索 / 精排 / 配额）
docker compose logs --tail=50 frontend
```

### 彻底重置（会丢数据）

```bash
docker compose down -v        # -v 连数据卷一起删
docker compose up -d --build
```

---

## 附：本次变更摘要（2026-09）

| 提交 | 内容 |
|---|---|
| `0620a04` | 凭据与暴露面加固：删掉硬编码 JWT 默认密钥、PG/Redis 改绑回环、agent/gateway 不再发布端口、Redis 加密码、补 `restart` 策略 |
| `2b837e0` | 移除本地 embedding / 精排模型，改走阿里云 API。**镜像 9.08GB → 1.04GB**，构建不再依赖 HuggingFace |
| `e81bba7` | 修复 `/kb/` 越权与 `/api/sessions/{id}` 越权、封注册、加成本熔断与安全响应头、补齐上传限制与错误信息收敛 |
| `5b650d3` | 同步研究路径补记 token 用量（此前控制台 Token 统计只算 SSE 那一半） |
| `6a69999` | 新增本操作手册；修正 README 中被上述改动变成错误的内容 |
| `cc5d057` | 登录页如实反映注册状态；字段为空时给出提示（此前点了没反应） |
| 本次 | 注册改为**三态**（open / invite / closed）—— 原先只有两态，表达不了"完全开放" |
