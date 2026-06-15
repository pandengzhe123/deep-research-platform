# 工作留痕

> 每次改动记录：改了什么文件、为什么、怎么测试的。

---

## 2026-06-14 — SSE 流式透传 + 性能优化 + 停止研究

### 做了什么

#### SSE 三层打通
- Java `AgentClient`: `bodyToFlux(String)` → `bodyToFlux(ServerSentEvent<String>)`，保留事件名
- Java `ResearchController`: SSE 端点补齐 session 管理 + user_id 注入 + 报告保存
- 前端: `api.post('/research')` → `fetch('/api/research/stream')` + ReadableStream 解析
- nginx: `/api/` 补齐 `proxy_http_version 1.1` + `Connection ''` + `chunked_transfer_encoding`
- 修复 `@AuthenticationPrincipal` 在 WebFlux 中不生效 → 手动从 Header 解析 JWT

#### 性能优化（Level 2+ 每轮省 ~16 秒）
- **批量摘要**: 5 个 URL 合并 1 次 LLM 调用（串行 20s → 批量 7s）
- **跨轮 URL 去重**: `_seen_urls` 集合，重复 URL 不再摘要（省 ~30% LLM 调用）
- **搜索结果缓存**: TTL 5 分钟，相同 query 命中缓存（省 Tavily API 调用）

#### 停止研究（真正取消）
- 前端: `AbortController.abort()` → fetch 中断
- Python: `finally` 块 `task.cancel()` → CancelledError 穿透所有 await
- 效果: LLM/搜索调用立即停止，不再消耗 token

#### RAG 文档勾选
- 前端 KB 面板加 checkbox，用户选择要搜索的文档
- `rag_doc_ids` 全链路传递: 前端 → Java → Python → Agent → kb.search(doc_ids)
- 不勾选 = 搜全部，勾选 = 只搜选中的

#### 前端体验优化
- SSE 真实进度替代假动画（搜索/RAG/反思/撰写 分阶段显示）
- 会话切换不中断后台研究，切回恢复实时进度
- 研究耗时显示在报告顶部
- 停止按钮（红色圆形）替换发送按钮
- UI 高级感: 渐变/阴影/动画/无 emoji 文字标签
- 计时器: 渐变药丸 + 旋转动画，仅研究会话显示

#### 其他修复
- kb.py 存储截断 100→完整 chunk（5 倍信息量）
- SSL 全局绕过改为仅 HF_HUB
- Embedding 模型三级降级加载（本地→自动下载→手动提示）
- Dockerfile 预下载模型打进镜像
- Chroma delete_doc where 多字段→单字段
- kb.ingest 用 asyncio.to_thread

### 修改文件

| 文件 | 改动 |
|------|------|
| `agent/src/researcher/search.py` | 批量摘要 + 跨轮 URL 去重 + 搜索缓存 |
| `agent/src/researcher/agent.py` | rag_doc_ids 全链路 + kb_searching step |
| `agent/src/researcher/server.py` | rag_doc_ids + task.cancel + CancelledError |
| `agent/src/researcher/kb.py` | 完整 chunk 存储 + SSL 修复 + 模型降级 + delete 修复 |
| `agent/Dockerfile` | 预下载 embedding 模型 |
| `java-gateway/.../AgentClient.java` | ServerSentEvent 类型 |
| `java-gateway/.../ResearchController.java` | SSE 完整端点 + JWT 手动解析 |
| `java-gateway/.../ResearchModels.java` | ragDocIds 字段 |
| `frontend/src/views/ResearchView.vue` | SSE + 停止 + 文档勾选 + UI 优化 |
| `frontend/nginx.conf` | SSE 头 |




## 2026-06-11 — Agent 代码审查：RAG 半失效 + 协议违规修复

### 发现的问题

审查 `agent.py` Level 2 循环时发现三个相互关联的问题，导致 RAG（知识库检索）**表面可用、实际半失效**：

#### 1. `AGENT_SYSTEM` 提示词未提及 `search_kb` 工具（已修复）

系统提示只描述了 `search` 和 `think` 两个工具，完全没有提到 `search_kb`。虽然工具定义通过 OpenAI `tools` 参数传给了 API，但 Agent 的系统提示里没告诉它"你可以搜知识库"，导致 LLM 大概率不会主动调用 `search_kb`。

**修复**：`AGENT_SYSTEM` 中加入 `search_kb` 工具描述，并明确指示"涉及我的文档/上传的资料时优先使用"。

#### 2. `search_kb` 结果未进入最终报告流水线（已修复）⚠️ 关键

`search_kb` 的执行结果只被追加到 `messages`（Agent 对话历史），**没有被追加到 `all_search_results`**。而最终报告的生成流程是：

```
all_search_results → 压缩去噪 → LLM 撰写报告
```

这意味着 KB 内容虽然能影响 Agent 的思考决策（在对话历史中），但**永远不会出现在最终报告里**——除非 Agent 在后续反思中主动手动引用。这和你之前测试的"RAG 正常工作"的印象矛盾。

**修复**：`search_kb` 结果同步追加到 `all_search_results`，与 `search` 结果同等对待。同时添加了与 `search` 一致的 `MAX_RESULTS_CHARS` 截断和 `MAX_TOTAL_RESULTS` 轮次限制。

#### 3. 异常处理：`tool_call_id` 造假 + 粒度过粗（已修复，两轮迭代）

**第一轮**：`except Exception` 块中硬编码 `"error"` 作为 `tool_call_id`。当异常发生在 `chat_with_tools()` 调用时，不存在 `tc` 对象，造假 ID 破坏消息链完整性。改为 `role: "user"` 消息。

**第二轮（用户指正）**：`except` 粒度太粗——整个 try 块从 `chat_with_tools()` 包到工具循环结束。LLM 调用失败和具体工具执行失败混在一起，异常时拿不到真实的 `tc.id`，LLM 无法知道是哪个工具出了问题。

**最终方案**：两层异常处理：

```
for tc in msg.tool_calls:
    try:                          ← 内层：每个工具独立 try
        if name == "search": ...
        elif name == "search_kb": ...
        elif name == "think": ...
    except Exception as e:
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,  ← 真实的！LLM 知道这次调用失败了
        })

except Exception as e:            ← 外层：LLM 调用本身失败才到这里
    messages.append({
        "role": "user",            ← 没有 tc，用 user 角色
    })
```

#### 4. JSON 解析失败导致孤儿 `tool_call`（已修复）

`json.loads(tc.function.arguments)` 失败时，原来的代码直接 `continue`——assistant 消息已声明该 `tool_call`，但永远没有对应的 `tool` 响应。协议要求 N 个 `tool_calls` → N 条 `tool` 响应，孤儿 ID 破坏这个不变量。

**修复**：JSON 解析失败时也追加 tool 响应，使用真实的 `tc.id`，内容为错误描述，提示 LLM 检查参数格式后重试。

#### 5. `Level1Agent` 类——死代码（待删除）

`Level1Agent` 类定义了 ~50 行但从未被调用。`server.py` 实际使用的是 `FastLevel1Agent`。不影响功能但增加维护负担。

### 影响评估

| 问题 | 严重度 | 用户感知 |
|------|:--:|------|
| RAG 结果不进报告 | 🔴 高 | 勾了 RAG 但报告里看不出 KB 内容 |
| Prompt 不提 search_kb | 🟡 中 | LLM 可能不调用，RAG 开关形同虚设 |
| 异常处理粒度过粗 | 🟡 中 | LLM 不知道哪个工具失败，无法针对性调整 |
| JSON 解析孤儿 ID | 🟢 低 | LLM 极少出非法 JSON，但出一次就脏消息历史 |
| Level1Agent 死代码 | 🟢 低 | 零影响，纯代码卫生 |

### 教训

- 数据流审查应该是 Code Review 的必查项——跟踪每个数据源的完整生命周期（产生 → 存储 → 消费）
- Prompt 和工具定义要一起审查，二者共同决定 Agent 行为
- OpenAI 协议中 `tool_call_id` 是外键关系，不能随意编造
- **异常处理粒度决定错误信息的质量**——太粗则 LLM 不知道什么失败了，太细则重复代码多。应该在"能拿到上下文信息"的最小范围内 catch
- **协议不变量应该在所有代码路径上保持**——`assistant.tool_calls` 有 N 条，就必须有 N 条 `tool` 响应，包括失败路径和 continue 路径

#### 6. `TOOLS[:-1]` 靠位置过滤 search_kb（已修复）

`TOOLS[:-1]` 假设 `search_kb` 永远是最后一个元素，未来在末尾新增工具会导致移除错误工具。改为按名称显式过滤：`[t for t in TOOLS if t["function"]["name"] != "search_kb"]`。

---

### 第二轮审查：数据流 + 并发 + 边界条件

#### 7. `all_search_results` 按结果个数截断，非按轮截断（已修复）

注释写"保留最近 3 轮"，但代码是每次 `append` 后 `len > 3` 就截断到末尾 3 个**元素**。一轮内 LLM 同时调 `search` + `search_kb` 就占 2/3，早期轮次信息被无声挤掉。

**修复**：改名 `MAX_TOTAL_RESULTS` → `MAX_ROUND_RESULTS`；每轮内结果收集到 `round_results`，for 循环结束后 `"\n\n---\n\n".join()` 合并为一条再追加；截断单位从"单条结果"变为"整轮合并后字符串"。

#### 8. `kb.search()` 同步调用阻塞事件循环（已修复）⚠️ 性能

`kb.search()` 内部 sentence-transformers 做 CPU embedding + ChromaDB 磁盘 I/O，均为同步阻塞。`async def` 包装无法改变其阻塞本质——`asyncio.gather(search_fast, _kb_search)` 在 Level 1 中退化为串行。

**修复**：两处统一改为 `await asyncio.to_thread(self.kb.search, ...)` 扔进线程池：
- `FastLevel1Agent._kb_search()`（Level 1 并行路径）
- Level 2 `search_kb` handler（工具循环内）

#### 9. 消息历史截断可能丢弃原始问题（已修复）

`messages = messages[-6:]` 在对话深入后，第一条（`"请研究以下问题：{question}"`）被截掉，LLM 忘记研究目标，后续搜索失焦。

**修复**：`messages = [messages[0]] + messages[-5:]`，始终锚定原始问题，仍维持 6 条。

#### 10. 空 `all_search_results` 导致无意义报告（已修复）

所有搜索失败且无 KB 结果时 `raw_text = ""`，LLM 收到空输入产生幻觉。

**修复**：压缩前加守卫 `if not all_search_results`，直接返回含原因的兜底提示，跳过 LLM 压缩+报告链路。

#### 11. emit 回调信息泄露风险（已评估，接受）

emit 经 SSE 明文传输到前端。经审查，当前 emit 内容均为已截断字段（查询词、150 字反思摘要、进度消息），搜索结果和 KB 内容不经过此通道。风险可接受。

### 影响评估

| 问题 | 严重度 | 用户感知 |
|------|:--:|------|
| RAG 结果不进报告 | 🔴 高 | 勾了 RAG 但报告里看不出 KB 内容 |
| 按结果数截断非按轮 | 🔴 高 | 早期轮次结果被无声丢弃，报告遗漏 |
| kb.search 阻塞事件循环 | 🟡 中 | Level 1 并发退化为串行，响应变慢 |
| 消息截断丢原始问题 | 🟡 中 | 长对话后 LLM 忘记研究目标 |
| 空结果无兜底 | 🟡 中 | 全失败时产生幻觉报告 |
| Prompt 不提 search_kb | 🟡 中 | LLM 可能不调用，RAG 开关形同虚设 |
| 异常处理粒度过粗 | 🟡 中 | LLM 不知道哪个工具失败 |
| `TOOLS[:-1]` 位置依赖 | 🟢 低 | 当前恰好正确，未来可能翻车 |
| JSON 解析孤儿 ID | 🟢 低 | LLM 极少出非法 JSON |
| emit 信息泄露 | 🟢 低 | 已审查，当前无风险 |
| Level1Agent 死代码 | 🟢 低 | 零影响，纯代码卫生 |

### 教训

- **数据流审查**：跟踪每个数据源完整生命周期（产生 → 存储 → 消费），不能只看单个函数
- **并发不是 `async` 关键字**：`async def` + 同步阻塞 = 假并发，`to_thread` 才能真正释放事件循环
- **截断操作要保护锚点**：消息截断保留原始问题，结果截断以轮次而非条数为单位
- **协议不变量覆盖所有代码路径**：包括 `continue`、`except`、`return` 等非主线路径
- **异常处理粒度决定错误信息质量**：在能拿到上下文的最小范围内 catch
- Prompt 和工具定义联合审查，二者共同决定 Agent 行为

---

### 第三轮审查：多级 Agent 联动 + 汇总质量

#### 12. Level 3/4 未传递 `kb_enabled` 和 `user_id` 给子 Agent（已修复）

`Level3Agent` 和 `Level4Agent` 创建子 `Level2Agent` 时没传 `kb_enabled` 和 `user_id`，导致 Level 3/4 模式下 RAG 完全失效——即使前端勾了 RAG，子研究员也看不到知识库。

**修复**：`Level3Agent.__init__`、`Level4Agent.__init__` 接收并存储 `kb_enabled`/`user_id`；`server.py` 创建时传入。子 Agent 创建时传递这两个参数。全链路：`server.py` → `Level3/4Agent` → `Level2Agent`。

#### 13. 子课题失败返回 Markdown 错误串污染汇总（已修复）

`safe_run` 异常时返回 `"# 研究失败\n\n子课题「xxx」执行出错: ..."`，被 LLM 当作正常子报告写入最终报告。

**修复**：失败返回 `None`，汇总前过滤。Level 3 增加全部失败的兜底提示。Level 4 的 zip 循环检查 `None`，跳过并通知 Supervisor 该课题失败。

#### 14. 子报告 `#` 顶级标题破坏汇总 Markdown 层级（已修复）

子报告以 `#` 开头，放在 `## 子课题N` 下形成"一级标题嵌套二级"，渲染混乱。

**修复**：汇总拼接前用 `re.sub(r'^(#{1,6})\s', r'#\1 ', ...)` 将子报告所有标题降一级（`#`→`##`, `##`→`###`），层级适配为 `## 子课题 → ### 原H1 → #### 原H2`。

#### 15. 失败子课题导致编号断层（已修复）

原始 `idx` 跳过失败项后编号不连续：子课题 0 失败，下一个显示"子课题2"。

**修复**：改用 `enumerate(valid)` 从 1 开始连续编号，用户看不到断层。

#### 16. `DECOMPOSE_PROMPT` 自相矛盾（已修复）

Rule 1 "拆成 2-4 个"与 Rule 4 "简单问题可返回 1 个"矛盾，Schema 允许 `minItems: 1`。

**修复**：合并为"拆成 1-4 个子课题（简单事实类 1 个，复杂对比/分析类 2-4 个）"。

#### 17. Level3Agent / Level4Agent 冗余 `self.kb` 导入（已修复）

Level3/Level4 的 `__init__` 导入了 `self.kb` 但从未调用——只传 `kb_enabled` 给子 Agent，子 Agent 自己加载 KB。

**修复**：删除两个类中的冗余 `self.kb` 初始化。

#### 18. 失败子课题无前端通知（已改进）

失败时只 `print` 到终端，前端不知情。

**改进**：加 `self.emit()` 通知前端 SSE 进度流，如 `"子课题 3「...」失败，跳过"`。

#### 19. `DECOMPOSE_PROMPT` system_prompt 未同步（已修复）

`structured_output()` 的 `system_prompt` 参数仍写死"2-4 个"，与已改为"1-4"的 `DECOMPOSE_PROMPT` 不一致。

**修复**：`system_prompt="你是研究规划专家。把用户问题拆成 1-4 个独立子课题。"`

#### 20. 全链路超时 10→30 分钟（已修复）

Level 3/4 多路并行常超 10 分钟，导致 Java 超时断开但 Python 仍在跑，结果丢失。涉及 5 个超时点：

| 层级 | 文件 | 之前 | 之后 |
|------|------|:--:|:--:|
| Netty TCP 读 | `WebClientConfig.java` | 10 分钟 | 30 分钟 |
| Java 阻塞等待 | `AgentClient.java` | 10 分钟 | 30 分钟 |
| 前端 axios | `api.js` | 10 分钟 | 30 分钟 |
| Vite 代理 `/api`, `/research` | `vite.config.js` | ~1 分钟 | 30 分钟 |
| nginx `/api`, `/research` | `nginx.conf` | 10 分钟 | 30 分钟 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `agent/src/researcher/agent.py` | ①AGENT_SYSTEM 加 search_kb 描述 ②search_kb 结果进 all_search_results + 截断 ③异常处理两层 ④JSON 解析补 tool 响应 ⑤按名称过滤 TOOLS ⑥按轮截断 ⑦asyncio.to_thread ⑧消息截断保留 messages[0] ⑨空结果兜底 ⑩kb_enabled/user_id 全线贯通 ⑪子课题失败不污染汇总 ⑫Markdown 标题降级 ⑬失败编号连续 ⑭DECOMPOSE Prompt+Schema+system_prompt 统一 ⑮冗余 kb 导入删除 ⑯失败 emit 前端通知 |
| `agent/src/researcher/server.py` | Level 3/4 传入 kb_enabled 和 user_id |
| `java-gateway/.../AgentClient.java` | `.block()` 超时 10→30 分钟 |
| `java-gateway/.../WebClientConfig.java` | `responseTimeout` 10→30 分钟 |
| `frontend/src/utils/api.js` | axios timeout 10→30 分钟 |
| `frontend/vite.config.js` | 代理超时 1→30 分钟 |
| `frontend/nginx.conf` | `proxy_read_timeout` 10→30 分钟 |


### 第四轮审查：并发连接 + 容错 + 前端缓存

#### 21. 子 Agent 各自创建 LLMClient 导致连接洪峰（已修复）

Level 3/4 的每个子 Level2Agent 各自 `LLMClient()` 创建独立 HTTP 连接池，N 个子 Agent = N 个 OpenAI 客户端 = N 套连接。并行请求时 DeepSeek 负载均衡随机重置部分连接，每次 Level 3/4 都出现 `APIConnectionError`。

**修复**：`Level2Agent.__init__` 接受可选 `llm` 参数；`Level3Agent`/`Level4Agent` 创建子 Agent 时传入 `llm=self.llm`，所有子 Agent 共享同一客户端，连接复用 keep-alive。

#### 22. JSON 字符串含未转义换行导致解析失败（已修复）

LLM 生成 `think` 工具的 `reflection` 参数时，长文本中的裸换行符未转义为 `\n`。直接 `json.loads()` 失败，Agent 的反思内容丢失。

**修复**：`json.loads()` 失败后，用 `re.sub(r'(?<!\\)\n', r'\\n', ...)` 修复未转义换行，再试一次。两次都失败才报错。

#### 23. DuckDuckGo 包更名（已修复）

`duckduckgo_search` 已改名 `ddgs`，每次降级搜索弹 `RuntimeWarning`。

**修复**：`search.py` 导入改为 `from ddgs import DDGS`；`Dockerfile` 依赖改为 `ddgs`；安装 `pip install ddgs`。

#### 24. 前端 localStorage 缓存阻碍报告加载（已修复）

`switchSession()` 命中 localStorage 后直接 `return`，缓存中的旧数据不含报告时，前端永远不从 API 拉取数据库中的完整报告。

**修复**：localStorage 命中时检查最后一条消息是否为报告（长度 > 500），不是则穿透到 API。

| 文件 | 改动 |
|------|------|
| `agent/src/researcher/agent.py` | LLMClient 共享 + JSON 换行容错 |
| `agent/src/researcher/search.py` | `duckduckgo_search` → `ddgs` |
| `agent/Dockerfile` | `duckduckgo-search` → `ddgs` |
| `frontend/src/views/ResearchView.vue` | localStorage 缓存检查 + 报告穿透 |

---

### 第五轮审查：Level 4 Supervisor 健壮性

#### 25. 未强制执行 max_parallel 并发限制（已修复）

Supervisor 可一次请求任意数量的 ConductResearch，代码不检查 `len(conduct_calls)`，可能同时启动 10 个 Level2Agent。

**修复**：改为分批派遣，每批最多 `self.max_parallel` 个，批次间顺序执行。不丢课题，只控制并发。

#### 26. Level 4 消息历史无限增长（已修复）

Level 2 有 `MAX_HISTORY_CHARS=500000` 保护，Level 4 没有。多轮 Supervisor 决策 + 研究报告累积可超模型上下文限制。

**修复**：每轮开始前检查 `total_chars`，超限时截断保留 `messages[0]` + 最近 5 条。

#### 27. ResearchComplete 与 ConductResearch 同时出现时处理顺序错误（已修复）

如果 Supervisor 同时返回两者，代码先处理 ConductResearch（收集课题），再检测 ResearchComplete 跳出。虽然最终不会派遣（break 在派遣前），但逻辑不清晰。

**修复**：`ResearchComplete` 检测提到工具处理循环之前，一旦出现直接 break，不解析任何 ConductResearch。

#### 28. Level 4 子研究员进度反馈丢失（已修复）

`safe_run` 创建 Level2Agent 时未传 `on_progress=self.emit`，研究员内部搜索/反思事件无法上报前端。

**修复**：`Level2Agent(on_progress=self.emit, ...)`。

#### 29. 报告截断 3000 字符丢失关键信息（已修复）

Supervisor 基于截断后的摘要决策，3000 字符可能截掉关键数据。

**修复**：3000 → 10000 字符。典型报告 3000-7000 字符，10000 基本完整保留。去掉硬编码 `...` 避免误导。

#### 30. think_tool 反思内容丢弃（已修复）

Level 4 的 tool 响应写 `"反思已记录"` 丢弃了实际内容，Supervisor 下轮看不到自己的反思。Level 2 保留了完整内容。

**修复**：`"反思已记录"` → `f"反思：{r}"`，与 Level 2 一致。

#### 31. zip 对齐依赖隐含顺序（已重构）

三个独立列表 `(tcs, conduct_calls, results)` 靠位置隐含对齐，二次过滤 `msg.tool_calls` 增加错位风险。

**修复**：改为 `conduct_items: list[tuple]` 在收集时绑定 `(tc, topic)` 配对，zip 只需两路对齐。

#### 32. safe_run 失败原因未传递给 Supervisor（已修复）

失败时只打印到终端，Supervisor 看到的是通用的"研究失败"，无法判断是超时、限流还是格式错误。

**修复**：`safe_run` 返回 `(report, error)` 元组，错误信息写入 tool 消息：`f"研究失败: {error}"`。

#### 33. 任务取消机制未接通（已知限制，待实现）

`server.py` 中 `cancel = aio.Event()` + `_active_tasks` 字典 + `DELETE /research/{task_id}` 端点构成了取消功能的骨架，但三处断线：
1. cancel 事件传入 `run_agent_with_sse` 但函数体**从不检查** `cancel.is_set()`
2. `_active_tasks` 字典始终为空——没有代码把 cancel 注册进去
3. 取消端点永远 404（找不到任何 task_id）

Java 端 `AgentClient.cancel()` 也已写好转发逻辑。属于"接口占位、功能待补"的半成品。

**要接通需要**：① 生成 task_id 注册到 `_active_tasks` ② `run_agent_with_sse` 循环中定期检查 `cancel.is_set()` ③ 触发后 `task.cancel()` + yield error 事件 ④ finally 清理 `_active_tasks`

### 修改文件

| 文件 | 改动 |
|------|------|
| `agent/src/researcher/agent.py` | ①分批派遣 ②Level 4 历史截断 ③ResearchComplete 优先检测 ④子研究员 on_progress ⑤报告 10000 字符 ⑥反思内容保留 ⑦conduct_items 配对 ⑧safe_run 错误传递 |


## 2026-06-09 — P0 漏洞修复日

### 做了什么

- 前端步骤轮播：等待期间不再只有"正在分析"，改为🔍搜索→📖分析→💭整理→📝撰写四种状态轮播
- 错误提示友好化：401/429/500/超时/网络断开 → 六种用户可理解的提示
- Agent OOM 保护：单结果 30万字符截断 + 只保留 3 轮 + 压缩前 50 万截断，三层防止内存溢出
- Token 超限保护：消息历史超 50 万字符自动截断旧消息
- Agent 单点故障：Java 侧 3 次指数退避重试（3s/9s/27s）
- PG 连接失败：HikariCP 自动重连 + SELECT 1 验证 + 健康检查含 DB 状态
- Docker 启动顺序：PostgreSQL 容器 healthcheck，gateway 等待 PG 就绪
- Chroma 数据损坏：ingest 加 try/except + health_check()
- API Key 泄露：验证 .gitignore 保护 + .env.example 加警告注释
- 会话卡死：@Scheduled 每 10 分清理 30 分钟以上的 running 会话
- 报告导出：📋 复制 Markdown + 💾 下载 .md 文件
- LLM 超时：OpenAI client 加 30s 超时
- 一键启动脚本 `start-all.bat`：三窗口同时启动

### 新增文档

| 文档 | 内容 |
|------|------|
| `docs/project-overview.md` | 项目全貌（9 章，面试前必读） |
| `docs/ux-critique.md` | 用户痛点批判（12 个问题，含修复状态） |
| `docs/dev-critique.md` | 开发者漏洞批判（15 个问题，含修复状态） |

### 当前状态

- 整体 88%，核心功能 + 致命/高风险漏洞全部修完
- 代码 ~3,800 行，文档 ~4,000 行


## 2026-06-08 — Docker Compose 一键部署

### 做了什么

- 新增 `agent/Dockerfile`：Python 3.11-slim，装依赖 + 启动 FastAPI
- 新增 `java-gateway/Dockerfile`：多阶段构建（Maven 编译 → JRE 运行）
- 新增 `frontend/Dockerfile`：多阶段构建（Node 编译 Vue → Nginx 托管）
- 新增 `frontend/nginx.conf`：Nginx 反向代理 `/api`→Java，`/kb` `/research`→Python
- 新增 `docker-compose.yml`：4 服务编排（postgres + agent + gateway + frontend）
- `application.yml` 连接地址支持 `${ENV_VAR:default}` 环境变量覆盖

### 效果

```bash
git clone ... && docker compose up
# → http://localhost:3000 直接用，不用装任何东西
```

### 新增文件

| 文件 | 作用 |
|------|------|
| `agent/Dockerfile` | Python Agent 镜像 |
| `java-gateway/Dockerfile` | Java 网关镜像 |
| `frontend/Dockerfile` | Vue + Nginx 镜像 |
| `frontend/nginx.conf` | Nginx 反向代理配置 |
| `docker-compose.yml` | 4 服务编排 |


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
