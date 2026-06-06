# 我的项目 vs open_deep_research 代码层面对比

## 文件映射

| 我的文件 | 原项目对应 | 我的行数 | 原项目行数 |
|---------|-----------|---------|----------|
| `agent/src/researcher/llm.py` | `deep_researcher.py`(模型部分) + `configuration.py` + `utils.py`(API key) | 97 | ~100 |
| `agent/src/researcher/search.py` | `utils.py`(tavily_search + summarize_webpage) | 175 | ~230 |
| `agent/src/researcher/agent.py` | `deep_researcher.py`(主图+子图) + `state.py` + `prompts.py` | 750+ | ~1500 |
| `agent/src/researcher/server.py` | `langgraph.json` + LangGraph Server | 470 | ~20 |
| `agent/src/researcher/config.py` | `configuration.py` | 33 | 235 |
| **Python 合计** | | **~1550** | **~2100** |
| `java-gateway/` (8 个文件) | 无 | 492 | 0 |
| `index.html` | LangGraph Studio UI | 143 | 0 |

---

## 逐层对比

### 1. LLM 调用

```
原项目                              我的
──────                              ──

① 声明式创建                        ① 显式创建
configurable_model =                 self.client = OpenAI(
  init_chat_model(                       api_key=...,
    configurable_fields=(                base_url="https://api.deepseek.com"
      "model","max_tokens","api_key"  )
  )

② 运行时配置注入                     ② 方法参数直传
configurable_model                   def chat_with_tools(
  .with_config({                         self, ..., tools=[...]
    "model": "gpt-4.1",              ):
    ...                                  return self.client
  })                                       .chat.completions.create(
  .bind_tools(tools)                           model=self.model,
                                                messages=messages,
③ 框架自动选供应商（openai:/deepseek:/anthropic:）  tools=tools
→ get_api_key_for_model()                     )
→ ChatOpenAI / ChatAnthropic / ChatDeepSeek

                                        ③ 固定 DeepSeek，不做供应商路由
```

**省了什么**：框架抽象层（init_chat_model、with_config、bind_tools）全部去掉，直接调 OpenAI Python SDK。

### 2. Agent 循环

```
原项目                              我的
──────                              ──

① LangGraph StateGraph 编排          ① Python while/for 循环
                                       for round_num in range(1, max+1):
② 节点 = 函数 + 状态转换规则               msg = llm.chat_with_tools(...)
supervisor_builder = StateGraph(...)
supervisor_builder.add_node(              if not msg.tool_calls:
  "supervisor", supervisor)                  break  ← 退出
supervisor_builder.add_edge(
  START, "supervisor")                    for tc in msg.tool_calls:
supervisor_subgraph =                        if name == "search":
  supervisor_builder.compile()                  result = await search(...)
supervisor_subgraph.ainvoke(state)              messages.append(...)

③ 子图递归编译（子 Agent 也是 StateGraph）
researcher_subgraph = researcher_builder.compile()
→ supervisor_tools() 里调 researcher_subgraph.ainvoke()
```

**省了什么**：LangGraph 的 StateGraph、add_node、add_edge、compile、子图嵌套全部去掉。

### 3. 状态管理

```
原项目                              我的
──────                              ──

① 显式 State 定义                    ① 无 State 对象
class AgentState(MessagesState):
    supervisor_messages: ...         messages: list[dict] = [...]  Python list
    research_brief: Optional[str]    all_findings: list[str] = []   Python list
    notes: list[str]                 round_num = 1                   Python int
    raw_notes: list[str]

② override_reducer 自动合并          ② 手动 append
def override_reducer(current, new):   messages.append(
    if new["type"] == "override":         {"role": "tool", "content": ...}
        return new["value"]           )
    return current + new

③ 框架保证状态流转                    ③ 函数参数/返回值传递
State → node1 → 新State → node2      result = await agent.run(topic)
```

**省了什么**：Pydantic State 类、reducer 合并逻辑、框架自动流转。

### 4. 工具定义

```
原项目                              我的
──────                              ──

① Pydantic BaseModel                ① 手写 JSON dict
class ConductResearch(BaseModel):    {
    research_topic: str                  "type": "function",
                                         "function": {
② @tool 装饰器                               "name": "search",
@tool                                    "parameters": {
def think_tool(reflection):                  "properties": {
    return f"Reflection: {reflection}"           "queries": {"type": "array"}
                                             }
                                         }

③ 工具执行：LangChain 自动路由         ③ 工具执行：if/elif 手动路由
→ .bind_tools() → LLM 调用             for tc in msg.tool_calls:
→ .ainvoke() → 框架执行                    if name == "search": result = await ...
```

**省了什么**：Pydantic 模型定义、`@tool` 装饰器、框架自动路由。

### 5. 搜索流程

```
原项目                              我的
──────                              ──

① Tavily API                        ① 完全相同 → Tavily API
② URL 去重                           ② 完全相同 → URL 去重
③ LLM 摘要（with_structured_output）   ③ 完全相同（structured_output）
   用 Pydantic Summary 模型              手写 JSON schema
④ 格式化返回                          ④ 格式化返回

几乎一模一样，只是类型定义方式不同。
```

### 6. 并行调度

```
原项目                              我的
──────                              ──

asyncio.gather(                     asyncio.gather(
    *[                                 *[
        researcher_subgraph                Level2Agent().run(topic)
        .ainvoke(state, config)            for topic in sub_topics
        for tool_call in calls         ]
    ]                               )
)
```

**完全一样——都是 Python `asyncio.gather`**。只是原项目通过子图调用，你是直接函数调用。

### 7. 流式推送

```
原项目                              我的
──────                              ──

LangGraph Server 内置 SSE             FastAPI + sse-starlite 手动实现
→ /runs/stream                        → /research/stream
→ StreamMode 自动切换                  → 手动 yield SSE 事件
→ 0 行代码                             → 200 行代码
```

**你多写了**：原项目 LangGraph Server 自带 SSE，你要自己做。但这正是你的亮点——你会做流式。

---

## 你没有的

| 功能 | 原项目代码位置 | 你 |
|------|-------------|-----|
| 用户澄清 | `clarify_with_user()` | ✅ 浏览器+命令行双入口 |
| 研究简报 | `write_research_brief()` | ✅ 合并进规划步骤 |
| 压缩研究 | `compress_research()` | ✅ Level 2 内置 |
| Token 超限处理 | `is_token_limit_exceeded()` + 渐进截断 | ❌ |
| MCP 集成 | `load_mcp_tools()` + OAuth | ❌ |
| 多模型供应商 | `get_api_key_for_model()` 8 种 | ❌ 只有 DeepSeek |
| 配置 UI | LangGraph Studio | ❌ 只有 .env |
| 评估系统 | `tests/` 全套 | ❌ |

---

## 你有但原项目没有的

| 功能 | 你的代码 | 原项目 |
|------|---------|--------|
| Level 1 极速模式 | `FastLevel1Agent`，1 次 LLM | ❌ |
| Level 1-4 渐进演进 | 每层独立可跑 | ❌ 直接拉满 |
| Java 全栈网关 | 8 文件，Spring Boot | ❌ |
| Web UI | `index.html`，计时器 | 依赖外部 Studio |
| 搜索自动降级 | Tavily→DuckDuckGo | ❌ |
| 架构重构 | server.py 零 Agent 逻辑 | 强依赖 LangChain/LangGraph |
| 纯 API 实现 | 0 框架依赖 | LangGraph StateGraph |
| 完整面试准备 | 24 问 Q&A、架构评审、代码对照 | ❌ |

---

## 结论

核心系统层面的 DNA 完全一致——双层 Agent 循环、Function Calling、并行分派、压缩、报告生成。LangChain/LangGraph 本质上是中间一层胶水代码，你把胶水拆了，直接用手拼的。
