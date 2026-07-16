"""批量生成评测文档和测试题。"""
import json, os

eval_dir = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# 文档定义：文件名 → Markdown 内容
# ============================================================
DOCS = {
    "doc14_mongodb.txt": """MongoDB NoSQL 文档数据库

MongoDB 是一个面向文档的 NoSQL 数据库，使用类似 JSON 的 BSON 格式存储数据。由 Dwight Merriman 和 Eliot Horowitz 于 2007 年创立。数据以集合（Collection）组织，每个集合包含多个文档（Document），文档之间可以有嵌套结构。

与关系型数据库不同，MongoDB 没有固定的表结构，同一集合内的文档可以有不同的字段。这种灵活的 Schema 适合快速迭代和数据模型多变的场景。MongoDB 支持丰富的查询语法——find() 按条件查询、$gt/$lt 范围比较、$in/$nin 集合匹配、$regex 正则匹配。支持创建索引来加速查询，默认使用 B-tree 索引。

MongoDB 的副本集（Replica Set）提供高可用——一个 Primary 节点处理所有写入，多个 Secondary 节点异步复制数据。Primary 故障时，剩余的 Secondary 通过 Raft 协议选举新的 Primary，实现自动故障转移，通常在 10-30 秒内完成。分片集群（Sharded Cluster）将数据按分片键分散在多个分片上，支持水平扩展。

聚合管道（Aggregation Pipeline）是 MongoDB 的数据处理利器——多个 Stage 按顺序执行，每个 Stage 对文档进行过滤、分组、排序、投影等操作。常用 Stage 包括：$match（过滤）、$group（分组聚合）、$sort（排序）、$project（字段投影）、$lookup（跨集合关联查询，类似 SQL JOIN）。""",

    "doc15_microservices.txt": """微服务架构

微服务架构（Microservices）是一种将单体应用拆分为多个小型独立服务的架构风格。每个服务围绕特定业务能力构建，独立开发、测试、部署和扩展。Martin Fowler 和 James Lewis 在 2014 年系统化地阐述了这一概念。

与单体架构相比，微服务的优势在于：独立部署——某个服务的更新不需要重新部署整个应用；技术异构——不同服务可以选用最适合的编程语言和数据库；故障隔离——单个服务崩溃不会拖垮整个系统；团队自治——小团队独立负责一个或多个服务的全生命周期。

服务间通信是微服务的核心挑战。同步通信常用 HTTP REST 或 gRPC——简单直接但会形成调用链。异步通信常用消息队列——RabbitMQ 和 Kafka 是两种主流选择。RabbitMQ 基于 AMQP 协议，支持复杂的路由规则和消息确认机制，适合需要保证单条消息可靠投递的场景。Kafka 基于分布式日志，支持超高吞吐量和消息回溯，适合流处理和事件溯源。

服务发现（Service Discovery）解决"服务 A 如何找到服务 B 的地址"的问题。客户端发现模式中，客户端从注册中心查询可用实例并自行负载均衡（如 Eureka + Ribbon）。服务端发现模式中，客户端通过负载均衡器访问，由负载均衡器从注册中心获取后端实例（如 Kubernetes Service + kube-proxy）。Consul 和 Nacos 是常用的注册中心，提供健康检查和配置管理功能。""",

    "doc16_go.txt": """Go 编程语言

Go（又称 Golang）是由 Google 的 Robert Griesemer、Rob Pike 和 Ken Thompson 于 2009 年发布的开源编程语言。设计目标是简洁、高效、并发能力强，适合构建网络服务和系统工具。

Go 的并发模型基于 CSP（Communicating Sequential Processes）理论，核心原语是 goroutine 和 channel。goroutine 是轻量级协程——启动一个 goroutine 只需 2KB 栈空间，一个程序可以运行数十万个。channel 用于 goroutine 间的通信和同步，遵循"不要通过共享内存来通信，而通过通信来共享内存"的设计哲学。go 关键字启动 goroutine，<- 操作符从 channel 收发数据。

Go 的语法简洁——没有类继承，通过 struct 组合和 interface 实现多态。interface 是隐式实现的，不需要显式声明"implements"。错误处理通过返回 error 值而非异常，defer 关键字确保资源释放。内置的 gofmt 工具统一代码格式。标准库丰富——net/http 构建 HTTP 服务，database/sql 操作数据库，encoding/json 处理 JSON。

Docker、Kubernetes、Prometheus、Etcd 等重要基础设施项目都是用 Go 编写的。Go 编译为单个静态二进制文件，无需运行时依赖，容器化部署非常友好。模块系统从 1.11 版本引入 go.mod 文件管理依赖。""",

    "doc17_jwt.txt": """JWT 认证机制

JWT（JSON Web Token）是一种基于 JSON 的开放标准（RFC 7519），用于在各方之间安全传输声明。广泛用于 Web 应用的认证和授权——用户登录后，服务端签发 JWT，客户端后续请求携带 JWT 以证明身份。

JWT 由三部分组成，用点号分隔：Header.Payload.Signature。Header 声明算法类型（通常 HS256 或 RS256）。Payload 包含声明（Claims）——如 sub（用户 ID）、exp（过期时间）、iat（签发时间）。Signature 用 Header 指定的算法对前两部分签名：HMACSHA256(base64UrlEncode(header) + "." + base64UrlEncode(payload), secret)。签名确保 Token 未被篡改——任何对 Header 或 Payload 的修改都会导致签名验证失败。

Access Token 和 Refresh Token 是常见的双 Token 策略。Access Token 有效期短（通常 15-30 分钟），直接用于 API 认证。Refresh Token 有效期长（通常 7-30 天），用于获取新的 Access Token 而不需要用户重新登录。Refresh Token 存在服务端可被撤销，Access Token 是无状态的。

JWT 的安全注意事项：密钥（secret）必须足够长且保密——生产环境建议至少 32 字节随机字符串。不要将敏感信息（如密码）放在 Payload 中——Payload 仅 Base64 编码而非加密，任何人可解码。尽量使用 HTTPS 防止 Token 被中间人截获。token 存储在客户端时，httpOnly cookie 比 localStorage 更安全——前者不被 JavaScript 读取，抵御 XSS 攻击。""",

    "doc18_redis2.txt": """Redis 数据类型与应用场景

Redis 支持多种数据类型，远超简单的 key-value 存储：

String（字符串）：最基本类型，可以存文本、数字、序列化对象。支持原子操作如 INCR（自增）、DECR（自减）、SETNX（不存在时设置）。常用于计数器、分布式锁（SETNX + EXPIRE）、缓存序列化对象。

Hash（哈希）：存储字段-值对，适合存储对象。如用户信息：HSET user:1001 name "张三" age 25。支持单字段读写和全量获取，比 String 存整个序列化对象更灵活。常用于购物车、用户资料缓存。

List（列表）：双向链表，左右两端插入/弹出 O(1)。LPUSH + RPOP 可实现消息队列。LRANGE 可按索引范围获取元素，常用于最新动态列表。BLPOP 阻塞弹出可实现简单的工作队列。

Set（集合）：无序、元素唯一。支持交并差集运算：SINTER（交集）可用于计算共同好友，SUNION（并集）合并多个集合，SDIFF（差集）找出差异。SISMEMBER O(1) 判断元素是否存在。

ZSet（有序集合）：每个元素带 score，按 score 排序。ZADD 添加，ZRANGEBYSCORE 按分数范围查询。常用于排行榜（score=得分，member=用户ID）、延迟队列（score=执行时间戳）、滑动窗口限流（score=请求时间）。""",

    "doc19_distributed_lock.txt": """分布式锁

分布式锁是分布式系统中协调多个进程或服务访问共享资源的一种机制。核心要求：互斥——同一时刻只有一个客户端持有锁；无死锁——即使持有锁的客户端崩溃，锁最终能被释放；容错——只要大多数节点正常，锁服务就能正常提供加锁和释放。

基于 Redis 的分布式锁是最常见的实现。Redlock 算法（由 Redis 作者 antirez 提出）：客户端向 N 个独立的 Redis 节点请求锁，使用相同的 key 和随机 value。如果在多数节点（N/2+1）上成功获取且总耗时小于锁的有效期，则认为获取锁成功。释放时向所有节点发送 Lua 脚本删除，删除前先验证 value 是否匹配——防止误删其他客户端持有的锁。

基于 ZooKeeper 的分布式锁利用临时顺序节点。客户端在指定路径下创建临时顺序节点，检查自己是不是序号最小的节点——是则获取锁，否则 watch 前一个节点等待通知。ZooKeeper 通过心跳保持 Session——客户端崩溃后临时节点自动删除，锁自动释放，天然避免死锁。

基于 MySQL 的分布式锁最简单但性能最差——利用唯一索引和行级锁实现。INSERT INTO locks (lock_name) VALUES ('order_lock') 成功表示获取锁；DELETE 释放锁。缺点是重试频繁时对数据库压力大，没有自动过期机制需要额外定时清理。

选择策略：追求性能用 Redis（单机 10 万+ QPS），需要强一致和自动释放用 ZooKeeper，最小依赖用 MySQL（项目已有数据库时）。""",
}

# ============================================================
# 测试题定义
# ============================================================
QUESTIONS = [
    # ---- MySQL ----
    {"type": "simple", "question": "MySQL 的默认存储引擎是什么", "expected_docs": ["doc10_mysql.txt"], "expected_chunks": ["InnoDB"], "ground_truth": "InnoDB 是 MySQL 从 5.5 版本开始的默认存储引擎，支持行级锁、外键约束和崩溃恢复。"},
    {"type": "simple", "question": "MySQL 主从复制的原理是什么", "expected_docs": ["doc10_mysql.txt"], "expected_chunks": ["binlog", "I/O 线程"], "ground_truth": "主库记录 binlog，从库通过 I/O 线程拉取 binlog 并在本地重放。"},
    {"type": "precision", "question": "MySQL InnoDB 索引是什么数据结构", "expected_docs": ["doc10_mysql.txt"], "expected_chunks": ["B+ 树"], "ground_truth": "InnoDB 使用 B+ 树索引结构。"},
    {"type": "colloquial", "question": "怎么查 MySQL 查询慢", "expected_docs": ["doc10_mysql.txt"], "expected_chunks": ["慢查询日志", "EXPLAIN"], "ground_truth": "使用 EXPLAIN 分析执行计划，开启慢查询日志记录耗时 SQL。"},

    # ---- Nginx ----
    {"type": "simple", "question": "Nginx 是谁创建的", "expected_docs": ["doc11_nginx.txt"], "expected_chunks": ["Igor Sysoev"], "ground_truth": "Nginx 由俄罗斯工程师 Igor Sysoev 于 2004 年发布。"},
    {"type": "precision", "question": "Nginx 负载均衡有哪些策略", "expected_docs": ["doc11_nginx.txt"], "expected_chunks": ["轮询", "IP 哈希", "最少连接", "加权轮询"], "ground_truth": "Nginx 支持轮询、加权轮询、IP 哈希、最少连接等策略。"},
    {"type": "colloquial", "question": "Nginx 怎么配置 SSE 流式推送", "expected_docs": ["doc11_nginx.txt"], "expected_chunks": ["proxy_buffering off"], "ground_truth": "关闭代理缓冲 proxy_buffering off 并开启 chunked_transfer_encoding on。"},
    {"type": "simple", "question": "Nginx 反向代理的核心指令是什么", "expected_docs": ["doc11_nginx.txt"], "expected_chunks": ["proxy_pass"], "ground_truth": "proxy_pass 指令将请求转发到后端服务器。"},

    # ---- Git ----
    {"type": "simple", "question": "Git 是谁创建的", "expected_docs": ["doc12_git.txt"], "expected_chunks": ["Linus Torvalds", "2005"], "ground_truth": "Git 由 Linus Torvalds 于 2005 年创建。"},
    {"type": "precision", "question": "Git merge 和 rebase 的区别", "expected_docs": ["doc12_git.txt"], "expected_chunks": ["合并提交", "线性历史"], "ground_truth": "merge 产生合并提交保留分支脉络，rebase 改写历史为线性。不应 rebase 已推送的分支。"},
    {"type": "colloquial", "question": "Git 三个区是什么", "expected_docs": ["doc12_git.txt"], "expected_chunks": ["工作区", "暂存区", "本地仓库"], "ground_truth": "工作区（Working Directory）、暂存区（Staging Area）、本地仓库（Local Repository）。"},
    {"type": "multi_doc", "question": "Git 和 Linux 的创建者分别是谁", "expected_docs": ["doc12_git.txt", "doc4.txt"], "expected_chunks": ["Linus Torvalds"], "ground_truth": "Git 和 Linux 都由 Linus Torvalds 创建。"},

    # ---- Elasticsearch ----
    {"type": "simple", "question": "Elasticsearch 的核心搜索原理是什么", "expected_docs": ["doc13_elasticsearch.txt"], "expected_chunks": ["倒排索引"], "ground_truth": "Elasticsearch 基于 Lucene 的倒排索引——将词映射到包含该词的文档列表。"},
    {"type": "precision", "question": "Elasticsearch 集群的分片和副本是什么", "expected_docs": ["doc13_elasticsearch.txt"], "expected_chunks": ["Shard", "Replica"], "ground_truth": "索引拆为多个分片（Shard），每个分片可以有副本（Replica）。主分片负责写入，副本提供读取和故障转移。"},
    {"type": "colloquial", "question": "ES 怎么查日志", "expected_docs": ["doc13_elasticsearch.txt"], "expected_chunks": ["ELK Stack", "Logstash", "Kibana"], "ground_truth": "ELK Stack——Logstash 采集日志、Elasticsearch 存储和搜索、Kibana 可视化。"},

    # ---- MongoDB ----
    {"type": "simple", "question": "MongoDB 的数据存储格式是什么", "expected_docs": ["doc14_mongodb.txt"], "expected_chunks": ["BSON"], "ground_truth": "MongoDB 使用类似 JSON 的 BSON 格式存储数据。"},
    {"type": "precision", "question": "MongoDB 副本集如何实现故障转移", "expected_docs": ["doc14_mongodb.txt"], "expected_chunks": ["Raft", "Primary", "Secondary"], "ground_truth": "Primary 故障时剩余 Secondary 通过 Raft 协议选举新 Primary，10-30 秒完成。"},
    {"type": "colloquial", "question": "MongoDB 怎么实现 JOIN", "expected_docs": ["doc14_mongodb.txt"], "expected_chunks": ["$lookup", "聚合管道"], "ground_truth": "聚合管道的 $lookup Stage 实现跨集合关联查询，类似 SQL JOIN。"},

    # ---- 微服务 ----
    {"type": "simple", "question": "微服务架构的核心优势是什么", "expected_docs": ["doc15_microservices.txt"], "expected_chunks": ["独立部署", "技术异构", "故障隔离"], "ground_truth": "独立部署、技术异构、故障隔离、团队自治。"},
    {"type": "precision", "question": "RabbitMQ 和 Kafka 的应用场景区别", "expected_docs": ["doc15_microservices.txt"], "expected_chunks": ["消息确认", "流处理"], "ground_truth": "RabbitMQ 适合需要保证单条消息可靠投递，Kafka 适合高吞吐流处理和事件溯源。"},
    {"type": "colloquial", "question": "微服务怎么找到对方", "expected_docs": ["doc15_microservices.txt"], "expected_chunks": ["服务发现", "注册中心", "Consul"], "ground_truth": "通过服务发现机制——客户端或服务端从注册中心（Consul/Nacos）查询可用实例。"},

    # ---- Go ----
    {"type": "simple", "question": "Go 语言的并发模型基于什么理论", "expected_docs": ["doc16_go.txt"], "expected_chunks": ["CSP", "goroutine", "channel"], "ground_truth": "基于 CSP 理论，核心原语是 goroutine 和 channel。"},
    {"type": "precision", "question": "Go 的 goroutine 有什么特点", "expected_docs": ["doc16_go.txt"], "expected_chunks": ["2KB", "轻量级"], "ground_truth": "goroutine 是轻量级协程，启动仅需 2KB 栈空间，可运行数十万个。"},
    {"type": "multi_doc", "question": "Go 语言和 Docker 是什么关系", "expected_docs": ["doc16_go.txt", "doc3.txt"], "expected_chunks": ["Go", "Docker"], "ground_truth": "Docker 是用 Go 语言编写的，Go 编译为单二进制文件的特性使其适合容器化部署。"},

    # ---- JWT ----
    {"type": "simple", "question": "JWT 由哪三部分组成", "expected_docs": ["doc17_jwt.txt"], "expected_chunks": ["Header", "Payload", "Signature"], "ground_truth": "JWT 由 Header、Payload、Signature 三部分组成，用点号分隔。"},
    {"type": "precision", "question": "JWT 的 Access Token 和 Refresh Token 有什么区别", "expected_docs": ["doc17_jwt.txt"], "expected_chunks": ["15分钟", "无状态", "可被撤销"], "ground_truth": "Access Token 短期有效（15-30分钟）无状态不可撤销，Refresh Token 长期有效（7-30天）可撤销。"},
    {"type": "colloquial", "question": "JWT 安全怎么保证", "expected_docs": ["doc17_jwt.txt"], "expected_chunks": ["httpOnly cookie", "HTTPS", "32 字节"], "ground_truth": "密钥至少 32 字节随机字符串，使用 HTTPS 传输，httpOnly cookie 存储防 XSS。"},
    {"type": "no_answer", "question": "JWT 签名用 RS256 比 HS256 快多少", "expected_docs": [], "expected_chunks": [], "ground_truth": "知识库中未涉及 RS256 和 HS256 的性能对比数据。"},

    # ---- Redis 数据类型 ----
    {"type": "simple", "question": "Redis 有哪些数据类型", "expected_docs": ["doc18_redis2.txt"], "expected_chunks": ["String", "Hash", "List", "Set", "ZSet"], "ground_truth": "String、Hash、List、Set、ZSet（有序集合）五种核心数据类型。"},
    {"type": "precision", "question": "Redis ZSet 适合什么场景", "expected_docs": ["doc18_redis2.txt"], "expected_chunks": ["排行榜", "延迟队列", "score"], "ground_truth": "ZSet 适合排行榜（按 score 排序）、延迟队列、滑动窗口限流。"},
    {"type": "multi_doc", "question": "Redis 的分布式锁和主从复制分别怎么实现", "expected_docs": ["doc18_redis2.txt", "doc10_mysql.txt", "doc19_distributed_lock.txt"], "expected_chunks": ["Redlock", "SETNX"], "ground_truth": "分布式锁用 SETNX 或 Redlock 算法，主从复制通过 binlog 实现。"},

    # ---- 分布式锁 ----
    {"type": "simple", "question": "分布式锁有哪几种常见实现方式", "expected_docs": ["doc19_distributed_lock.txt"], "expected_chunks": ["Redis", "ZooKeeper", "MySQL"], "ground_truth": "基于 Redis、ZooKeeper、MySQL 三种主流实现方式。"},
    {"type": "precision", "question": "Redlock 算法如何获取锁", "expected_docs": ["doc19_distributed_lock.txt"], "expected_chunks": ["多数节点", "N/2+1"], "ground_truth": "向 N 个独立 Redis 节点请求锁，在多数节点（N/2+1）上成功获取则锁获取成功。"},
    {"type": "colloquial", "question": "分布式锁防止别人删我的锁怎么办", "expected_docs": ["doc19_distributed_lock.txt"], "expected_chunks": ["value 匹配", "Lua 脚本"], "ground_truth": "释放锁前用 Lua 脚本验证 value 是否匹配，防止误删其他客户端的锁。"},
    {"type": "no_answer", "question": "分布式锁的最佳超时时间设多少", "expected_docs": [], "expected_chunks": [], "ground_truth": "没有固定值——取决于业务操作的最大耗时，通常设业务操作耗时 3-5 倍的冗余。"},

    # ---- 跨文档 ----
    {"type": "multi_doc", "question": "MySQL、MongoDB 和 Redis 的数据模型有什么区别", "expected_docs": ["doc10_mysql.txt", "doc14_mongodb.txt", "doc18_redis2.txt"], "expected_chunks": ["关系型", "文档", "key-value"], "ground_truth": "MySQL 关系型表格模型，MongoDB 文档 BSON 模型，Redis key-value 多数据类型。"},
    {"type": "multi_doc", "question": "消息队列在哪些场景被使用", "expected_docs": ["doc7_kafka.txt", "doc15_microservices.txt"], "expected_chunks": ["Kafka", "异步通信"], "ground_truth": "Kafka 在微服务异步通信、流处理、日志收集等场景使用。"},
]

# ============================================================
# 写入文档
# ============================================================
for filename, content in DOCS.items():
    filepath = os.path.join(eval_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  文档: {filename}")

# ============================================================
# 加载已有测试集并合并
# ============================================================
old_path = os.path.join(eval_dir, "..", "src", "researcher", "evaluation", "golden_testset_v2.json")
with open(old_path, encoding="utf-8") as f:
    old_set = json.load(f)

merged = old_set + QUESTIONS
print(f"\n  旧测试集: {len(old_set)} 题")
print(f"  新增: {len(QUESTIONS)} 题")
print(f"  合并: {len(merged)} 题")

# 题型分布
from collections import Counter
types = Counter(item["type"] for item in merged)
print(f"  题型分布: {dict(types)}")

# 保存合并后的
out_path = os.path.join(eval_dir, "..", "src", "researcher", "evaluation", "golden_testset_v3.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(merged, f, ensure_ascii=False, indent=2)

print(f"\n  已保存: {out_path}")
print("  文档数:", len(DOCS) + 9, "篇")
