# Java 网关 + Deep Research Agent 架构设计与实现指南

## 一、项目概述

### 1.1 背景

[Open Deep Research](https://github.com/langchain-ai/open_deep_research) 是 LangChain 团队开发的开源深度研究智能体，基于 Python 的 LangGraph 框架构建，采用 Supervisor-Researcher 双层 Agent 架构，能够自动搜索网络并生成深度研究报告。

然而，作为一个纯 Python 的 AI Agent 服务，它缺少生产环境所需的企业级特性：用户管理、并发调度、请求队列、流式推送、多实例部署等。本项目设计一个 **Java 网关层**，在不改动原 Agent 的前提下，补齐这些工程能力。

### 1.2 核心思路

```
┌──────────────────────────────────────────────────┐
│                 不要重写 Agent                     │
│            而是用 Java 给它做一层"壳"              │
└──────────────────────────────────────────────────┘
```

**Java 负责**：用户认证、请求调度、并发控制、会话管理、流式转发、多实例路由——这是 Java/Spring 生态最擅长的事。

**Python Agent 负责**：LLM 调用、搜索工具、研究推理、报告生成——这是 Python AI 生态最擅长的事。

两个语言各司其职，通过 HTTP 协议通信，互不侵入。

---

## 二、产品竞争分析：为什么要做这个项目

> 站在竞品视角审视 open_deep_research，识别其工程化短板，明确本项目的差异化价值。

### 2.1 用户侧痛点

| 痛点 | 原项目现状 | 本项目解决方案 |
|------|-----------|-------------|
| **启动门槛高** | 需安装 Python + uv + LangGraph CLI + 配环境变量，Windows 上 GBK 编码报错 | 一个 `docker compose up` 或双击 `start.bat` 启动 |
| **没有自主 UI** | 必须打开 `smith.langchain.com`，数据经过第三方服务器 | 内置 Web UI（Vue/React），离线可用，数据不出本地 |
| **不会导出** | 报告只能从 Studio 里复制粘贴 | 一键导出 Markdown / PDF / Word（`export.bat`） |
| **不可估费** | 提交前不知道会烧多少 token，单次可高达 500 万 | 提交前估算 token 量及费用，执行中实时显示花费 |
| **不能暂停** | 研究跑一半只能等或杀进程，无法中途干预 | 支持暂停/恢复，断点续传 |
| **没有历史对比** | 两次研究结果没法并排比较 | 历史报告列表，支持多报告并排对比 |
| **中文体验差** | 搜索词未做中文优化，搜索结果多为英文 | 自动检测语言，中文问题匹配中文搜索策略 |

### 2.2 架构侧弱点

| 弱点 | 原项目现状 | 本项目解决方案 |
|------|-----------|-------------|
| **搜索太单一** | 仅 Tavily API，无 RAG 能力 | 混合检索：Tavily + RAG（向量数据库 + Embedding） |
| **没有缓存** | 相同 query 重复调用 Tavily，重复计费 | 搜索结果缓存，TTL 可配，相同 query 直接复用 |
| **错误处理粗糙** | token 超限直接暴力截断，丢失上下文 | 自动压缩历史而非截断，优先保留关键信息 |
| **配置散落三处** | `.env` + Studio UI + `configuration.py` | 一个 `config.yml` 集中管理，带中文注释 |
| **MCP 接入复杂** | 需配 URL + Token + OAuth + 工具白名单 | MCP 工具市场，点选即用 |
| **监控缺失** | 没有指标面板，看不出系统健康状态 | Prometheus + Grafana 监控面板 |

### 2.3 Agent 行为缺陷（深度使用后暴露）

| 问题 | 具体表现 | 本项目改进 |
|------|---------|---------|
| **Supervisor 偏保守** | Prompt 里写"偏向单 Agent"，可并行的任务也只派一个 | 自动检测问题可并行度，动态决定并发数 |
| **Researcher 过早停** | 发现 3 个来源就停，复杂问题信息不足 | 根据问题复杂度自适应调整搜索阈值 |
| **反思流于形式** | `think_tool` 只是回显，没有真正的质量检查 | 反思阶段做事实核查，交叉验证来源可信度 |
| **报告结构死板** | 永远是 intro → body → conclusion → sources | 根据问题类型自动选结构（对比表/时间线/FAQ/综述） |
| **追问不够智能** | 默认跳过澄清，用户问"AI 安全"就开始搜 | 默认开启澄清，限制只问 1-2 个关键问题 |
| **并发控制粗放** | 固定并发数，不区分简单/复杂任务 | 根据任务复杂度 + 当前负载动态调节并发 |

### 2.4 竞争定位

| 维度 | open_deep_research | 本项目 |
|------|-------------------|--------|
| **定位** | 开发者工具 / LangChain 生态 Demo | 开箱即用的生产力工具 |
| **目标用户** | 会 Python 的开发者 | 任何想深度调研的人 |
| **上手时间** | 30 分钟+ | 1 分钟（双击启动） |
| **搜索能力** | 单一 Tavily | Tavily + RAG + 中文优化 |
| **单次成本** | ¥0.2-15（波动巨大） | ¥0.1-2（稳定可控） |
| **UI** | 依赖第三方 Studio | 自建 Web UI |
| **导出** | 手动复制粘贴 | 一键 PDF/MD/Word |
| **用户体系** | 无 | JWT 多租户 |
| **部署** | 命令行启动 | Docker Compose 一键部署 |

---

## 三、系统架构

### 2.1 整体架构图

```
                        ┌──────────────┐
                        │   浏览器      │
                        │  (前端 UI)    │
                        └──────┬───────┘
                               │ HTTP / SSE
                               ▼
┌─────────────────────────────────────────────────────────┐
│                   Java 网关层 (Spring Boot 3)             │
│                                                          │
│  ┌──────────┐  ┌───────────┐  ┌────────────────────┐   │
│  │ 认证模块  │  │ 会话管理   │  │  请求队列 + 限流    │   │
│  │ JWT      │  │ thread_id │  │  Semaphore / Queue │   │
│  │ Spring   │  │ 映射管理   │  │  优先级调度        │   │
│  │ Security │  │           │  │                    │   │
│  └──────────┘  └───────────┘  └────────────────────┘   │
│                                                          │
│  ┌──────────┐  ┌───────────┐  ┌────────────────────┐   │
│  │ 路由层    │  │ 流式转发   │  │  多实例负载均衡     │   │
│  │ REST API │  │ WebFlux   │  │  Round-Robin       │   │
│  │ 参数校验  │  │ SSE 推送   │  │  Health Check     │   │
│  └──────────┘  └───────────┘  └────────────────────┘   │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │            LangGraph HTTP Client                  │   │
│  │    - /threads  (创建/恢复会话)                     │   │
│  │    - /runs     (提交研究任务)                     │   │
│  │    - /runs/stream  (SSE 流式获取进度)             │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
└───────────────────────┬─────────────────────────────────┘
                        │  HTTP (内网)
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
   ┌──────────┐  ┌──────────┐  ┌──────────┐
   │ Agent #1 │  │ Agent #2 │  │ Agent #3 │
   │ LangGraph│  │ LangGraph│  │ LangGraph│
   │ Server   │  │ Server   │  │ Server   │
   │ :2024    │  │ :2025    │  │ :2026    │
   │          │  │          │  │          │
   │ Tavily   │  │ Tavily   │  │ Tavily   │
   │ OpenAI   │  │ OpenAI   │  │ OpenAI   │
   └──────────┘  └──────────┘  └──────────┘
        Python 生态（原封不动）
```

### 2.2 一次完整请求的调用链路

```
时间轴 →

① 用户发起请求
   POST /api/research  {"query": "量子计算的最新进展"}
        │
② Java: JWT 认证校验
        │
③ Java: 查找或创建 LangGraph thread_id
        │  (同一个用户在同一个话题下可以继承上下文)
        │
④ Java: 获取信号量许可（限流控制）
        │
⑤ Java: 选择一台 Agent 实例（负载均衡）
        │
⑥ Java → Agent: POST /threads/{tid}/runs  (提交任务)
        │
⑦ Java ← Agent: SSE 事件流（实时进度）
   ┌────┼────────────────────────────────────────┐
   │    │ event: metadata  → "研究任务已拆分"      │
   │    │ event: values    → "Researcher#1 搜索中" │
   │    │ event: values    → "Researcher#2 搜索中" │
   │    │ event: values    → "开始生成最终报告"     │
   │    │ event: values    → "报告生成完成"         │
   └────┼────────────────────────────────────────┘
        │
⑧ Java: SSE → SSE 透传/转换 → 前端
   (可选: 将中间状态存入数据库)
        │
⑨ 前端: 实时展示研究进度，最终渲染 Markdown 报告
```

---

## 四、核心模块设计

### 3.1 认证模块

**技术选型**：Spring Security + JWT

```java
@Configuration
@EnableWebSecurity
public class SecurityConfig {

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) {
        return http
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/api/auth/**").permitAll()
                .anyRequest().authenticated()
            )
            .oauth2ResourceServer(oauth2 -> oauth2
                .jwt(Customizer.withDefaults())
            )
            .build();
    }
}
```

设计要点：
- 登录接口返回 JWT token，后续请求携带 `Authorization: Bearer <token>`
- 每个用户有自己的 `user_id`，用于隔离会话和配额
- 管理员角色可以查看全局统计、管理队列

### 3.2 会话管理模块

LangGraph 使用 `thread_id` 来标识一个对话会话。同一个 thread_id 下的多次请求共享聊天历史。

```java
@Service
public class SessionService {

    // 数据库存储映射关系
    // user_id → topic_id → thread_id

    public String getOrCreateThreadId(Long userId, String topicId) {
        // 查找该用户在该话题下的 thread_id
        // 如果不存在，调用 LangGraph POST /threads 创建新会话
        // 返回 thread_id
    }

    public List<TopicInfo> getUserTopics(Long userId) {
        // 返回该用户的所有历史话题列表
    }
}
```

为什么要做这个映射？

- 原项目每次请求都是独立的 thread，**没有用户概念**
- 网关层补上用户体系，让同一个用户的对话可以延续
- 用户可以看到自己的历史研究记录

### 3.3 并发调度模块

这是 Java 网关最核心的价值所在。

```java
@Service
public class ResearchScheduler {

    // 全局并发数限制，防止 LLM API 被限流
    private final Semaphore globalSemaphore = new Semaphore(20);

    // 每用户并发限制
    private final LoadingCache<Long, Semaphore> perUserSemaphore;

    // 优先级队列（付费用户优先）
    private final PriorityBlockingQueue<ResearchTask> priorityQueue;

    public CompletableFuture<ResearchResult> submit(Long userId, String query) {
        return CompletableFuture.supplyAsync(() -> {
            // 1. 获取用户级许可
            perUserSemaphore.get(userId).acquire();
            try {
                // 2. 获取全局许可
                globalSemaphore.acquire();
                try {
                    // 3. 实际执行研究
                    return executeResearch(userId, query);
                } finally {
                    globalSemaphore.release();
                }
            } finally {
                perUserSemaphore.get(userId).release();
            }
        }, virtualThreadExecutor);  // 使用虚拟线程
    }
}
```

并发控制层级：

```
第一层：用户级 Semaphore (每用户 3 个并发)
    └── 第二层：全局 Semaphore (总计 20 个并发)
            └── 第三层：Agent 实例内部 max_concurrent_research_units (每个请求 5 个)
```

这样三层控制下来，最多有 20 × 5 = 100 个 Researcher 同时工作。

### 3.4 流式转发模块

LangGraph Server 的 `/runs/stream` 接口返回 SSE（Server-Sent Events）格式的实时进度。网关需要把它转发给前端。

```java
@RestController
@RequestMapping("/api/research")
public class ResearchController {

    private final WebClient langGraphClient;

    @GetMapping(value = "/stream/{topicId}", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<ServerSentEvent<String>> streamResearch(
            @PathVariable String topicId,
            @RequestParam String query,
            @AuthenticationPrincipal Jwt jwt) {

        Long userId = Long.valueOf(jwt.getSubject());
        String threadId = sessionService.getOrCreateThreadId(userId, topicId);

        // 向 LangGraph Server 发起流式请求
        return langGraphClient.post()
            .uri("/threads/{threadId}/runs/stream", threadId)
            .bodyValue(Map.of(
                "assistant_id", "deep-researcher",
                "input", Map.of("messages", List.of(
                    Map.of("role", "user", "content", query)
                ))
            ))
            .accept(MediaType.TEXT_EVENT_STREAM)
            .retrieve()
            .bodyToFlux(String.class)
            // 转换为标准 SSE 格式
            .map(data -> ServerSentEvent.builder(data).build())
            // 出错时向前端发送错误事件
            .onErrorResume(e -> Flux.just(
                ServerSentEvent.builder("{\"error\": \"" + e.getMessage() + "\"}")
                    .event("error")
                    .build()
            ));
    }
}
```

### 3.5 多实例负载均衡

当单个 LangGraph Server 不够用时，可以水平扩展：

```java
@Component
public class AgentLoadBalancer {

    // 可用实例列表（可以通过配置中心动态更新）
    private final List<String> instances = List.of(
        "http://agent1.internal:2024",
        "http://agent2.internal:2024",
        "http://agent3.internal:2024"
    );

    private final AtomicInteger roundRobinIndex = new AtomicInteger(0);
    private final WebClient.Builder webClientBuilder;

    public String pickInstance() {
        // Round-Robin 轮询
        int idx = roundRobinIndex.getAndIncrement() % instances.size();
        return instances.get(idx);
    }

    // 可选：健康检查
    @Scheduled(fixedDelay = 30000)
    public void healthCheck() {
        for (String instance : instances) {
            try {
                webClientBuilder.build()
                    .get()
                    .uri(instance + "/ok")
                    .retrieve()
                    .toBodilessEntity()
                    .block(Duration.ofSeconds(5));
                // 标记为健康
            } catch (Exception e) {
                // 标记为不健康，暂时摘除
            }
        }
    }
}
```

---

## 五、技术栈一览

| 层级 | 技术 | 用途 |
|------|------|------|
| **Java 框架** | Spring Boot 3.2+ | 应用框架 |
| **响应式** | Spring WebFlux | 非阻塞 HTTP + SSE 流式推送 |
| **并发** | Java 21 Virtual Threads | 高并发请求处理 |
| **安全** | Spring Security + JWT + OAuth2 | 用户认证与授权 |
| **HTTP 客户端** | WebClient | 调用 LangGraph Server API |
| **数据库** | PostgreSQL / MySQL | 用户、会话、历史记录 |
| **缓存** | Caffeine / Redis | 限流计数、会话缓存 |
| **消息队列** | RabbitMQ / Kafka（可选） | 异步任务队列、削峰 |
| **监控** | Micrometer + Prometheus | 指标采集 |
| **AI Agent** | Python LangGraph Server | 深度研究核心引擎 |

---

## 六、项目结构

```
deep-research-platform/
├── java-gateway/                        # Java 网关（你写的）
│   ├── pom.xml
│   └── src/main/java/com/example/gateway/
│       ├── GatewayApplication.java       # 启动类
│       ├── config/
│       │   ├── SecurityConfig.java       # Spring Security 配置
│       │   ├── ExecutorConfig.java       # 虚拟线程执行器
│       │   └── WebClientConfig.java      # HTTP 客户端配置
│       ├── controller/
│       │   ├── AuthController.java       # 登录/注册
│       │   └── ResearchController.java   # 研究接口（核心）
│       ├── service/
│       │   ├── SessionService.java       # 会话管理
│       │   ├── ResearchScheduler.java    # 并发调度
│       │   └── StreamService.java        # 流式转发
│       ├── client/
│       │   ├── LangGraphClient.java      # LangGraph API 封装
│       │   └── AgentLoadBalancer.java    # 多实例负载均衡
│       ├── model/
│       │   ├── User.java                # 用户实体
│       │   ├── ResearchTask.java         # 研究任务
│       │   └── Topic.java               # 话题实体
│       └── repository/
│           ├── UserRepository.java
│           └── TopicRepository.java
│
├── python-agent/                         # Python Agent（不动）
│   └── (open_deep_research 原项目)
│
├── frontend/                             # 前端 UI（可选）
│   └── (Vue/React，调用 Java 网关的 API)
│
├── docker-compose.yml                    # 一键部署
│   ├── java-gateway (端口 8080)
│   ├── langgraph-server-1 (端口 2024)
│   ├── langgraph-server-2 (端口 2025)
│   ├── postgres (端口 5432)
│   └── redis (端口 6379)
│
└── README.md
```

---

## 七、实现步骤

### 第一阶段：基础打通（1-2 天）

**目标**：Java 能调用 Python Agent，拿到结果。

```
□ 启动 LangGraph Server 本地 (端口 2024)
□ 创建 Spring Boot 3 项目，引入 WebFlux
□ 写一个简单的 LangGraphClient
    - POST /threads             创建会话
    - POST /threads/{id}/runs   提交任务
□ 写一个简单的 ResearchController
    - 接收 query
    - 调用 LangGraphClient
    - 返回最终结果
□ 用 Postman 测试整个链路
```

### 第二阶段：流式进度（1 天）

**目标**：前端能实时看到研究进度。

```
□ 对接 SSE 流式接口 /runs/stream
□ 实现 WebFlux 的 Flux<SSEEvent> 转发
□ 写一个简单的 HTML 页面验证 SSE
```

### 第三阶段：用户体系（1 天）

**目标**：多用户隔离，每个人有自己的会话。

```
□ 集成 Spring Security + JWT
□ 设计用户表、话题表
□ 实现 SessionService（user_id ↔ thread_id 映射）
□ 登录/注册接口
□ 历史记录查询接口
```

### 第四阶段：并发控制（1-2 天）

**目标**：控制并发，防止 LLM API 被打爆。

```
□ 全局 Semaphore 限流
□ 每用户 Semaphore 限流
□ 虚拟线程执行器配置
□ 请求队列（超出并发数时排队而非拒绝）
□ 超时与优雅降级处理
```

### 第五阶段：完善交付（1-2 天）

**目标**：可演示、可部署。

```
□ 多实例负载均衡配置
□ Docker Compose 一键部署脚本
□ README 文档 + 架构图
□ 基础前端页面（或者直接用 Swagger）
□ 简单的错误处理与日志
```

---

## 八、关键设计决策

### 7.1 为什么用 HTTP 而不是 gRPC 或消息队列？

| 方案 | 优点 | 缺点 |
|------|------|------|
| **HTTP**（推荐） | LangGraph Server 原生支持；SSE 流式方便；调试简单 | 同步调用有超时风险 |
| gRPC | 性能好、类型安全 | LangGraph 不原生支持，需要额外适配层 |
| 消息队列 | 解耦彻底、削峰 | 研究任务是实时交互的，用户想等结果 |

建议：**同步请求用 HTTP，如果后续有离线批量研究需求，再引入消息队列。**

### 7.2 虚拟线程 vs 传统线程池

```java
// 传统方式：固定线程池
ExecutorService executor = Executors.newFixedThreadPool(200);
// 问题：200 个请求就把池子占满了，后面的全排队

// 虚拟线程方式：
ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor();
// 优势：可以同时处理上万个请求，每个请求一个虚拟线程
//       虚拟线程非常廉价（几 KB 栈空间），阻塞时让出 CPU
```

对于网关这种 IO 密集型场景（等待 LLM 响应），虚拟线程是完美选择。

### 7.3 限流策略

```
并发数限制（同步）：
  - 全局: Semaphore(20)
  - 每用户: Semaphore(3)

速率限制（异步）：
  - 每分钟 60 个请求（Bucket4j 令牌桶）

组合使用：
  1. 先过速率限制（防止瞬时洪峰）
  2. 再过并发限制（防止累积占用）
```

---

## 九、LangGraph Server API 关键接口

Java 网关需要调用的 LangGraph API：

| 方法 | 路径 | 用途 |
|------|------|------|
| `POST` | `/threads` | 创建新会话 |
| `GET` | `/threads/{id}/state` | 获取会话状态/历史 |
| `POST` | `/threads/{id}/runs` | 提交研究任务（同步等待） |
| `POST` | `/threads/{id}/runs/stream` | 提交研究任务（SSE 流式） |
| `GET` | `/assistants` | 获取可用 Agent 列表 |

示例——创建会话：

```bash
curl -X POST http://127.0.0.1:2024/threads \
  -H "Content-Type: application/json" \
  -d '{}'

# 响应: {"thread_id": "d4f8e2a1-..."}
```

示例——提交研究任务（流式）：

```bash
curl -X POST http://127.0.0.1:2024/threads/{thread_id}/runs/stream \
  -H "Content-Type: application/json" \
  -d '{
    "assistant_id": "deep-researcher",
    "input": {
      "messages": [{"role": "user", "content": "量子计算最新进展"}]
    }
  }'

# 响应: SSE 事件流
```

---

## 十、部署架构

### 开发环境

```
docker compose up

本地访问：
  Java 网关: http://localhost:8080
  LangGraph: http://localhost:2024
  Swagger:   http://localhost:8080/swagger-ui.html
```

### 生产环境

```
                    ┌──────────────┐
                    │  Nginx / CDN │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌─────────┐  ┌─────────┐  ┌─────────┐
        │Gateway#1│  │Gateway#2│  │Gateway#3│   (K8s Pod, 自动扩缩)
        └────┬────┘  └────┬────┘  └────┬────┘
             │            │            │
             └────────────┼────────────┘
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
        ┌─────────┐ ┌─────────┐ ┌─────────┐
        │Agent #1 │ │Agent #2 │ │Agent #3 │   (固定池, GPU 实例)
        └─────────┘ └─────────┘ └─────────┘
```

---

## 十一、总结

这个方案的核心哲学是 **"各司其职"**：

| | Java 擅长 | Python 擅长 |
|---|---|---|
| **能力** | 企业级工程、并发控制、Web 服务 | AI/LLM 生态、LangChain、数据处理 |
| **在项目中的角色** | 网关、调度器、门面 | 核心引擎，原封不动 |
| **代码量** | ~2000 行 | 0 行改动 |

这样的项目写进简历，展示的是：

1. **架构设计能力**：你懂得如何为 AI 服务设计工程化的网关层
2. **并发编程功底**：虚拟线程、Semaphore、SSE 流式处理
3. **技术视野**：Java + Python 混合架构，而不是固守单一语言
4. **工程化思维**：不是"写个 Demo"，而是考虑了认证、限流、负载均衡、水平扩展

这就是一个可以拿到面试中去深入聊的项目。
