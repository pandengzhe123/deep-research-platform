# 面试模拟：最犀利的问题 & 标准答案

> 共 30 问，分十类：架构设计、效果边界、工程实践、学习动机、性能成本、异步并发、工程细节、深度追问、健壮性、RAG与检索。每题附考官视角。

---

## 一、架构设计类

### Q1: "你说没用 LangChain/LangGraph，那你 Agent 的 Function Calling 是怎么实现的？给我讲清楚每一行。"

**标准答案：**

"本质就是 OpenAI 原生 API 的 tools 参数。我定义了 tools JSON schema，每次调 API 时把 tools 传进去。LLM 返回的不是普通文本，而是一个 message 对象，里面有个 tool_calls 数组。我解析这个数组，自己写 if/elif 路由执行对应的 Python 函数，把结果以 `role: tool` 的消息追加回 messages 列表，再调一次 LLM。循环直到 LLM 不返回 tool_calls——说明它认为信息够了。

整个循环的核心只有 3 行代码：

```python
msg = self.llm.chat_with_tools(messages=messages, tools=TOOLS)  # LLM 决策
result = await self.search.search(queries)                        # 我执行
messages.append({'role': 'tool', 'content': result})              # 追加结果
```

LangChain 的 bind_tools 底层做的就是我这三行。我不需要框架帮我做，因为这三行的逻辑本身就很直白。"

> **考官想听**：你没有回避细节，能讲到 API 层面的 tool_calls、role:tool、追加到 messages。

---

### Q2: "你的 Level 1-4 有什么区别？为什么 Level 4 不是 Level 3 的无意义堆叠？"

**标准答案：**

"Level 3 和 Level 4 的关键区别是**外层循环的有无**。

Level 3 是'一次性分派'——LLM 拆一次题，并行跑 N 个 Level 2，汇总。适合'比较 A、B、C'这类平面问题。

Level 4 是'循环调度'——Supervisor 拆一批 → 并行跑 N 个 Level 2 → **评估结果** → 发现信息缺口 → 再拆一批补充 → 直到满意。适合'详细对比新能源车技术路线'这种需要多轮补充的问题。

差别用一个例子就能讲清楚：

```
用户: '对比蔚来、理想、小鹏的技术路线'

Level 3:
  拆 3 个任务 → 并行搜一轮 → 汇总 → 报告
  问题: 每个公司只搜了一轮，电池技术细节可能不够

Level 4:
  第 1 轮: 拆 3 个任务 → 并行搜
  Supervisor: '蔚来和理想的电池策略对比不够，补一轮'
  第 2 轮: 拆 1 个任务 → 搜'蔚来 vs 理想 电池策略'
  Supervisor: '够了'
  → 汇总两轮的所有发现 → 报告
```

这不是无意义堆叠，而是**信息完整性的本质提升**。"

> **考官想听**：你能讲清楚循环的价值，不是"多一层循环就叫 Level 4"。

---

### Q3: "为什么用 Java + Python 混合架构？一个 Spring Boot 全栈不行吗？"

**标准答案：**

"不行。Python 生态有 OpenAI SDK、Tavily SDK、asyncio 和所有 AI 基础设施——这是 Python 的绝对主场。Java 有 Spring Boot、WebFlux、虚拟线程、成熟的并发控制和 REST 生态。

我的选择不是'我只会这两种语言'，而是故意的架构决策——**AI 推理层和网关层解耦**。

具体好处：
- Python 代理升一个模型版本不用动 Java
- Java 加一个限流策略不用动 Python
- 两个服务独立部署、独立扩容、独立崩恢复
- 面试官如果问'生产化了怎么办'——网关可以在不动推理逻辑的情况下直接部署到 K8s

全栈 Spring Boot 的做法要么要在 Java 里嵌入 Python 解释器，要么要用 LangChain4j（功能受限）。我的架构是用最小的代价拿到两种语言最强的能力。"

> **考官想听**：不是"因为我会两种语言"，而是 "因为架构上需要解耦"。

---

### Q4: "你的并发有多少层？各管什么？"

**标准答案：**

"四层：

第一层：搜索内部。`asyncio.gather` 并行发多个 Tavily 查询请求。

第二层：Level 3/4 的子研究员并行。`asyncio.gather(N个Level2Agent.run())`。

第三层：FastAPI 的 asyncio 事件循环。每个请求一个协程。

第四层：Java 网关的 `Semaphore(20)`。全局最多 20 个并发研究任务。

Java 限 20 是因为——保护的是 DeepSeek API 的速率限制。Python 侧本身没有硬上限，asyncio 可以处理几百个协程。但 LLM API 有每分钟请求数限制，所以在网关那层提前做好保护。这四层从下到上分别控制的是**网络 I/O、CPU 并行度、并发请求数、外部 API 预算**。"

> **考官想听**：你能分清每层在保护什么资源，不是随便丢个数字。

---

## 二、效果和边界类

### Q5: "你这个跟直接用 ChatGPT 搜一下有什么区别？"

**标准答案：**

"三个本质区别。

第一，**自主决策**。ChatGPT 搜什么是你决定的。我这个是 Agent 自己决定搜什么——它分析问题、规划搜索词、搜完之后反思'还缺什么'、针对缺口再搜。用户不需要知道怎么搜，只需要知道想问什么。

第二，**多源并行**。ChatGPT 一次搜一个话题。Level 3/4 同时派多个研究员搜不同方向，然后汇总。'对比五家公司的技术路线'——ChatGPT 要搜五次，你也要问五次。Agent 自己拆五个研究员同时跑。

第三，**引用可追溯**。ChatGPT 给答案但不一定给来源。我这个每句话都带 `[标题](URL)`，结尾有 Sources 章节。用户可以验证每一句话的真伪。"

> **考官想听**：自主决策、并行、可追溯——三个点每个都打中 ChatGPT 的弱点。

---

### Q6: "你这个项目在什么情况下会失效？"

**标准答案：**

"三个明确边界：

1. **信息不透明的领域**。如果被搜索的话题在公开网络上没有足够信息（如内部商业机密、未公开的政策），Agent 搜不到就是搜不到，不会编。

2. **deep research 级别的话题深度不足**。如果一个问题需要读 50 篇论文并做元分析，当前架构的信息深度不够——每个搜索结果只取前 5 条摘要。应该在之后加一个'深层阅读'阶段：对关键来源做全文分析而非摘要。

3. **长报告的 token 超限风险**。目前没有 token 超限处理。一个非常非常复杂的问题导致搜索结果积累到超过 DeepSeek V4 Flash 的 1M 上下文窗口，会直接 400 报错。原项目有渐进截断机制——这是明确的后续优化点。

能识别边界比声称'完美'更专业。"

> **考官想听**：你知道项目的边界在哪，不是吹牛。

---

### Q7: "你用的是什么模型？为什么选它？成本多少？"

**标准答案：**

"DeepSeek V4 Flash。选它三个原因：

1. **成本低**——输入 $0.14/M tokens，输出 $0.28/M tokens。同样的报告质量，比 GPT-4.1 便宜 12 倍。一次深度研究 2-5 毛钱。

2. **Function Calling 稳定**——V3 开始就支持 Function Calling，不用像 V4 Pro 那样处理思考模式的兼容问题。

3. **1M 上下文窗口**——深度研究的搜索结果可能累积很长的 messages 历史，大窗口是必需的。

踩过的坑：V4 Pro 默认开启思考模式，不支持 tool_choice。需要 `extra_body={"thinking": {"type": "disabled"}}` 显式关掉。这是通过读 API 文档和 debug 实际请求发现的。"

> **考官想听**：你有成本意识、做了技术调研、踩过坑并解决了。

---

## 三、工程实践类

### Q8: "SSE 和 WebSocket 你选哪个？为什么？"

**标准答案：**

"选了 SSE。原因：研究进度推送是**单向的**——服务器推送进度给前端，前端不需要回传信息。SSE 比 WebSocket 简单太多：浏览器原生 EventSource 支持、自动重连、不需要心跳包、不需要处理双向通信的状态管理。

但如果后续要加'用户中途取消研究'或'流式对话追问'，就需要改成 WebSocket。当前架构已经有这个扩展能力——Python 端用 asyncio，Java 端用 WebFlux，改 WebSocket 只需要换传输层。"

> **考官想听**：你的选择是基于场景需求的，不是随便选的。

---

### Q9: "如果 DeepSeek API 挂了或者 Tavily 挂了，你的系统怎么处理？"

**标准答案：**

"三层防护。

LLM 层：`_call_with_retry()` 里 429 限流等 5 秒重试、网络错误指数退避（2s→4s→8s）、5xx 服务端错误重试三次。401/403/400 客户端错误不重试直接报。

搜索层：`_do_search()` 先调 Tavily，失败或空结果自动降级 DuckDuckGo。DuckDuckGo 免费且不需要 API Key——质量不如 Tavily 但能用。Agent 不感知降级，它只看到 `search` 工具。

Agent 循环层：Level 2 的 while 循环整轮包 try/except。一轮失败不中断全部——异常信息作为 tool message 追加到 messages，Agent 基于已有信息继续。

总共 55 行代码实现了三层各管各的防护。LLM 崩一次不致命、Tavily 挂了不瘫痪、子研究员一轮失败不影响汇总。"

> **考官想听**：不是计划中的功能，是已经实现的三层防护。每层对标不同的故障点，各有各的降级逻辑。

---

### Q10: "你怎么保证生成的报告不是胡编的？"

**标准答案：**

"两个机制。

第一，**强制引用**。Prompt 里要求'每个来源标注 [标题](URL)'、'结尾必须有 Sources 章节'。如果 LLM 没找到对应的原文，它很难编出精确的引用——URL 造假对 LLM 来说比直接编文本更难。

第二，**think_tool 反思机制**。每次搜索后 Agent 被强制暂停、反思'我找到的信息有没有矛盾？来源够不够权威？'——这让它在写报告前做了一次元认知检查。

但诚实地讲，当前的'反思'不完美——无法保证 LLM 不会产生幻觉。后续要加**事实核查步骤**：在做最终汇总时，对关键数据做多源交叉验证。如果多个来源对同一个数字有矛盾，要么标注分歧、要么按权威来源排序优先选择。"

> **考官想听**：你知道当前机制有限，也清楚怎么改进。

---

## 四、学习 & 动机类

### Q11: "你为什么选这个项目？为什么不用现成的框架？"

**标准答案：**

"我一开始是跑了 open_deep_research 原项目，亲身体验了它的功能。然后我花了几天读源码，发现 LangGraph 的 StateGraph 在底层做的事就是 while 循环 + messages 管理。我觉得'这东西我不需要框架也可以实现'——于是用了 1 天写了个 Level 2、再加 1 天写了 Level 3/4。

为什么要这样做？因为我发现如果只是'用 LangGraph 搭了一个 Agent'写进简历，面试官一眼就能看出是文档例子的变体。但如果我说'我从 OpenAI 原始 API 开始写了一个 Agent 循环'——不需要解释什么框架学会了什么用法，而是直接展示我对 Function Calling 的底层机制的理解。两个表述在面试官那里的分量天差地别。"

> **考官想听**：这不是偷懒或无知，而是故意的"返璞归真"。

---

### Q12: "做完这个项目你最大的收获是什么？"

**标准答案：**

"三个方面。

技术上：彻底理解了 Function Calling 不是框架特性，是 API 能力。Agent 循环的本质是 while + tool_calls + messages 追加。RAG 也不只是概念——从文档切块、embedding 模型下载失败到换 HashingVectorizer 再到解决 SSL 证书问题最终用上 sentence-transformers，这条链路每一环都实际踩过坑才真正明白 Vector DB 怎么工作。

架构上：犯过一个关键错误——server.py 里为了推送进度把 Agent 逻辑从头写了一遍，导致 Level 1/2/3 在 agent.py 和 server.py 各有一份实现。后来用 on_progress 回调模式重构，server.py 从 460 行缩到 218 行，所有 Agent 逻辑回归 agent.py。这个踩坑让我深刻理解了观察者模式和关注点分离。

最后一个是面试官可能更关心的——**我学会了判断什么时候需要框架、什么时候不需要。** LangGraph 有价值，但如果我连它底层做什么都不懂，我用它也只是在调 API。现在我知道 bind_tools 背后在干什么、state 每次是怎么合并的、Agent 循环到底在 while 什么。"

> **考官想听**：不是背项目经验，而是能迁移到任何岗位的思维模型。

> **考官想听**：不是背项目经验，而是能迁移到任何岗位的思维模型。

---

## 五、性能与成本类

### Q13: "一次 Level 2 研究要调多少次 LLM？能不能优化？"

**标准答案：**

"一次典型的 Level 2 研究调 15-20 次 LLM。其中有 8-15 次花在**网页摘要**上——每个搜索结果都要调一次 LLM 把几千字的网页压成几百字的摘要。

我做了两件事来优化：

1. Level 1 Fast 把摘要全部跳过——直接用 Tavily 原生摘要。15-20 次 → 1 次，15-30 秒出结果。这是极致优化。

2. `search_fast()` 方法独立出去，Level 2 也用更快的方式（不抓取网页全文、不调 LLM 摘要）。权衡——速度快了 80%，但摘要精细度下降了。

更大的优化点在搜索去重升级——目前按 URL 去重；如果改成语义去重（基于相似度阈值），能在源头减少 30% 以上的冗余 LLM 调用。"

> **考官想听**：你不只知道怎么调用，还知道瓶颈在哪、怎么 trade-off。

---

### Q14: "你的系统 QPS 能到多少？怎么扩？"

**标准答案：**

"单用户场景：Level 1 能在 15-30 秒完成一个请求，Level 4 需要 3-10 分钟。这不是 QPS 问题——是单请求延迟问题。

多用户场景（如果扩展到 SSO 场景）：瓶颈不在我的代码，在 DeepSeek API 的速率限制。我的 Java 网关已经做了 Semaphore(20) 的并发控制。

扩法三步：
1. 加 Agent 实例——每个 Python Agent 是独立的，加一个容器就加一倍容量
2. Java 网关改 Round-Robin 负载均衡——分发到多个 Agent 实例
3. 如果到几十个并发，加一层 Redis 做分布式 Semaphore——把单机 20 变成集群 X

当前架构天然支持水平扩展。Python Agent 是无状态的，加实例就行。"

> **考官想听**：你知道瓶颈在外部 API，不是你的代码。扩展方案不是拍脑袋。

---

### Q15: "你说 DeepSeek V4 Flash 比 GPT-4.1 便宜 12 倍。你怎么做成本控制的？"

**标准答案：**

"四个手段：

1. **压缩**：每轮研究结束后，把原始搜索结果压缩整理一遍。去掉冗余和重复信息，减少后续 token 消耗。

2. **极速模式**：Level 1 全程只调 1 次 LLM。简单问题用 Level 1，复杂问题才升级到 Level 2+。

3. **search_fast**：跳过网页全文抓取和 LLM 摘要，直接用 Tavily 的原始摘要。搜索阶段 0 LLM 调用。

4. **tavily_search 去重**：多个搜索词可能返回同一个 URL——URL 去重避免对同一个网页做两次摘要。

如果以后在 Java 网关加一个费用预估 API——调 LLM 前先告诉用户'这次大概要花 XX 元'，确认后再执行——就更完善了。"

> **考官想听**：成本是你关心的问题，而且你有从代码层面做到控制。

---

## 六、异步与并发深度类

### Q16: "asyncio.gather 和 asyncio.wait 有什么区别？你为什么用 gather？"

**标准答案：**

"`asyncio.gather` 收集所有结果、按顺序返回；如果任何一个任务抛异常，整个 gather 抛异常。`asyncio.wait` 更灵活——可以设置 `return_when=FIRST_COMPLETED`，也可以拿到 partial 结果再继续等。

我用 gather 是因为 Level 3/4 的场景需要**所有子研究员都完成**再做汇总。如果我想要更智能的调度——比如"3 个研究员中 2 个完成就开始汇总，第 3 个后补"——就需要改用 `asyncio.wait` 或 `asyncio.as_completed`。

但在 Level 4 里我已经加了每个子研究员的 try/except——一个挂了不炸掉 gather。这是用 `safe_run` 包装器实现的：每个研究员内部 try/except 捕获异常，返回一个包含错误信息的默认报告，而不是让 gather 抛异常。"

> **考官想听**：你知道 API 选择的 trade-off，以及为什么当前场景选 gather。

---

### Q17: "你的异步代码里有 race condition 吗？"

**标准答案：**

"没有 race condition。Python asyncio 是**单线程协程模型**——同一时刻只有一个协程在执行，只有在 `await` 点才会切换。不会有多个协程同时修改同一个变量。

但有一个地方需要注意：Level 3 里多个 Level2Agent 共享同一个 `AgentClient` 实例。`LLMClient` 和 `SearchTool` 都是无状态的——它们只是包装了 HTTP 请求，不维护可变状态。每个 `Level2Agent.run()` 方法内部自己维护 `messages` 和 `all_search_results`，不存在共享可变状态。

如果改成多线程模型（比如 Java 的虚拟线程），就需要在共享状态上加锁。Python asyncio 天然避免了这个问题。"

> **考官想听**：你知道 Python asyncio 的单线程协程模型，也知道什么情况下会有 race condition。

---

## 七、工程细节类

### Q18: "你的 SSE 流式推送具体是怎么做的？LLM 是流式的吗？"

**标准答案：**

"两层流式。

第一层——LLM 不是流式的。`researchStream()` 方法传的每个 SSE 事件是研究流程的进度更新（'正在规划搜索'、'搜索中'、'反思中'、'写报告中'），不是 LLM 逐个 token 输出。

第二层——从 Python 数据到浏览器。Python 用 FastAPI 的 `EventSourceResponse`，每个事件 `yield {"event": "status", "data": ...}`。Java 用 WebClient 订阅 `/research/stream`，返回 `Flux<String>`。前端用 `fetch` 的 `response.body.getReader()` 逐片读取。

当前 LLM 调用本身不是流式的——`chat()` 方法等完整结果返回。如果改成流式 LLM，用户体验会更好——写报告的时候可以看到文字逐字出现。技术上是可行的——OpenAI SDK 支持 `stream=True`，但要改造 `llm.py` 的三个方法。"

> **考官想听**：你知道流式有两层（进度流 vs 内容流），当前只是进度流。

---

### Q19: "你的 DeepSeek V4 Pro 报 'thinking mode does not support tool_choice' 这个错，你是怎么排查和解决的？"

**标准答案：**

"三步。

第一步——读报错信息：'Thinking mode does not support this tool_choice' → V4 Pro 默认开了推理模式，推理模式和 tool_choice 参数冲突。V4 Pro 的 Function Calling 不是直接可用的。

第二步——看 DeepSeek API 文档确认：thinking mode 和 forced tool_choice 不兼容。工具调用允许用 `tool_choice='auto'`——让 LLM 自己决定是否调工具。但 `structured_output()` 强制 tool_choice，冲突。

第三步——解决方案：每次 API 调用时传 `extra_body={"thinking": {"type": "disabled"}}`。不是关掉模型能力——只是关掉前置推理步骤，模型照样推理，只是不占用 context 来思考。体现在代码里就是 `llm.py` 的三个方法里的那行 `extra_body`。

这也让我意识到——LangChain 的 `init_chat_model()` 有额外的 '适配层' 帮用户处理这种参数差异。我用的是原生 API，要自己处理。但好处是我完全理解为什么这行代码存在。"

> **考官想听**：你展示了一个完整的排查→查文档→修复的流程，不是搜到方案就直接抄。

---

### Q20: "你怎么做测试？怎么保证你改了 Prompt 之后效果是变好不是变坏？"

**标准答案：**

"说实话，目前没有自动化测试——每次改代码后靠手工跑一次验证。这不是最优方案，但我诚实说明。

后来如果要加测试，会做两件事：
1. **回归测试**——预录一些搜索 API 的 mock 数据，用 `unittest.mock` 替换真实的 Tavily/DeepSeek API。这样每次改 Agent 逻辑后，跑一遍同样的输入 → 对比输出。不会因为 Prompt 调整引入功能性 bug。
2. **评测指标**——选 5-10 个固定问题，跑完 Level 2/3/4，用 LLM-as-judge 自动打分（完整性、引用质量、结构清晰度）。每周改 Prompt 后对比指标。

当前版本的'测试'就是——改完代码重启，看报告是否正常生成。对于一个个人项目来说暂时够用。"

> **考官想听**：诚实承认没有测试，但知道该怎么加。

---

## 八、深度追问类

### Q21: "如果 Level 4 Supervisor 做了一个错误的决策——派了一个完全不相关的研究方向——你的系统怎么纠正？"

**标准答案：**

"当前版本**不纠正**。研究员去搜了不相关的方向，结果汇总进 `all_findings`，最终还是要经过压缩和报告生成环节——但压缩和报告生成步骤的 Prompt 要求按主题整理，不相关的内容会被忽略掉。所以不是完全不纠正——是**间接纠正**。

更好的方案：把当前架构的三步流程——压缩 → 合并 → 报告——改成更主动的三步：

1. 每轮结束后，Supervisor 用 think_tool 复盘：'我派的研究员去了正确的方向吗？' 
2. 如果发现有离题的子研究报告，标记为低质量，降低在汇总时的权重
3. 汇总步骤对低质量报告做过滤——不直接丢弃，但不会被当作核心来源

这相当于在循环式反思上再加一层质量控制——是 Level 4.5 的概念。"

> **考官想听**：你知道当前版本的局限，也知道怎么改进。

---

### Q22: "5 个 Level 2 研究员并行跑，总 token 消耗是单路的 5 倍——值吗？"

**标准答案：**

"取决于需求的维度。

**值**：如果是多向对比型问题（'对比 Java、Go、Rust'），三路并行搜到的信息**互补**——三份报告合在一起能完整回答问题。单路搜可能需要很多轮才能覆盖所有维度，最终总 token 差不了多少。

**不值**：如果是单向深度型问题（'量子计算的数学原理'），拆成多个子课题可能重复搜到相同的网页——5 个研究员搜回 10 次同一个 Wikipedia 页面。这种情况应该用单路 + 深层跟踪——一个研究员多次搜索，每次跟踪不同子问题。

所以不是'Level 3 永远更好'。当前版本是用户手动选 Level——但更好的方案是**自动选择**：LLM 先判断问题类型（多向对比 vs 单向深度），再决定用哪一层。这是 Level 5 的雏形。"

> **考官想听**：你知道并行不是万能的，能讲清楚什么场景并行有价值、什么场景浪费。

---

### Q23: "如果 Tavily 搜不到中文内容怎么办？你的中文报告质量会受影响吗？"

**标准答案：**

"会受影响。这是 Tavily 的一个已知局限——它的中文源覆盖率不如英文。

当前缓解措施：
- LLM 拆搜索词时自动检测用户语言，中文问题用中文搜索词——增加匹配中文内容的概率
- 最终报告 prompt 明确规定 '输出语言与用户输入语言一致'——即使搜到的是英文，报告也是中文

如果后续加 DuckDuckGo 搜索引擎（免费，中文覆盖更好），或者在搜索规划 prompt 里加一句 '中文问题优先用中文搜索词并在 .cn/中文论坛/国内平台搜索'——效果会明显提升。这是中文体验优化的明确方向。"

> **考官想听**：你知道中文内容的局限性，也理解为什么需要中文搜索优化。

---

### Q24: "你这个项目最大技术遗憾是什么？如果重做会怎么设计？"

**标准答案：**

"最大的技术遗憾：SSE 流式实现得太晚——`run_agent_with_sse()` 是在 server.py 里重新写了一遍 Agent 逻辑，而不是复用 agent.py 的类。这导致 Agent 逻辑分散在两个文件：agent.py 有完整的 Level 2 类，server.py 有另一个硬编码版本。

如果重做：Agent 类内部应该提供一个**回调/观察者模式**——每个关键步骤调用 `self.on_progress(step, message)` 通知外部监听者。SSE 层只需向 Agent 注册一个回调，Agent 逻辑完全保留在一个文件里。

这让我深刻理解了**关注点分离**——进度推送是展示层的事，不应该侵入到 Agent 核心逻辑。"

> **考官想听**：你有自我反思能力，能指出项目的缺陷并给出改进方案。这是最吸引面试官的回答。

---

## 九、健壮性类

### Q25: "LLM API 调失败或者 Tavily 挂了，你的系统怎么处理？"

**标准答案：**

"三层防护，各管一层的错误。

第一层，LLM 层。在 `llm.py` 的 `_call_with_retry()` 里，429 限流等 5 秒重试，网络错误指数退避（2s→4s→8s），5xx 服务端错误重试三次。只有 401/403/400 这种客户端错误才不重试直接报。

第二层，搜索层。在 `search.py` 的 `_do_search()` 里，Tavily 优先，失败或返回空结果自动降级到 DuckDuckGo。DuckDuckGo 免费且不需要 API Key，搜出来的内容质量不如 Tavily 精细但能用。

第三层，Agent 循环。Level 2 的 while 循环整轮包在 try/except 里。单轮失败不中断全部研究——异常信息作为 tool message 追加到对话历史，Agent 基于已有信息继续跑或者停止写报告。

加起来大概 55 行新代码。LLM 崩一次不致命、Tavily 挂了不瘫痪、Agent 一轮失败不影响总体成果。"

> **考官想听**：三层不是随便叠——每层对标不同的故障点，各有各的降级逻辑。

---

## 十、RAG 与检索类

### Q26: "你的 RAG 是怎么实现的？用了什么向量库、什么 embedding 模型？"

**标准答案：**

"向量库用 Chroma——嵌入式，本地运行，不需要单独装服务。Embedding 模型用 sentence-transformers 的 `paraphrase-multilingual-MiniLM-L12-v2`——118MB、384 维、中英文都支持、CPU 上跑。

切块策略是手写的——段落→句子→字符三级降级：先按 `\n\n` 段落切，段落太长按句号/问号/感叹号切句子，句子还太长按 500 字符硬切 + 100 字符重叠。没用 LangChain 的 TextSplitter——逻辑太简单不值得引入一个依赖。

入库时每个 chunk 带上元数据 `{user_id, doc_id}`，检索时用 Chroma 的 where 过滤——天然支持多租户和会话级文档选择。检索结果按余弦相似度排序，取前 5 条返回给 Agent。

踩过两个坑：一是 sentence-transformers 在企业网络下因为 SSL 证书问题下载失败，用 `ssl._create_unverified_context` 跳过验证解决。二是先用 HashingVectorizer 当临时方案，但它维度不稳定，查一次和存一次的特征数不一样，Chroma 报 'expecting dimension 30, got 1'——后来换了真正的语义模型才解决。"

> **考官想听**：不只是"用了 Chroma"，而是从切块→embedding→入库→检索→过滤的完整链路，以及踩过的实际坑。

---

### Q27: "你的搜索有 Tavily 和 DuckDuckGo，Agent 怎么决定用哪个？"

**标准答案：**

"Agent 不自己决定用哪个。它只看到一个 `search` 工具。背后 `_do_search()` 先调 Tavily，Tavily 失败或返回空结果自动降级到 DuckDuckGo。Agent 不知道、也不关心自己是搜了 Tavily 还是 DuckDuckGo。

这和网卡自动切换 5G/4G 是一个思路——上层应用不应该感知网络层的降级策略。Tavily 精度更高但有 API 限流，DuckDuckGo 免费但搜中文内容不如 Tavily。两个合在一起，核心场景走 Tavily，异常场景走 DuckDuckGo 兜底。

DuckDuckGo 的搜索结果格式和 Tavily 不一样——DuckDuckGo 返回 `{href, title, body}`，Tavily 返回 `{url, title, content, raw_content}`——我在 `_ddg_search` 里做了格式适配，统一成 Tavily 格式再返回。"

> **考官想听**：降级是透明的，不受 Agent 感知。格式适配是实际工程细节。

---

### Q28: "你的 embedding 模型从 HashingVectorizer 换到 sentence-transformers 的过程，具体怎么排查和解决的？"

**标准答案：**

"中间换了三次。

第一次用 HashingVectorizer——已经装了 sklearn，不联网就能用。但发现入库 4 个片段产生 30 维向量，检索 1 个 query 产生 1 维向量，Chroma 报 'expecting dimension 30, got 1'。根因是 HashingVectorizer 的输出维度取决于同时处理的文本数——入库和检索分别调了 `transform()`，各自产生不同的特征维度。

第二次尝试用 DeepSeek embedding API——但它没提供 embedding 端点，返回 404。大多数 OpenAI 兼容的 API 只有 chat 接口没有 embedding。

第三次回到 sentence-transformers——118MB 模型，但下载时报 SSL 证书验证失败。根因是企业/教育网络环境限制了 HTTPS 证书验证。用 `ssl._create_unverified_context` 解决。现在加了模型缓存——首次加载 3 秒，之后所有调用复用内存里的模型实例。

整个过程 2 小时。最后方案确认后改 kb.py 一行代码——从 `_embed_hash` 改成 `_embed_semantic`。Chroma 的接口没变——存的时候传 embedding、搜的时候传 query_embedding，换模型对上层完全透明。"

> **考官想听**：完整的排查链——不是"用了一个模型"，而是三个方案的试验路径和最终的技术决策理由。

---

### Q29: "你的 server.py 从 460 行缩减到 218 行——具体删了什么、为什么、怎么做到的？"

**标准答案：**

"删了 242 行重复的 Agent 逻辑。

问题起源是 SSE 流式推送。`agent.py` 的 `Level2Agent.run()` 是个黑盒——3 分钟跑完只返回一个 report 字符串。为了推送进度，我在 `server.py` 里把 Agent 的整个搜索-反思循环重写了一遍，在每个步骤后插入 `yield` 事件。结果 Level 1/2/3 各有一份手写实现：agent.py 一份、server.py 一份。加新功能时两个文件各改一遍，压缩、澄清、搜索都踩过不同步的坑。

解决方案是 on_progress 回调模式。给每个 Agent 类的构造函数加一个可选回调参数——`__init__(self, on_progress=None)`。Agent 内部关键步骤调用 `self.emit({"step": "searching", ...})`。server.py 侧创建一个 `asyncio.Queue`，传一个把事件放进队列的回调给 Agent，然后从队列读取事件 yield 成 SSE。

效果：server.py 不再包含任何搜索-反思循环代码。Agent 逻辑 100% 归 agent.py。加新功能现在只改 agent.py——所有入口（命令行、SSE、同步）自动受益。

这是观察者模式在异步 Agent 场景下的应用。也是我真正理解'the observer'的价值——Agent 负责发事件，SSE 层负责转格式，两者互不依赖。"

> **考官想听**：不是"I refactored some code"，而是能看到架构问题的根因、重构的动机、用到的设计模式、以及验证它有效的证据（460→218 行）。

---

### Q30: "你的测试策略是什么？怎么保证改了 Prompt 之后 Agent 没退化？"

**标准答案：**

"两层测试。

第一层：单元测试。`test_units.py` 14 个用例，全部测纯函数——`chunk_text()` 5 种输入、`read_file()` 4 种文件类型、URL 去重、正则匹配、格式化输出、中文检测。不调 LLM、不联网，1 秒跑完。改切块逻辑或文件读取逻辑后跑一次验证没退化。

第二层：回归测试。`test_quality.py`——3 个固定问题 × 2 个 Level = 6 次完整 Agent 运行。每次验证 4 条规则：报告非空、有 Markdown 标题、有引用来源、中文问题输出中文。四条全过就是合格。一套跑下来 3-5 分钟，改完 Prompt 或 Agent 逻辑后跑一次。

LLM 自评分不作为通过条件——DeepSeek 写报告后自己打分有偏见。正则规则不依赖任何 LLM，客观、可重复。

目前不依赖 LangSmith、Gemini API 或任何外部评测服务。整个测试套件 0 依赖。"

> **考官想听**：纯函数和 Agent 行为都有覆盖。知道 LLM 自评的局限性（自己夸自己），并明确规定了通过标准（正则是客观的）。
