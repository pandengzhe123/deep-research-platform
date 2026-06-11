# 完整开发路线图

> 当前进度 90%。Phase 1-5 已完成，Phase 6 为可选增强。

---

## 阶段总览

```
Phase 1: RAG 核（单用户版）        ████████████  ~110 行  2h   简历 ⭐⭐⭐
Phase 2: 会话系统 + 持久化          ██████        ~80 行   1.5h  简历 ⭐⭐
Phase 3: 用户系统 + 多租户         ██████████    ~120 行  3h   简历 ⭐⭐⭐
Phase 4: 部署 + 交付              ████          ~50 行   1h   简历 ⭐⭐
Phase 5: 前端升级                 ████████████  ~300 行  5h   简历 ⭐
Phase 6: 功能补完                 ████          ~100 行  2h   简历 ⭐
```

---

## Phase 1：RAG 核（单用户版）—— 现在做

**目标**：Agent 能同时搜网络和本地知识库

### 新增文件

```
agent/src/researcher/kb.py        ~60 行  Chroma + embedding + 检索
```

### 修改文件

```
agent/src/researcher/agent.py     ~20 行  TOOLS 加 search_kb，工具路由
agent/src/researcher/server.py    ~15 行  POST /kb/upload, GET /kb/files
agent/pyproject.toml              +2 行   chromadb, sentence-transformers
index.html                        ~10 行  联网搜索 + RAG 检索开关
ResearchModels.java               ~5 行   search_mode 字段
```

总计 ~110 行新代码。

### 切块策略：段落优先 → 句子 → 字符（三级降级）

```
chunk_text(text, chunk_size=500, overlap=100):
  1. 按 \n\n 段落切 → 段落 ≤ 500 直接作为一个 chunk
  2. 段落太长 → 按句号/感叹号/问号切句子
  3. 句子还太长 → 按 chunk_size 硬切 + overlap 重叠
```

不用 LangChain 的 TextSplitter——手写 15 行，逻辑透明。

### 支持的文件类型

| 类型 | 读取方式 | 依赖 |
|------|---------|------|
| `.txt` | 直接读 | 无 |
| `.md` | 直接读 | 无 |
| `.pdf` | PyMuPDF (`fitz`) 提取文本 | `pymupdf`（原项目已有） |

三个覆盖 90% 场景。Word/PPT 不做——边际成本太高。

### Embedding 模型

**`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`**

| 维度 | 值 |
|------|-----|
| 大小 | 118MB |
| 向量维度 | 384 |
| 最大输入 | 128 tokens（中文约 100 字） |
| 语言 | 中/英文 |
| 速度 | CPU 上 ~1000 条/秒 |
| 费用 | 0（本地运行） |

**为什么是这个**：中英文都行、轻量本地跑、384 维 Chroma 检索快。代价是单段只能编码 ~100 中文字符——这就是为什么切块设 500 字符，embedding 前自动截断到模型上限。

### 整体架构

```
POST /kb/upload  (file + user_id="default")
    │
    ▼
① 读文件 → 纯文本（PDF 走 PyMuPDF，TXT/MD 直接读）
    │
    ▼
② chunk_text() → ["片段1", "片段2", ...]
    │
    ▼
③ model.encode(片段) → [[0.12, -0.34, ...], [...], ...]  ← 384 维向量
    │
    ▼
④ collection.add(
        documents=片段,
        embeddings=向量,
        metadatas=[{"user_id": "default", "doc_id": "report.pdf"}],
        ids=[chunk_id_1, chunk_id_2, ...]
   )
    │
    ▼
⑤ 返回 {"status": "ok", "doc_id": "report.pdf", "chunks": 23}

Agent 研究时:
⑥ 调 search_kb 工具
    │
    ▼
⑦ collection.query(
        query_texts=["量子计算应用场景"],
        where={"user_id": "default"},           ← 用户隔离
        n_results=5
   )
    │
    ▼
⑧ 5 个最相关片段 → 拼成格式化文本 → 作为 tool message 返回给 Agent
```

### Chroma 元数据设计（天然为 Phase 3 多租户预留）

```python
# 入库：user_id + doc_id
metadatas=[{"user_id": "default", "doc_id": "report.pdf"}]

# 检索：按用户 + 指定文档过滤
collection.query(
    query_texts=["量子计算"],
    where={
        "user_id": "user_1",
        "doc_id": {"$in": ["report.pdf", "research.docx"]}  ← 会话级文档选择
    }
)
```

现在 `user_id` 写死 `"default"`，Phase 3 加多租户时只改一行传参。

### 简历卖点

> "集成 RAG 混合检索——Agent 同时调用 Tavily 实时搜索和 Chroma 本地向量检索，按语义融合两者结果生成报告。自研段落→句子→字符三级切块策略，选用多语言 MiniLM 模型，向量库元数据设计预留了多租户和会话级文档过滤。"

---

## Phase 2：会话系统 + 持久化

**目标**：重启不丢。同一会话内 Agent 记住之前说过什么，后续追问不丢失上下文。

### 当前问题

```
会话生命周期：

用户: "研究AI安全"
  → Agent 追问 "技术还是政策？"
  → 前端 contextHistory = ["用户: 研究AI安全"]

用户: "技术层面"
  → 前端带 contextHistory → Agent 看到完整上下文 → 开始研究
  → 报告出来 → 前端清空 contextHistory ← 问题！

用户: "详细说说加密部分"
  → contextHistory 为空！
  → Agent 不知道之前研究的是 AI 安全 → 乱搜或追问
```

**根因**：报告生成后 `contextHistory` 被清空，后续追问变成新会话。

### 修复方案：不改 Agent 逻辑，只改会话数据流

核心思路和原项目一样——**报告生成后不清空 history，只追加**。Agent 每次收到的不是"当前问题"，而是"完整对话历史"。

```
SessionService.java 管理的会话对象新增字段：

class ResearchSession {
    String id;
    String question;           // 最初的问题
    String report;             // 最终报告
    String status;
    List<String> history;      // ← 新增：完整对话链
    // history = [
    //   "用户: 研究AI安全",
    //   "Agent: （澄清追问）你关心技术还是政策？",
    //   "用户: 技术层面",
    //   "Agent: （报告摘要）...",
    //   "用户: 详细说说加密部分"        ← 报告后继续追加！
    // ]
}
```

### 技术选型

| 组件 | 选型 | 原因 |
|------|------|------|
| 数据库 | **H2**（file mode） | Java 原生嵌入式，Spring Boot 自带，零安装。数据存磁盘，重启不丢 |
| ORM | Spring Data JPA | 三行注解搞定 CRUD |
| 迁移 | `schema.sql` | 放 `resources/` 里，Spring Boot 启动自动执行 |

**为什么不用 SQLite/PostgreSQL/Redis**：SQLite 在 Java 下集成不如 H2 顺滑；PostgreSQL 要单独装服务（等 Docker 阶段再切）；Redis 适合存 session 状态不适合存几千字的报告正文。**H2 开发/演示阶段最合适，后续一行配置换 PostgreSQL。**

### 数据库设计

```sql
CREATE TABLE sessions (
    id VARCHAR(8) PRIMARY KEY,
    user_id VARCHAR(64) DEFAULT 'anonymous',
    question TEXT,
    report TEXT,
    history TEXT,                              -- JSON array: 完整对话链
    search_mode VARCHAR(20) DEFAULT 'hybrid',  -- web / rag / hybrid
    rag_docs TEXT,                              -- JSON array: ["report.pdf"]
    status VARCHAR(20) DEFAULT 'running',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

一个表，9 个字段。`history` 字段用 JSON 数组存整个对话链——不做外键关联更简单。

### 数据流变化

```
之前（有缺陷）：
  report 生成后 → contextHistory 清空 → 后续追问丢失上下文

之后（Phase 2）：
  report 生成后 → history 追加 "Agent: （报告摘要）" → 不清空
  后续追问 → Java 读完整 history → 拼成 context → Python Agent 看到完整上下文
  → Agent 知道之前研究过什么 → 直接深入追问主题
```

### 修改文件

```
SessionService.java           ~30 行  H2 + JPA 替换 ConcurrentHashMap + history 管理
ResearchController.java       ~15 行  报告生成后追加 history，不清空
index.html                    ~20 行  会话列表、历史加载、context 不清空
agent/src/researcher/kb.py    ~15 行  doc_id 级别 where 过滤
schema.sql                     ~10 行  建表语句
```

总计 ~80 行。

### 简历卖点

无——这是工程基础不是亮点。但面试被问"怎么存数据""怎么让 Agent 记住上下文"时有答案：H2 + JPA + JSON 字段存对话链。

---

## Phase 3：用户系统 + 多租户

**目标**：注册登录，自己的文件自己用，别人的看不到

```
新增文件：
  java-gateway/.../security/SecurityConfig.java        ~30 行  Spring Security
  java-gateway/.../security/JwtTokenProvider.java      ~40 行  JWT 签发/验证
  java-gateway/.../controller/AuthController.java      ~20 行  登录/注册
  agent/src/researcher/auth.py                          ~15 行  验证 JWT 中间件

修改文件：
  schema.sql                                           ~15 行  users 表
  SessionService.java                                  ~20 行  关联 user_id
  ResearchController.java                              ~10 行  从 JWT 提取 user_id
  kb.py                                                 ~5 行  user_id 写入元数据

总计：~120 行
```

**简历卖点**：
> "实现 JWT 多租户认证——每个用户独立的 RAG 知识库、会话历史和搜索偏好。向量检索按 user_id 命名空间隔离，确保数据安全。"

---

## Phase 4：部署 + 交付

**目标**：别人能用。`docker compose up` 一行启动。

```
新增文件：
  agent/Dockerfile            ~10 行
  java-gateway/Dockerfile     ~8 行
  docker-compose.yml          ~20 行
  nginx.conf                  ~15 行  反向代理（可选）

修改文件：
  application.yml             ~5 行  agent.url 改成容器间网络

总计：~50 行
```

**启动命令**：
```bash
git clone ... && docker compose up -d
# → http://localhost:8080
```

**简历卖点**：
> "Docker Compose 一键部署， Python Agent + Java 网关双容器编排。可部署到任何云服务器。"

---

## Phase 5：前端升级 ✅ 已完成

**目标**：从纯 HTML 换成 Vue 3 / React → **Vue 3 + Vite 已完成**

**成果**：
- Vue 3 + Vite + vue-router + Pinia + axios + marked
- 全屏聊天布局：左侧栏 + 中间消息 + 右侧 KB
- 登录/注册页 + 路由守卫
- 多轮追问 + 会话管理 + localStorage 缓存
- 退出确认弹窗 + 响应式布局

---

## Phase 6：功能补完（可选）

| 功能 | 行数 | 优先级 | 说明 |
|------|------|--------|------|
| 报告导出 PDF/Word | ~30 | 低 | 后端生成，前端下载 |
| 搜索结果缓存 | ~40 | 中 | 同 query 复用，省钱 |
| SSE 流式 Java 透传 | ~50 | 中 | 目前同步够用，修不修看时间 |
| Token 超限处理 | ~50 | 中 | 只有超长报告才会触发 |
| WebSocket 替代 SSE | ~80 | 低 | 允许中途取消时双向通信用 |

---

## 决策建议

| 如果目标是 | 做到哪个阶段 | 总工作量 |
|-----------|------------|---------|
| **校招面试** | Phase 1（RAG）+ Phase 4（Docker） | ~160 行，3h |
| **实习面试** | Phase 1-4 全部 | ~360 行，7h |
| **个人项目展示** | Phase 1 + 2 + 4 | ~240 行，5h |

**Phase 3（用户系统）是量最大的单阶段**——JWT + 多表 + 安全配置。如果时间紧可以先跳过，面试时说"当前是多用户架构预留、单用户运行，后续加 JWT 即可多租户"。

---

## 当前状态

Phase 1-5 已完成，Phase 6 为可选增强。
