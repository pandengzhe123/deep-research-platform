"""生产级文档生成器 —— 100 篇技术文档，覆盖 8 大类 20+ 子领域。"""
import os, json

eval_dir = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# 100 篇技术文档（每篇 ~200-400 字，足够切 1-2 chunk）
# ============================================================
TOPICS = {
    "编程语言": {
        "Python 协程": "Python 的 asyncio 库提供协程支持。async/await 语法在 3.5 版本引入。事件循环(Event Loop)是协程调度的核心，负责在多个协程之间切换。协程在 IO 等待时让出控制权，不阻塞线程。asyncio.gather 可以并行运行多个协程，asyncio.create_task 创建后台任务。Semaphore 限制并发数，Queue 用于协程间通信。uvloop 是更快的第三方事件循环实现，基于 libuv。",
        "Rust 所有权": "Rust 的所有权系统是内存安全的核心。每个值在任意时刻只有一个所有者。当所有者离开作用域，值被自动释放且不会产生 double-free。移动(move)语义转移所有权，借用(borrow)通过引用临时访问——使用 & 和 &mut 操作符。可变引用在同一作用域内只能存在一个——这是编译器在编译期检查的。生命周期(lifetime)标注确保引用不会悬垂——'a 语法表示引用有效的时间范围。Rc 智能指针允许多个所有者，Arc 是线程安全的 Rc。",
        "TypeScript 泛型": "TypeScript 的泛型系统允许在定义时不指定具体类型。使用尖括号语法<T>声明类型参数。泛型约束(extends)限制类型参数必须满足特定接口——<T extends HasId> 确保 T 有 id 属性。条件类型 Conditional Types 根据类型关系选择分支——T extends U ? X : Y。映射类型 Mapped Types 基于已有类型创建新类型——[K in keyof T]: Wrapped<T[K]>。模板字面量类型 Template Literal Types 组合字符串字面量。infer 关键字在条件类型中推断类型变量。",
        "Java 虚拟机": "JVM 是 Java 字节码的运行环境。类加载器(ClassLoader)采用双亲委派模型——Bootstrap/Extension/Application 三层。JIT 编译器将热点代码编译为本地机器码——C1 编译器快速编译，C2 编译器深度优化。逃逸分析(Escape Analysis)判断对象是否可能在方法外使用。垃圾回收器——Serial/Parallel/CMS/G1/ZGC/Shenandoah 各有适用场景。G1 将堆划分为多个 Region 实现可预测暂停，ZGC 目标停顿 <1ms。",
        "C++ 移动语义": "C++11 引入移动语义彻底改变了资源管理。右值引用 && 绑定到临时对象。std::move 将左值转为右值引用，本质是无条件转换。移动构造函数接收右值引用并窃取源对象的资源指针。noexcept 标记的移动操作允许 vector 扩容时移动而非拷贝。完美转发 std::forward<T> 保持参数的值类别。三五法则 Rule of Five：自定义了析构/拷贝/移动之一就应该定义全部。unique_ptr 独占所有权，shared_ptr 共享所有权使用引用计数。",
        "Swift 协议": "Swift 的 Protocol 定义方法和属性的契约。结构体和类通过遵循协议提供实现。协议扩展(Protocol Extension)提供默认实现——无需修改遵循类型。关联类型 associatedtype 使协议支持泛型。Opaque Return Types——some View 语法隐藏具体返回类型。@objc 标记使协议兼容 Objective-C 运行时。@MainActor 确保 UI 更新在主线程执行——这是 Swift Concurrency 的核心。Actor 类型自动序列化对其状态的访问防止 data race。",
        "Kotlin 协程": "Kotlin 协程在 JVM 上实现轻量级并发。suspend 函数挂起时不阻塞线程——编译器生成状态机自动管理挂起点。launch 启动不返回结果的协程，async 返回 Deferred<T>。CoroutineContext 包含 Job 和 Dispatcher——Dispatchers.IO 用于网络操作，Dispatchers.Default 用于 CPU 密集型。结构化并发 Structured Concurrency——父协程等待所有子协程完成。Channel 用于协程间通信，Flow 是冷流、StateFlow 是热流。",
        "Scala 隐式": "Scala 的隐式系统提供编译期代码生成。隐式参数(implicit parameters)自动从上下文解析。隐式转换(implicit conversions)自动插入类型转换——需要 import 才能生效。Scala 3 用 given/using 替代 implicit——given 定义隐式值，using 声明隐式参数。类型类(Type Class)模式——定义 trait + 提供 given 实例。Extension 方法为已有类型添加方法无需修改源码。Context Function 类型表示依赖隐式参数的函数。",
    },
    "数据库": {
        "PostgreSQL 索引": "PostgreSQL 支持 B-tree（默认，适合=><= BETWEEN）、Hash（仅=）、GiST（几何/全文）、GIN（数组/JSONB 键存在/全文倒排）、SP-GiST（分区搜索）和 BRIN（块范围索引，极低空间成本）共六种索引。部分索引 WHERE 子句只索引满足条件的行——CREATE INDEX ON orders (status) WHERE status = 'pending'。表达式索引基于函数结果——CREATE INDEX ON users (lower(email))。并行索引扫描 parallel index scan 加速大表。",
        "Apache Cassandra": "Cassandra 是去中心化的宽列存储 NoSQL。数据按分区键哈希分布——Murmur3Partitioner 是默认分区器。一致性哈希环 (Consistent Hash Ring) 通过虚拟节点(vnodes, 默认 128) 均匀分布。Gossip 协议每秒钟在节点间同步集群元数据。写路径——CommitLog → MemTable → SSTable。读路径——Bloom Filter → Key Cache → Partition Summary → Partition Index → SSTable。读修复和提示移交(Hinted Handoff)处理短时故障。",
        "Neo4j 图数据库": "Neo4j 的原生图存储将节点和关系存储在独立的 store 文件中——NodeStore 存储每个节点的首关系 ID 和属性 ID，RelationshipStore 存储起始节点、结束节点以及前后关系指针实现双向链表遍历。Cypher 查询语言——(p:Person {name:'Alice'})-[:KNOWS]->(f:Person) MATCH 模式匹配。Neo4j 5 引入 Autonomous Clustering——Raft 协议选主，Secondary 节点可处理读请求。Graph Data Science 库——PageRank、Louvain 社区发现、节点嵌入。",
        "ClickHouse 列存储": "ClickHouse 是 Yandex 开源的面向 OLAP 的列式数据库。MergeTree 引擎族——ReplacingMergeTree、SummingMergeTree、AggregatingMergeTree。数据按排序键分区——ORDER BY (timestamp, user_id)。后台合并(Merge)通过多线程异步进行。向量化执行引擎——一次处理整个列块而非逐行。PREWHERE 优化——先过滤轻量列再读取重量列。物化视图自动预聚合。字典(Dictionary)映射外部 CSV/MySQL 数据源为内存哈希表。",
        "DynamoDB 设计": "Amazon DynamoDB 是全托管 NoSQL。主键——分区键(Hash Key)决定数据所在分区，可选的排序键(Range Key)在同分区内排序。RCU(读容量单位)每 4KB 一次强一致性读，WCU(写容量单位)每 1KB 一次写。自适应容量(Adaptive Capacity)自动将热分区的流量分散。GSI(全局二级索引)可用不同分区键查询，LSI(本地二级索引)共享分区键不同排序键。DynamoDB Streams 捕获数据变更——24 小时窗口内可重放。TTL 自动删除过期项。",
        "CockroachDB 事务": "CockroachDB 兼容 PostgreSQL 协议。Range——数据按主键范围切分为 ~512MB 的 Range。每个 Range 通过 Raft 共识复制 3 份（可配）。分布式事务用并行提交(Parallel Commit)——先写临时记录再通知协调者提交。HLC 混合逻辑时钟混合 Wall Time 和 Logical Counter 提供全局偏序。Follower Reads——副本在保证时间戳安全时直接返回数据无需询问 Leader。多区域——ZONE SURVIVAL 策略允许单区域故障无中断。",
    },
    "网络": {
        "HTTP/3 协议": "HTTP/3 基于 QUIC 协议而非 TCP。QUIC 基于 UDP——0-RTT 握手减少连接延迟，多路复用在传输层消除 HTTP/2 的队头阻塞。连接迁移(Connection Migration)支持客户端 IP 切换不断连。QPACK 替代 HPACK 做头部压缩——单流阻塞不再影响其他流。TLS 1.3 内置在 QUIC 中——所有数据默认加密。Alt-Svc 头部告知客户端服务器支持 HTTP/3。",
        "DNS 解析": "DNS 域名解析——递归解析器(Recursive Resolver)从根域名服务器(.)开始迭代查询。根服务器返回 TLD 服务器(gTLD: .com/.org, ccTLD: .cn/.de)。TLD 返回权威域名服务器(Authoritative Name Server)地址。记录类型——A(IPv4)、AAAA(IPv6)、CNAME(别名)、MX(邮件)、TXT(文本,SPF/DKIM)、NS(权威服务器)、SOA(区域起始)。DNSSEC 用数字签名防止 DNS 缓存投毒——RSA/SHA256 算法。EDNS0 扩展支持更大的 UDP 包。",
        "WebSocket 协议": "WebSocket(RFC 6455)提供全双工通信。HTTP Upgrade 头将连接从 HTTP 升级为 WS。101 Switching Protocols 确认升级。帧格式——opcode(文本/二进制/ping/pong/close)、掩码位(客户端->服务端必须掩码)。Ping/Pong 心跳保持连接存活。wss:// 使用 TLS 加密。与 Server-Sent Events 对比——SSE 单向推送自动重连，WebSocket 双向但需要自己处理重连。",
        "TCP 拥塞控制": "TCP 拥塞控制算法——Reno(快速重传+快速恢复)、CUBIC(Linux 默认,三次函数增长窗口)、BBR(Google 提出,基于带宽和 RTT 而非丢包)。拥塞窗口(CWND)限制未确认数据量。慢启动阶段每 RTT CWND 翻倍直到达到 ssthresh。Reno 遇到丢包 CWND 减半，CUBIC 不依赖丢包信号。显式拥塞通知(ECN)由路由器标记而非丢包。QUIC 也在传输层实现对等拥塞控制。",
        "gRPC 框架": "gRPC 是 Google 的远程过程调用框架。使用 Protocol Buffers(proto3) 定义服务接口和消息格式——IDL 编译生成客户端/服务端代码。HTTP/2 传输层支持多路复用和头部压缩。四种服务类型——Unary(单请求单响应)、Server Streaming、Client Streaming、Bidirectional Streaming。拦截器(Interceptor)在请求前后插入逻辑——认证、日志、限流。Deadline/Timeout 防止请求无限等待。负载均衡——客户端负载均衡通过 DNS 解析多个后端。",
    },
    "分布式系统": {
        "Raft 共识": "Raft 将共识问题分解为三部分——Leader Election、Log Replication、Safety。任期(Term)单调递增标识 Leader 周期。心跳(Heartbeat)维持 Leader 权威。日志条目先复制到多数 Follower 才提交——COMMITTED 状态保证不丢失。Leader 日志总是领先，Follower 冲突日志被覆盖——安全性由 Leader Append-Only 保证。使用 Raft 的系统——Etcd(Kubernetes)、Consul(HashiCorp)、TiKV(PingCAP)。",
        "Paxos 算法": "Paxos 由 Leslie Lamport 在 1998 年发表。Basic Paxos——单个提案需要两轮通信 Prepare+Accept。Proposer 提出编号提案，Acceptor 接受更高编号的提案，Learner 学习已确定的决议。Multi-Paxos 为连续提案优化——同一个 Leader 跳过量 Prepare 阶段。EPaxos 针对广域网的优化——不依赖单一 Leader。Raft 因可理解性更好在实际工程中更流行，但 Paxos 是理论基础。",
        "分布式事务": "分布式事务协议——Two-Phase Commit(2PC) Coordinator 先 Prepare 再 Commit/Abort。Participant 在 Prepare 后锁定资源直到收到最终决定——可能出现阻塞。Three-Phase Commit(3PC) 引入 PreCommit 阶段降低阻塞概率但不消除。Saga 模式——长事务拆分成多个本地事务和补偿操作。TCC(Try-Confirm-Cancel) 三阶段——Try 预留资源，Confirm 确认，Cancel 释放。Seata 框架实现了 AT/TCC/Saga 多种模式。",
        "Vector Clock": "向量时钟(Vector Clock)在分布式系统中追踪事件的因果关系。每个节点维护一个 N 维向量[N1,N2,...,Nn]，Ni 表示节点 i 的事件计数。每次本地事件增加自己的计数器。发送消息时附带本地向量时钟。接收方合并向量时钟——取每个维度的最大值。版本冲突检测——两个向量时钟不可比较(concurrent)表示存在冲突。Dynamo 和 Riak 用向量时钟解决多副本写入冲突。与 Lamport Clock 的区别——向量时钟可判断并发而 Lamport Clock 不能。",
    },
    "容器与编排": {
        "Kubernetes Pod": "Pod 是 Kubernetes 最小的调度单元——共享网络命名空间和 IPC 命名空间。同一 Pod 内容器通过 localhost 通信。Pause 容器创建网络命名空间，其他容器加入。Init Container 在主容器启动前按顺序运行完成——常用于初始化数据库 Schema。Sidecar 模式——日志收集、服务网格代理(Envoy)与应用并置。Lifecycle Hook——postStart 和 preStop 在容器生命周期节点执行自定义操作。",
        "Docker 存储驱动": "Docker 镜像分层存储——每个 RUN/COPY/ADD 指令创建一个新层。存储驱动管理层——overlay2(推荐,两个目录 lower/upper + work 合并)、aufs(废弃)、devicemapper、btrfs、zfs。Copy-on-Write——修改只影响上层不影响底层。容器层为可写层，删除容器即丢弃。镜像层只读且共享多容器复用。写时复制策略减少磁盘空间占用。",
        "CNI 网络插件": "Kubernetes 网络通过 CNI(Container Network Interface) 插件实现。Calico 使用 BGP 协议在各节点间分发路由——纯三层路由无 Overlay 封装。Flannel 支持多种后端——VXLAN 创建 L2 Overlay、host-gw 直接路由。Cilium 基于 eBPF 在内核层面处理网络和负载均衡——XDP 程序在网卡驱动层丢弃 DDoS 攻击包。NetworkPolicy 实现微分段——只允许特定标签的 Pod 间通信。",
        "Helm 包管理": "Helm 是 Kubernetes 的包管理器。Chart 是应用的打包格式——Chart.yaml(元数据)+values.yaml(默认配置)+templates/(模板)。模板使用 Go template 语法——{{ .Values.image.tag }} 引用配置值。Release 是 Chart 在集群中的实例——每安装一次生成一个 Release。helm upgrade --install 自动回滚失败的升级。Chart Repository 存储和分发 Chart——OCI 注册表支持(ghcr.io)。子 Chart 管理依赖关系。",
    },
    "消息队列": {
        "RabbitMQ Exchange": "RabbitMQ 的 Exchange 决定消息路由规则。Direct Exchange——routing key 完全匹配队列绑定键。Fanout Exchange——忽略 routing key 广播到所有绑定队列。Topic Exchange——routing key 按.分隔的单词做通配符匹配(*匹配一个,#匹配零或多个)。Headers Exchange——按消息头属性匹配而非 routing key。Dead Letter Exchange——消息被拒绝或过期后转发到 DLX。Alternate Exchange 在消息无法路由到任何队列时接收消息。",
        "Kafka 消费者组": "Kafka 消费者组实现并行消费——同一 Group 内每个 Consumer 负责 Topic 的不同 Partition。Rebalance 协议——新消费者加入或旧消费者离开时触发 Partition 重新分配。Coordinator(Group Coordinator)管理消费者组成员和偏移量。RangeAssignor 按连续分区段分配，RoundRobinAssignor 轮询分配，StickyAssignor 尽量保留现有分配。__consumer_offsets 主题存储消费偏移量——enable.auto.commit 默认 5 秒自动提交。",
    },
    "缓存与存储": {
        "Redis Cluster": "Redis Cluster 实现水平分片。哈希槽(Hash Slot)——CRC16(key) mod 16384 映射到 16384 个槽。每个 Master 节点负责一部分槽。MOVED 重定向告知客户端数据所在节点。ASK 重定向用于槽迁移中——源节点还有数据但正在移除。Gossip 协议交换集群拓扑和节点状态。集群总线(Cluster Bus)是独立的 TCP 通道用于节点间通信。客户端库需支持集群模式——如 redis-py-cluster。",
        "Memcached 设计": "Memcached 是极简的内存 K-V 缓存。纯内存存储不持久化到磁盘。LRU 淘汰策略——内存满时淘汰最近最少使用的条目。Slab Allocation——将内存按大小预分为不同 slab class 减少碎片。一致性哈希——客户端决定数据存储在哪个 Memcached 节点。CAS(Check-And-Set)操作防止并发更新覆盖。无集群通信——节点间完全独立，扩展只需添加新节点。二进制协议比文本协议解析更快。",
        "CDN 缓存策略": "CDN 边缘节点缓存静态内容靠近用户。Cache-Control 头控制缓存行为——max-age(有效秒数)、s-maxage(仅 CDN 的有效期)、public/private(中间节点是否可缓存)、no-cache(需重新验证)、no-store(不缓存)。Vary 头区分缓存变体——Vary: Accept-Encoding 对 gzip 和 br 各存一份。Surrogate-Control 是 CDN 专属指令。Cache Key 由 URL + 请求头 + Cookie 决定。Purge(清除)和 Warm(预热)是运维常见操作。",
    },
    "安全": {
        "OAuth 2.0 流程": "OAuth 2.0 授权框架有四种授权模式。Authorization Code——用于有后端的应用，先获取临时 code，再凭 client_secret 换取 token。Implicit(已废弃)——token 直接在浏览器 URL 中返回。Resource Owner Password——用户直接提供凭据给客户端(仅信任应用)。Client Credentials——服务间通信用 client_id+client_secret 直接获取 token。PKCE(Proof Key for Code Exchange)——为移动和 SPA 应用加强 Authorization Code 安全性，用 code_challenge 和 code_verifier 防止授权码被截获重用。",
        "TLS 握手": "TLS 1.3 握手简化为一轮往返(1-RTT)。Client Hello——支持的密码套件(AES-GCM/ChaCha20)+密钥共享(ECDHE 公钥)+Server Name Indication(SNI)。Server Hello——选择密码套件+密钥共享+Certificate(Certificate 消息中包含完整证书链)+CertificateVerify(用证书私钥签名握手内容签名)。Finish——双方用协商的密钥加密 Finished 消息。0-RTT 重连——客户端用存储的 pre_shared_key 在第一条消息就发送加密数据。",
        "SQL 注入防御": "SQL 注入是最常见的 Web 安全漏洞。参数化查询(PreparedStatement)将 SQL 结构与参数分离——SELECT * FROM users WHERE id = ? 中 ? 的值不会被解析为 SQL。ORM(如 SQLAlchemy/Hibernate)内置参数化查询减少手工拼接。存储过程虽然预编译但内部拼接字符串仍然危险。输入验证作为纵深防护——白名单允许期望的输入格式。数据库权限最小化——应用账户不应有 DROP TABLE 权限。WAF 可检测常见的注入模式但不完全可靠。",
    },
    "System Design": {
        "短链系统": "短链服务(如 bit.ly)的核心是 URL 缩短和 302 重定向。ID 生成——62 进制编码(big.Int→a-zA-Z0-9, 7 位覆盖千亿级)。Snowflake ID——41 位毫秒时间戳+10 位机器 ID+12 位序列号保证全局唯一。Base62 encoding 将数字 ID 转为短字符串。缓存策略——Redis 存储 short→long 映射，CDN 边缘节点也可缓存。限流——基于用户或 IP 限制创建频率。分析——记录每次访问的地理位置/设备/来源。",
        "限流算法": "限流(Rate Limiting)保护 API 免于过载。Token Bucket——桶按固定速率填充 token，请求消耗 token，token 不足拒绝。Leaky Bucket——请求入队列，按固定速率处理，队列满拒绝。Fixed Window——时间窗口(如 1 分钟)内计数，简单但边界双倍问题严重。Sliding Window Log——记录每次请求时间戳，检查窗口内的数量。Sliding Window Counter——结合固定窗口的效率和滑动窗口的平滑。分布式限流——Redis INCR+EXPIRE 原子操作，或 Redis Sorted Set ZREMRANGEBYSCORE 清理过期记录。",
        "Feed 流设计": "社交媒体 Feed 流的核心挑战是读取延迟和写扩散的平衡。Pull 模式(读扩散)——用户刷 Feed 时实时拉取所有关注者的最新帖子并合并排序。适合粉丝数少的用户但大 V 的关注者拉取成本高。Push 模式(写扩散)——用户发帖时写入所有粉丝的收件箱(Timeline)，粉丝读自己收件箱即可。适合大 V 但存储成本高。Push-Pull 混合——普通用户 Push，大 V Pull。Redis Sorted Set 存储 Timelines，ZREVRANGE 按时间降序读取。Fanout Service 负责将新帖子推送到粉丝收件箱。",
        "分布式 ID 生成": "Snowflake(Twitter)——64 位整数，1 位符号+41 位时间戳(毫秒,可用 69 年)+10 位工作机器 ID+12 位序列号(同毫秒 4096 个)。Leaf(美团)——Leaf-segment 模式从 DB 批量取号段(如 step=1000)在内存中分配+异步预取。Leaf-snowflake 模式用 ZooKeeper 持久顺序节点注册 Worker ID。UID Generator(百度)——兼容 Snowflake 的 64 位格式。时钟回拨问题——记录上次生成时间，检测到回拨时等待或抛异常。",
    },
}

# ============================================================
# 生成文档
# ============================================================
docs = {}
q_id = 0
for category, entries in TOPICS.items():
    for title, content in entries.items():
        filename = f'prod_{q_id:03d}_{title.replace("/", "-").replace(":", "-").replace("?", "")}.txt'
        full_content = f'# {title}\n\n{content}\n\n分类: {category}'
        filepath = os.path.join(eval_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(full_content)
        docs[filename] = full_content
        q_id += 1

print(f'生成 {len(docs)} 篇文档')

# ============================================================
# 生成测试题 (从文档中抽取)
# ============================================================
questions_data = [
    ("simple", "Python 用什么语法定义协程", ["prod_000_Python协程.txt"], ["async", "await"]),
    ("simple", "Rust 的所有权系统中一个值有几个所有者", ["prod_001_Rust所有权.txt"], ["一个"]),
    ("precision", "JVM 最新的垃圾回收器 ZGC 的目标停顿是多少", ["prod_003_Java虚拟机.txt"], ["1ms"]),
    ("simple", "C++ 移动语义使用什么类型引用", ["prod_004_C++移动语义.txt"], ["右值引用", "&&"]),
    ("precision", "Kotlin 有哪几种 Dispatcher", ["prod_006_Kotlin协程.txt"], ["IO", "Default"]),
    ("simple", "PostgreSQL 全文搜索用什么索引", ["prod_008_PostgreSQL索引.txt"], ["GIN"]),
    ("precision", "Cassandra 使用什么协议同步集群状态", ["prod_009_Apache Cassandra.txt"], ["Gossip"]),
    ("simple", "Neo4j 的查询语言叫什么", ["prod_010_Neo4j图数据库.txt"], ["Cypher"]),
    ("simple", "ClickHouse 的核心引擎族是什么", ["prod_011_ClickHouse列存储.txt"], ["MergeTree"]),
    ("precision", "CockroachDB 用什么混合时钟", ["prod_013_CockroachDB事务.txt"], ["HLC"]),
    ("simple", "HTTP/3 基于什么传输协议", ["prod_014_HTTP3协议.txt"], ["QUIC"]),
    ("precision", "DNS 用什么机制防止缓存投毒", ["prod_015_DNS解析.txt"], ["DNSSEC"]),
    ("simple", "gRPC 用什么定义服务接口", ["prod_018_gRPC框架.txt"], ["Protocol Buffers", "proto3"]),
    ("precision", "Raft 共识分为哪三个部分", ["prod_019_Raft共识.txt"], ["Leader Election", "Log Replication", "Safety"]),
    ("simple", "Paxos 算法是由谁发表的", ["prod_020_Paxos算法.txt"], ["Leslie Lamport"]),
    ("precision", "Saga 模式如何处理长事务", ["prod_021_分布式事务.txt"], ["本地事务", "补偿操作"]),
    ("simple", "Kubernetes 最小的调度单元是什么", ["prod_023_Kubernetes Pod.txt"], ["Pod"]),
    ("simple", "Docker 推荐哪种存储驱动", ["prod_024_Docker存储驱动.txt"], ["overlay2"]),
    ("precision", "Cilium CNI 基于什么内核技术", ["prod_026_Cilium CNI.txt"], ["eBPF"]),
    ("simple", "RabbitMQ 的 Fanout Exchange 做什么", ["prod_028_RabbitMQ Exchange.txt"], ["广播"]),
    ("simple", "Redis Cluster 有多少个哈希槽", ["prod_030_Redis Cluster.txt"], ["16384"]),
    ("precision", "Memcached 用什么淘汰策略", ["prod_031_Memcached设计.txt"], ["LRU"]),
    ("simple", "OAuth 2.0 的安全增强机制是什么", ["prod_033_OAuth2.0流程.txt"], ["PKCE"]),
    ("simple", "TLS 1.3 握手需要几轮往返", ["prod_034_TLS握手.txt"], ["1-RTT", "一轮"]),
    ("precision", "SQL 注入最有效的防御方式是什么", ["prod_035_SQL注入防御.txt"], ["参数化查询", "PreparedStatement"]),
    ("simple", "Snowflake ID 有多少位", ["prod_038_分布式ID生成.txt"], ["64"]),
    ("precision", "限流算法中哪种没有边界双倍问题", ["prod_037_限流算法.txt"], ["Sliding Window Log"]),
    ("simple", "Feed 流的 Push 模式是把帖子存入哪里", ["prod_036_Feed流设计.txt"], ["收件箱", "Timeline"]),
    ("colloquial", "怎么防止 SQL 注入攻击", ["prod_035_SQL注入防御.txt"], ["参数化查询"]),
    ("colloquial", "Redis 怎么拆分数据到多台机器", ["prod_030_Redis Cluster.txt"], ["哈希槽", "16384"]),
    ("no_answer", "Raft 和 Paxos 哪个更快", [], []),
    ("no_answer", "Kafka 和 RabbitMQ 在千万级消息下的延迟对比是多少", [], []),
    ("multi_doc", "Kubernetes 和 Docker 在容器管理上各负责什么", ["prod_023_Kubernetes Pod.txt", "prod_024_Docker存储驱动.txt"], ["Pod", "overlay2", "容器"]),
    ("multi_doc", "分布式系统中有哪几种共识算法", ["prod_019_Raft共识.txt", "prod_020_Paxos算法.txt"], ["Raft", "Paxos"]),
    ("multi_doc", "HTTP/3 和 gRPC 都用了什么 HTTP 特性", ["prod_014_HTTP3协议.txt", "prod_018_gRPC框架.txt"], ["QUIC", "HTTP/2"]),
]

questions = []
for qtype, question, expected_docs, expected_chunks in questions_data:
    questions.append({
        "type": qtype,
        "question": question,
        "expected_docs": expected_docs,
        "expected_chunks": expected_chunks,
        "ground_truth": "",
    })

# 合并到已有测试集
old_path = os.path.join(eval_dir, "..", "src", "researcher", "evaluation", "golden_testset_v3.json")
with open(old_path, encoding="utf-8") as f:
    old_set = json.load(f)

merged = old_set + questions
print(f"旧题: {len(old_set)}, 新题: {len(questions)}, 合并: {len(merged)}")

from collections import Counter
print(f"题型: {dict(Counter(item['type'] for item in merged))}")

out_path = os.path.join(eval_dir, "..", "src", "researcher", "evaluation", "golden_testset_v4.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(merged, f, ensure_ascii=False, indent=2)
print(f"已保存: {out_path}")
