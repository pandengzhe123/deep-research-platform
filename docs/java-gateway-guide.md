# Java 网关 + Python Agent 架构设计与实现指南

> 更新于 2026-06-03，反映实际项目状态。

## 一、项目概述

### 1.1 背景

本项目由两个子系统组成：

| 子系统 | 语言 | 框架 | 端口 | 职责 |
|--------|------|------|------|------|
| **Python Agent** | Python 3.11 | FastAPI + OpenAI SDK | `:8000` | LLM 推理、搜索、报告生成 |
| **Java 网关** | Java 21 | Spring Boot 3 + WebFlux | `:8080` | 用户接入、并发控制、SSE 转发 |

Python Agent 是自己从零写的（借鉴 open_deep_research 架构，不依赖 LangChain/LangGraph）。Java 网关则给它加一层企业级的"壳"。

### 1.2 核心思路

```
┌──────────────────────────────────────────────────┐
│              不要重写 Agent                        │
│          而是用 Java 给它做一层"壳"                 │
└──────────────────────────────────────────────────┘
```

**Python 负责**：LLM 调用、Tavily 搜索、Agent 循环、报告生成 —— **AI 核心逻辑**。

**Java 负责**：用户接入、请求调度、SSE 转发、会话管理、限流 —— **工程化外壳**。

两个进程通过 HTTP/SSE 通信，部署时各自独立容器。

---

## 二、产品竞争分析：为什么要做这个项目

> 站在竞品视角审视 open_deep_research，识别其工程化短板，明确本项目的差异化价值。

### 2.1 用户侧痛点

| 痛点 | 原项目现状 | 本项目解决方案 |
|------|-----------|-------------|
| **启动门槛高** | 需安装 Python + uv + LangGraph CLI + 配环境变量，Windows 上 GBK 编码报错 | `start.bat` 双击启动，或 `docker compose up` |
| **没有自主 UI** | 必须打开 `smith.langchain.com`，数据经过第三方服务器 | 内置 Web UI，离线可用，数据不出本地 |
| **不会导出** | 报告只能从 Studio 里复制粘贴 | 一键导出 Markdown / PDF / Word |
| **不可估费** | 提交前不知道会烧多少 token，单次可高达 500 万 | 提交前估算 token 量，执行中实时显示花费 |
| **不能暂停** | 研究跑一半只能等或杀进程，无法中途干预 | API 支持取消任务（`DELETE /research/{id}`） |
| **没有历史对比** | 两次研究结果没法并排比较 | 历史报告列表，支持多报告并排对比 |
| **中文体验差** | 搜索词未做中文优化，搜索结果多为英文 | 自动检测语言，中文问题匹配中文搜索策略 |

### 2.2 架构侧弱点

| 弱点 | 原项目现状 | 本项目解决方案 |
|------|-----------|-------------|
| **搜索太单一** | 仅 Tavily API，无 RAG 能力 | 混合检索：Tavily + RAG（向量数据库 + Embedding） |
| **没有缓存** | 相同 query 重复调用 Tavily，重复计费 | 搜索结果缓存，TTL 可配，相同 query 直接复用 |
| **错误处理粗糙** | token 超限直接暴力截断，丢失上下文 | 自动压缩历史而非截断，优先保留关键信息 |
| **配置散落三处** | `.env` + Studio UI + `configuration.py` | 一个 `config.py` 集中管理 |
| **监控缺失** | 没有指标面板，看不出系统健康状态 | Prometheus + Grafana 监控面板 |
| **依赖笨重** | 必须依赖 LangChain + LangGraph 全家桶 | Python Agent 只依赖 `openai` + `fastapi` + `tavily-python`，极简 |

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

## 三、当前系统架构

### 3.1 整体架构图

```
                     ┌──────────────┐
                     │   浏览器       │
                     │  (前端 UI)     │
                     └──────┬───────┘
                            │ HTTP / SSE
                            ▼
┌─────────────────────────────────────────────────────────┐
│              Java 网关 (Spring Boot 3 + WebFlux)          │
│                       localhost:8080                      │
│                                                          │
│  POST /api/research          同步研究                     │
│  POST /api/research/stream   SSE 流式（实时进度）          │
│  GET  /api/sessions          历史记录                     │
│  GET  /api/sessions/{id}     查看报告                     │
│  DELETE /api/research/{id}   取消任务                     │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │  内部组件                                         │   │
│  │  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │   │
│  │  │AgentClient│  │SessionSvc│  │Semaphore(20)  │  │   │
│  │  │(WebClient)│  │(H2/MySQL)│  │并发控制        │  │   │
│  │  └──────────┘  └──────────┘  └───────────────┘  │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
└───────────────────────┬─────────────────────────────────┘
                        │  HTTP + SSE（内网）
                        ▼
┌─────────────────────────────────────────────────────────┐
│            Python Agent (FastAPI + Tavily)                │
│                    localhost:8000                         │
│                                                          │
│  GET  /health                 健康检查                    │
│  POST /research               同步研究                    │
│  POST /research/stream        SSE 流式                    │
│  DELETE /research/{id}        取消任务                    │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Level 1 Agent: 分析 → 搜索 → 报告                 │   │
│  │  Level 2 Agent: 搜索 → 反思 → 再搜索 → ... → 报告  │   │
│  │  Level 3 Agent: 多路并行搜索 (TODO)                │   │
│  │  Level 4 Agent: Supervisor-Researcher (TODO)      │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

### 3.2 一次完整请求的调用链路

```
① 浏览器 POST /api/research/stream
   {"question": "量子计算最新进展", "level": 2}
        │
② Java: 创建会话、分配虚拟线程
        │
③ Java → Py: POST /research/stream
   WebClient 发起请求，accept SSE
        │
④ Py: run_agent_with_sse()
   ┌────┼──────────────────────────────────────────────┐
   │    │ SSE: {"step":"planning","message":"分析问题中"}│
   │    │ SSE: {"step":"searching","message":"搜索中"}   │
   │    │ SSE: {"step":"thinking","message":"反思中"}    │
   │    │ SSE: {"step":"reporting","message":"写报告"}    │
   │    │ SSE: {"event":"done","data":{"report":"..."}} │
   └────┼──────────────────────────────────────────────┘
        │
⑤ Java: Flux<String> 逐条转发 SSE 给浏览器
   （可选：存入数据库）
        │
⑥ 浏览器: 实时显示进度，最后渲染 Markdown 报告
```

---

## 四、核心模块设计

### 4.1 AgentClient —— 封装对 Python 的 HTTP 调用

```java
@Service
public class AgentClient {

    private final WebClient client;

    public AgentClient(@Value("${agent.url}") String agentUrl) {
        this.client = WebClient.builder()
            .baseUrl(agentUrl) // http://localhost:8000
            .build();
    }

    /**
     * 同步调用 —— 等 Agent 完全跑完再返回。
     */
    public ResearchResponse research(String question, int level) {
        return client.post()
            .uri("/research")
            .bodyValue(Map.of("question", question, "level", level))
            .retrieve()
            .bodyToMono(ResearchResponse.class)
            .block(Duration.ofMinutes(5));  // Agent 可能跑几分钟
    }

    /**
     * SSE 流式 —— 返回 Flux，网关不做缓冲，原样转发。
     */
    public Flux<String> researchStream(String question, int level) {
        return client.post()
            .uri("/research/stream")
            .bodyValue(Map.of("question", question, "level", level))
            .accept(MediaType.TEXT_EVENT_STREAM)
            .retrieve()
            .bodyToFlux(String.class);
    }

    /**
     * 健康检查 —— 网关启动时 / 定时探测 Agent 是否存活。
     */
    public boolean isHealthy() {
        try {
            return Boolean.TRUE.equals(
                client.get().uri("/health")
                    .retrieve()
                    .bodyToMono(Map.class)
                    .map(m -> "ok".equals(m.get("status")))
                    .block(Duration.ofSeconds(3))
            );
        } catch (Exception e) {
            return false;
        }
    }
}
```

### 4.2 ResearchController —— 前端调用的 REST 接口

```java
@RestController
@RequestMapping("/api")
public class ResearchController {

    private final AgentClient agentClient;

    /**
     * 同步 —— 等结果。
     */
    @PostMapping("/research")
    public Mono<ResponseEntity<ResearchResponse>> research(@RequestBody ResearchRequest req) {
        return Mono.fromCallable(() ->
            ResponseEntity.ok(agentClient.research(req.question(), req.level()))
        ).subscribeOn(Schedulers.boundedElastic());
    }

    /**
     * 流式 —— 实时 SSE，核心接口。
     */
    @PostMapping(value = "/research/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public Flux<String> researchStream(@RequestBody ResearchRequest req) {
        return agentClient.researchStream(req.question(), req.level());
    }
}
```

### 4.3 会话管理

```java
@Service
public class SessionService {

    // 轻量实现：内存存储
    private final ConcurrentHashMap<String, ResearchSession> sessions = new ConcurrentHashMap<>();

    public ResearchSession createSession(String userId, String question) {
        String id = UUID.randomUUID().toString().substring(0, 8);
        ResearchSession session = new ResearchSession(id, userId, question);
        sessions.put(id, session);
        return session;
    }

    public void appendReport(String sessionId, String report) {
        ResearchSession session = sessions.get(sessionId);
        if (session != null) session.setReport(report);
    }

    public List<ResearchSession> getUserSessions(String userId) {
        return sessions.values().stream()
            .filter(s -> s.getUserId().equals(userId))
            .toList();
    }
}
```

### 4.4 并发控制

```java
@Service
public class ResearchScheduler {

    // 全局最多 20 个并发研究任务（保护 Python Agent 和 LLM API）
    private final Semaphore semaphore = new Semaphore(20);

    public <T> T execute(Supplier<T> task) throws InterruptedException {
        semaphore.acquire();
        try {
            return task.get();
        } finally {
            semaphore.release();
        }
    }
}
```

### 4.5 超时处理

```java
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(TimeoutException.class)
    public ResponseEntity<Map<String, String>> handleTimeout(TimeoutException e) {
        return ResponseEntity
            .status(504)
            .body(Map.of("error", "研究超时，请简化问题或稍后重试"));
    }
}
```

---

## 五、Java 网关文件清单

```
java-gateway/
├── pom.xml                                   Maven 配置
│   └── 依赖: spring-boot-starter-webflux
│
├── src/main/resources/application.yml
│   agent.url: http://localhost:8000
│   server.port: 8080
│
└── src/main/java/com/deepresearch/gateway/
    ├── GatewayApplication.java               @SpringBootApplication
    ├── config/
    │   └── WebClientConfig.java              WebClient Bean
    ├── controller/
    │   └── ResearchController.java           REST 接口
    ├── service/
    │   ├── AgentClient.java                  Python HTTP 客户端
    │   ├── SessionService.java               会话管理
    │   └── ResearchScheduler.java            并发控制
    └── model/
        └── ResearchModels.java               Request/Response 记录类
```

总共 **8 个文件**，依赖仅 `spring-boot-starter-webflux`，极其干净。

---

## 六、实现步骤

### 第一阶段：基础打通（30 分钟）

**目标**：Java 成功调用 Python Agent，拿到报告。

```
□ mvn archetype 创建 Spring Boot 项目
□ 加 spring-boot-starter-webflux 依赖
□ 写 WebClientConfig（一个 Bean）
□ 写 AgentClient.research()
□ 写 ResearchController（一个 POST）
□ 启动 Python Agent（start.bat）
□ 启动 Java 网关
□ 用 curl 测试：POST /api/research
```

### 第二阶段：SSE 流式（20 分钟）

**目标**：前端实时看到研究进度。

```
□ 写 AgentClient.researchStream()（返回 Flux<String>）
□ 写 ResearchController.researchStream()（produces = TEXT_EVENT_STREAM）
□ 写一个 static HTML 页面用 EventSource 消费
□ 验证：进度一条条弹出来
```

### 第三阶段：完善（1-2 小时）

```
□ SessionService（内存/H2 存储）
□ ResearchScheduler（Semaphore 限流）
□ 超时处理（@ControllerAdvice）
□ 全局异常处理
□ 启动脚本
```

---

## 七、关键设计决策

### 7.1 为什么是 HTTP + SSE，不是 WebSocket？

| 场景 | 方案 |
|------|------|
| 进度推送（单向，服务器→客户端） | **SSE** —— 更简单，浏览器原生 `EventSource` 支持 |
| 双向实时通信 | WebSocket |

研究进度是纯单向的推送，SSE 正好，没到需要 WebSocket 的地步。

### 7.2 为什么是 WebFlux，不是 Tomcat？

一个研究任务要跑 1-3 分钟，传统 Tomcat（一个请求占一个线程）很快就会被堵满。WebFlux 响应式模型不占用线程等待 I/O，一条线程可以同时处理几百个连接。

### 7.3 为什么请求体设计这么简单？

```json
{"question": "...", "level": 2}
```

Python Agent 的接口就是这两个字段。Java 网关不增加复杂度——原样透传，Java 只负责"接客"，不改"内容"。

---

## 八、Python ↔ Java 通信契约

### Python 提供的接口（`http://localhost:8000`）

| 方法 | 路径 | 请求 | 响应 |
|------|------|------|------|
| `GET` | `/health` | 无 | `{"status":"ok","model":"deepseek-v4-flash"}` |
| `POST` | `/research` | `{"question":"...","level":2}` | `{"report":"# 报告...","language":"auto"}` |
| `POST` | `/research/stream` | 同上 | SSE 事件流（见下方格式） |
| `DELETE` | `/research/{id}` | 无 | `{"status":"cancelled"}` |

### SSE 事件格式

```
event: status
data: {"step":"planning","message":"分析问题中"}

event: status
data: {"step":"searching","message":"搜索: 量子计算进展","round":1}

event: status
data: {"step":"thinking","message":"信息充足，停止搜索","round":2}

event: status
data: {"step":"reporting","message":"写报告中"}

event: done
data: {"report":"# 研究报告\n\n...","language":"auto"}
```

Java 网关**不解析** SSE 内容，只做 `Flux<String>` 透明转发——保持解耦。

---

## 九、部署

### 开发环境

```bash
# 终端 1：启动 Python Agent
cd agent && start.bat       # → localhost:8000

# 终端 2：启动 Java 网关
cd java-gateway && mvn spring-boot:run   # → localhost:8080

# 浏览器打开
http://localhost:8080
```

### 生产环境

```
docker compose up
  ├── agent          (Python, :8000)
  ├── java-gateway   (Java 21, :8080)
  └── (可选) postgres (会话存储)
```

---

## 十、总结

| | Java 侧 | Python 侧 |
|---|---|---|
| **代码量** | ~400 行（8 个文件） | ~600 行（6 个文件） |
| **关注点** | 认证、限流、分发、转发 | LLM、搜索、Agent 逻辑 |
| **改动频率** | 低（基础设施） | 高（模型调优、加工具） |

这个架构的核心价值在于：**Agent 逻辑和工程外壳完全解耦**，两边可以独立迭代、独立部署。面试时你把 Python Agent 的 Level 1→4 渐进演进和 Java 网关的架构设计放在一起讲，展现的是"能写核心逻辑也能做系统工程"的复合能力。
