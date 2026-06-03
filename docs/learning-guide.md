# 🔬 Open Deep Research 源码学习指南

> 目标：理解这个项目的架构设计，具备自己模仿开发的能力。

---

## 一、先理解架构全貌（10 分钟）

### 1.1 这张图记在心里

```
用户输入（一段话）
    │
    ▼
┌──────────────────────────────────────┐
│  ① clarify_with_user                 │  ← LLM 判断：需要追问吗？
│     输出：要么追问用户，要么继续       │
└──────────────┬───────────────────────┘
               │ 不需要追问
               ▼
┌──────────────────────────────────────┐
│  ② write_research_brief              │  ← LLM 把用户问题转成研究简报
│     输出：research_brief（一段话）    │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  ③ research_supervisor（子图）        │  ← 核心！双层 Agent
│                                      │
│  ┌────────────────────────────┐      │
│  │  Supervisor（监督者）       │      │  ← LLM + 2 个工具
│  │  工具：ConductResearch      │      │
│  │        ResearchComplete     │      │
│  │        think_tool           │      │
│  │                            │      │
│  │  循环：规划 → 派活 → 看结果  │      │
│  │       → 再规划 → 再派活     │      │
│  └──────┬─────────┬───────────┘      │
│         │         │                  │
│    ┌────▼──┐  ┌───▼───┐             │
│    │Researcher│Researcher│  ← 可并行N个│
│    │  #1    │  │  #2   │             │
│    │        │  │       │             │
│    │ 工具： │  │ 工具： │             │
│    │ web_search│web_search│          │
│    │ think_tool│think_tool│          │
│    │ MCP工具  │  │ MCP工具│          │
│    │        │  │       │             │
│    │ 循环： │  │ 循环： │             │
│    │ 搜索→反思│  │ 搜索→反思│          │
│    │ →再搜索 │  │ →再搜索│           │
│    └───┬───┘  └───┬───┘             │
│        │          │                  │
│    ┌───▼──────────▼───┐             │
│    │  compress_research│  ← 压缩整理  │
│    └──────────────────┘             │
└──────────────┬───────────────────────┘
               │ 所有研究结果汇总
               ▼
┌──────────────────────────────────────┐
│  ④ final_report_generation           │  ← LLM 写最终报告
│     输出：Markdown 报告 + 引用        │
└──────────────────────────────────────┘
```

### 1.2 核心设计模式：双层 Agent 循环

整个系统的精髓就两个循环，理解了这两个循环，你就理解了整个项目：

**外层：Supervisor 循环**

```
Supervisor 拿到研究简报
  ↓
think_tool("我该搜什么？拆成几个子任务？")
  ↓
ConductResearch("任务A") + ConductResearch("任务B")  ← 并行派发
  ↓
等 Researcher 们返回结果
  ↓
think_tool("信息够了吗？还缺什么？")
  ↓
如果够了 → ResearchComplete
如果不够 → 再派一轮 ConductResearch
```

**内层：Researcher 循环**

```
Researcher 拿到一个研究主题
  ↓
web_search("搜索词1", "搜索词2")
  ↓
think_tool("搜到了什么？还缺什么？")
  ↓
web_search("更精准的搜索词3")
  ↓
think_tool("信息够了")
  ↓
调用 compress_research 把结果整理好
  ↓
返回给 Supervisor
```

### 1.3 数据流

所有数据通过 **State（状态）** 在节点间流转：

```
AgentState（全局状态）
├── messages           ← 用户输入 + 最终报告
├── research_brief     ← 从用户问题提炼的研究简报
├── supervisor_messages ← Supervisor 的对话历史
├── notes              ← 所有 Researcher 返回的压缩结果
└── final_report       ← 最终报告

SupervisorState（监督者子图状态）
├── supervisor_messages
├── research_brief
├── notes
└── research_iterations  ← 控制循环上限

ResearcherState（研究员子图状态）
├── researcher_messages   ← Researcher 的搜索对话历史
├── research_topic        ← 被分配的研究主题
├── tool_call_iterations  ← 控制循环上限
├── compressed_research   ← 压缩后的研究成果
└── raw_notes             ← 原始搜索结果
```

---

## 二、逐文件阅读指南（按依赖顺序）

### 第 1 步：`state.py`（5 分钟）

这是整个系统的"数据字典"。打开它，关注三样东西：

**A. 结构化输出（Structured Output）**
```python
class ConductResearch(BaseModel):
    research_topic: str   # Supervisor 用这个结构来"调用工具"

class ResearchComplete(BaseModel):
    pass   # 空结构，只是一个信号：我完成了
```
这些都是 LLM 的 Function Calling 定义。LLM 不直接写代码调用函数，而是输出 JSON，LangChain 把它转成函数调用。

**B. 状态定义（State）**
```python
class AgentState(MessagesState):    # 继承 MessagesState = 自带 messages 字段
    supervisor_messages: ...        # 用 override_reducer 的字段
    research_brief: Optional[str]
    notes: ...                      # 用 override_reducer 的字段
    final_report: str
```

**C. override_reducer（关键！）**
```python
def override_reducer(current_value, new_value):
    if isinstance(new_value, dict) and new_value.get("type") == "override":
        return new_value.get("value", new_value)  # 覆盖
    else:
        return operator.add(current_value, new_value)  # 追加
```
这是 LangGraph 状态管理的一个技巧：大部分时候消息是追加（add），但某些节点需要**完全替换**之前的内容（比如初始化 supervisor_messages 时）。

### 第 2 步：`configuration.py`（3 分钟）

纯配置类，用 Pydantic 定义所有可调参数。核心字段：

| 字段 | 默认值 | 作用 |
|------|--------|------|
| `research_model` | `openai:gpt-4.1` | 驱动 Supervisor + Researcher |
| `summarization_model` | `openai:gpt-4.1-mini` | 摘要搜索结果 |
| `compression_model` | `openai:gpt-4.1` | 压缩研究发现 |
| `final_report_model` | `openai:gpt-4.1` | 写最终报告 |
| `max_concurrent_research_units` | 5 | 最多同时几个 Researcher |
| `max_researcher_iterations` | 6 | Supervisor 最多循环几轮 |
| `max_react_tool_calls` | 10 | 单个 Researcher 最多搜索几次 |
| `search_api` | `tavily` | 用什么搜索引擎 |

`from_runnable_config()` 方法的逻辑：先从 config 字典取值，取不到就从环境变量取。这就是为什么我们在 `.env` 里设 `RESEARCH_MODEL=...` 就能生效。

### 第 3 步：`prompts.py`（10 分钟）

这是整个系统的"灵魂"。重点看这几个 prompt：

**A. `research_system_prompt`** — Researcher 的行为指南

核心约束：
- "用 2-5 次搜索就停"
- "每次搜索后用 think_tool 反思"
- "有 3+ 相关来源就够"
- "上次搜索和前一次返回相似信息就停"

**B. `lead_researcher_prompt`** — Supervisor 的行为指南

核心约束：
- "倾向于用单个 Researcher，除非任务明显可并行"
- "每个 ConductResearch 要写完整的、自包含的指令"
- "最多 {max_researcher_iterations} 轮"
- "每次 ConductResearch 前后都要 think_tool"

**C. `compress_research_system_prompt`** — 压缩研究成果

关键指令：不总结、不改写，只清理格式。保留所有信息 verbatim（逐字保留）。

**D. `final_report_generation_prompt`** — 写最终报告

约束：输出语言要和用户输入语言一致（"如果用户用中文问，你就用中文答"）。

### 第 4 步：`utils.py`（15 分钟）

工具函数集合，重点看这几个：

**A. `tavily_search()`** — 搜索 + 摘要流水线

```python
@tool
async def tavily_search(queries, max_results=5, ...):
    # 1. 并行搜索
    search_results = await tavily_search_async(queries, ...)
    # 2. 按 URL 去重
    unique_results = {}
    # 3. 对每个网页内容调用 LLM 摘要
    summaries = await asyncio.gather(*summarization_tasks)
    # 4. 格式化输出
    return formatted_output
```

注意这里的设计：搜索结果先去重，然后对每个网页用 LLM 做摘要（并行），避免把几万字的网页原文直接塞给 Researcher。

**B. `get_all_tools()`** — 工具注册中心

```python
async def get_all_tools(config):
    tools = [tool(ResearchComplete), think_tool]      # 固定工具
    search_tools = await get_search_tool(search_api)   # 搜索工具（Tavily/OpenAI/Anthropic）
    mcp_tools = await load_mcp_tools(config, ...)      # MCP 外部工具
    tools.extend(search_tools)
    tools.extend(mcp_tools)
    return tools
```

这就是 Researcher 能用的全部工具箱。

**C. `think_tool`** — 不是真工具，是"慢下来思考"

```python
@tool
def think_tool(reflection: str) -> str:
    return f"Reflection recorded: {reflection}"
```

这个设计很巧妙：它就是一个空操作，只是给 LLM 一个"停下来反思"的借口。如果不给 LLM 这个工具，它会一口气搜下去停不下来。

**D. `is_token_limit_exceeded()`** — 多供应商 token 超限检测

```python
def is_token_limit_exceeded(exception, model_name):
    # 根据 model_name 前缀判断供应商
    # 分别检查 OpenAI / Anthropic / Google 的错误格式
```

### 第 5 步：`deep_researcher.py`（30 分钟）

这是主文件，按节点阅读：

**A. 模型初始化（全局）**

```python
configurable_model = init_chat_model(
    configurable_fields=("model", "max_tokens", "api_key", "extra_body"),
)
```

`init_chat_model` 是 LangChain 的"万能模型工厂"。根据 `model` 字符串的前缀自动选择供应商：
- `openai:gpt-4.1` → ChatOpenAI
- `anthropic:claude-sonnet-4` → ChatAnthropic
- `deepseek:deepseek-v4-flash` → ChatDeepSeek

**B. `clarify_with_user()`** — 澄清节点

```
用户消息 → LLM 判断是否需要追问 → 追问 / 继续
```

关键代码：
```python
clarification_model = (
    configurable_model
    .with_structured_output(ClarifyWithUser)  # 强制 LLM 输出 JSON
    .with_retry(stop_after_attempt=3)         # 重试
    .with_config(model_config)                 # 注入模型参数
)
```

`with_structured_output(ClarifyWithUser)` 这行等价于告诉 LLM："你只能返回这个 JSON 结构"：
```json
{"need_clarification": true/false, "question": "...", "verification": "..."}
```

**C. `write_research_brief()`** — 简报节点

把用户消息转成一段详细的研究简报。用的也是 `with_structured_output(ResearchQuestion)`。

**D. `supervisor()` + `supervisor_tools()`** — 外层循环核心

```
supervisor()  →  LLM 决策（该搜什么？）
     ↓
supervisor_tools()  →  执行决策（并行启动 Researcher）
     ↓
supervisor()  →  LLM 看结果再决策（信息够了吗？）
     ↓
...循环直到 ResearchComplete 或超限...
```

**supervisor_tools() 的关键并行逻辑：**
```python
# 并行启动 Researcher
research_tasks = [
    researcher_subgraph.ainvoke({
        "researcher_messages": [HumanMessage(content=topic)],
        "research_topic": topic,
    }, config)
    for tool_call in allowed_conduct_research_calls
]
tool_results = await asyncio.gather(*research_tasks)
```

`researcher_subgraph` 是一个编译好的子图（见下面），每次 `.ainvoke()` 就启动一个完整的 Researcher 流程。

**E. `researcher()` + `researcher_tools()`** — 内层循环核心

```
researcher()  →  LLM 决策（该搜什么？）
     ↓
researcher_tools()  →  并行执行所有工具调用
     ↓
researcher()  →  LLM 看搜索结果再决策
     ↓
...循环直到 compress_research...
```

**researcher_tools() 的并行工具执行：**
```python
# 所有工具调用并行执行（比如一次搜 3 个 query）
tool_execution_tasks = [
    execute_tool_safely(tools_by_name[tc["name"]], tc["args"], config)
    for tc in tool_calls
]
observations = await asyncio.gather(*tool_execution_tasks)
```

**F. `compress_research()`** — 研究成果压缩

把 Researcher 的全部对话历史（搜索调用 + 搜索结果 + AI 思考）压缩成一篇整洁的研究报告。这是每个 Researcher 的最后一个步骤。

**G. `final_report_generation()`** — 最终报告生成

收集所有 Researcher 的 `compressed_research` 输出，汇总成一篇最终报告。有 token 超限重试机制（渐进式截断）。

**H. 子图编译（关键！）**

```python
# Researcher 子图
researcher_builder = StateGraph(ResearcherState, output=ResearcherOutputState, ...)
researcher_builder.add_node("researcher", researcher)
researcher_builder.add_node("researcher_tools", researcher_tools)
researcher_builder.add_node("compress_research", compress_research)
researcher_builder.add_edge(START, "researcher")
researcher_builder.add_edge("compress_research", END)
researcher_subgraph = researcher_builder.compile()  # ← 编译成子图

# Supervisor 子图
supervisor_builder = StateGraph(SupervisorState, ...)
supervisor_builder.add_node("supervisor", supervisor)
supervisor_builder.add_node("supervisor_tools", supervisor_tools)
supervisor_builder.add_edge(START, "supervisor")
supervisor_subgraph = supervisor_builder.compile()  # ← 编译成子图

# 主图
deep_researcher_builder = StateGraph(AgentState, ...)
deep_researcher_builder.add_node("clarify_with_user", clarify_with_user)
deep_researcher_builder.add_node("write_research_brief", write_research_brief)
deep_researcher_builder.add_node("research_supervisor", supervisor_subgraph)  # 子图作为节点
deep_researcher_builder.add_node("final_report_generation", final_report_generation)
deep_researcher_builder.add_edge(START, "clarify_with_user")
deep_researcher_builder.add_edge("research_supervisor", "final_report_generation")
deep_researcher_builder.add_edge("final_report_generation", END)
deep_researcher = deep_researcher_builder.compile()  # ← 主图
```

**子图是关键设计**：Supervisor 调用 `researcher_subgraph.ainvoke()` 就像调用一个函数，输入 ResearcherState，等它跑完拿到 ResearcherOutputState。

---

## 三、如果我想自己做一个，怎么入手？

### 3.1 最小可行版本（MVP）

你不需要一开始就做完整的双层 Agent。这是渐进路线：

**Level 1：单次搜索 → 报告（1 天）**

```
用户输入 → web_search → LLM 写报告
```

就一个线性流程，不用状态图，不用 Agent 循环。你要解决的问题：
- 怎么调搜索 API
- 怎么把搜索结果喂给 LLM 生成报告
- 怎么格式化输出

**Level 2：搜索-反思循环（1 天）**

```
用户输入 → web_search → think → web_search → think → 写报告
```

在 Level 1 基础上加循环。你要解决的问题：
- 怎么让 LLM 知道"该继续搜还是该停了"
- 怎么控制循环上限防止死循环

**Level 3：多路并行搜索（1 天）**

```
用户输入 → 拆成 3 个子任务 → 并行搜索 3 路 → 汇总 → 写报告
```

在 Level 2 基础上加并发。你要解决的问题：
- 怎么安全地并行执行多个搜索
- 怎么汇总多个搜索结果

**Level 4：Supervisor-Researcher 双层（2 天）**

加上 Supervisor 层，它负责拆任务、派活、判断完成。这就是当前项目的完整形态。

### 3.2 核心抽象（无论用什么语言实现）

不管你用 Python/Java/Go，核心抽象是一样的：

```
┌─────────────────────────────────────────┐
│  LLM 调用抽象                            │
│  - 普通对话（生成文本）                    │
│  - 结构化输出（生成 JSON）                 │
│  - 工具调用（Function Calling）           │
└─────────────────────────────────────────┘
           │
┌─────────────────────────────────────────┐
│  工具管理                                │
│  - 搜索工具（Tavily / 你自己的搜索）       │
│  - 反思工具（think_tool）                 │
│  - 完成信号（ResearchComplete）           │
└─────────────────────────────────────────┘
           │
┌─────────────────────────────────────────┐
│  状态管理                                │
│  - Agent 对话历史                        │
│  - 研究中间结果                          │
│  - 循环控制（iteration 计数）             │
└─────────────────────────────────────────┘
           │
┌─────────────────────────────────────────┐
│  循环控制                                │
│  - while 循环 + 退出条件                 │
│  - 最大迭代次数上限                      │
│  - 异常 + 超时处理                       │
└─────────────────────────────────────────┘
```

### 3.3 Java 实现的建议抽象

```java
// 1. LLM 调用接口
interface LLMClient {
    String chat(List<Message> messages);                              // 普通对话
    <T> T structuredOutput(List<Message> messages, Class<T> schema); // 结构化输出
    String chatWithTools(List<Message> messages, List<Tool> tools);   // 工具调用
}

// 2. 搜索工具接口
interface SearchTool {
    List<SearchResult> search(List<String> queries);
}

// 3. Agent 基类
abstract class Agent {
    List<Message> history;
    int maxIterations;

    abstract Result run(String task);
}

// 4. Supervisor
class SupervisorAgent extends Agent {
    List<ResearcherAgent> researchers;
    ExecutorService executor;  // 虚拟线程池

    Result run(String researchBrief) {
        while (iterations < maxIterations) {
            // LLM 决策：拆任务
            List<String> subTasks = llm.decideTasks(history, researchBrief);
            // 并行派发
            List<Future<Result>> futures = subTasks.stream()
                .map(t -> executor.submit(() -> researchers.get(0).run(t)))
                .toList();
            // 收集结果
            List<Result> results = futures.stream().map(Future::get).toList();
            // 判断是否完成
            if (llm.isComplete(history, results)) break;
        }
        return synthesize(results);
    }
}
```

---

## 四、面试可能被问到的点

| 问题 | 你的回答要点 |
|------|------------|
| "为什么用双层 Agent？" | 单层 Agent 面对复杂问题时 context 太长、搜索策略单一；Supervisor 负责宏观调度，Researcher 负责微观执行，职责分离 |
| "怎么防止死循环？" | 硬限制（max_iterations）+ 软引导（prompt 里写"搜 3 次就停"）+ 信号机制（ResearchComplete） |
| "并行搜索怎么控制？" | Semaphore 限制并发数，asyncio.gather 并行执行，超过限制的丢弃并给错误提示 |
| "搜索结果怎么处理？" | URL 去重 → LLM 摘要（并行）→ 保留原始引用 → 格式化输出 |
| "token 超限怎么办？" | 检测错误类型 → 渐进式截断（先截到 4x limit，然后每次减 10%）→ 最后返回错误 |
| "怎么保证报告质量？" | 多层约束：prompt 层（写明要求）+ 结构层（compress 必须保留原始信息）+ 引用强制（必须有 Sources 章节） |

---

## 五、建议的下一步

1. **跑一遍**：用一个简单问题（如"列出 3 个最好的 Python Web 框架"）在 Studio 里完整跑一遍，观察每个节点的输入输出
2. **改一个东西**：比如把 `research_system_prompt` 里的搜索次数限制从 5 改成 3，看效果变化
3. **加一个新工具**：在 `get_all_tools()` 里加一个 mock 工具，体验工具注册流程
4. **画一遍流程图**：不看代码，自己画出 Supervisor 循环和 Researcher 循环的状态转换图
5. **开始写 MVP**：从 Level 1 开始，用 Java 实现最简单的"搜索→报告"流程
