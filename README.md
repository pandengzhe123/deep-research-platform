# DeepResearch Platform

> Java 网关 + Python Agent 的深度研究智能体平台 —— 开箱即用，中文友好。

## 一句话

双点启动，输入问题，自动搜索网络 + 本地知识库，生成带引用的深度研究报告。

## 架构

```
浏览器 → Java 网关（Spring Boot）→ Python Agent（LangGraph）→ Tavily / RAG
```

## 比原项目强在哪

- **双击启动**，不用配 Python 环境
- **自带 Web UI**，不用打开外部网站
- **RAG 混合检索**，实时搜索 + 私有知识库
- **一键导出** Markdown / PDF / Word
- **提交前估费**，不会莫名其妙烧 500 万 token
- **中文优化**，自动匹配中文搜索策略

详见 [docs/java-gateway-guide.md](docs/java-gateway-guide.md)

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/你的用户名/deep-research-platform.git
cd deep-research-platform

# 2. 启动
docker compose up -d

# 3. 打开浏览器
http://localhost:8080
```

## 技术栈

| 层 | 技术 |
|---|---|
| 网关 | Java 21 + Spring Boot 3 + WebFlux |
| Agent 引擎 | Python LangGraph（open_deep_research） |
| 搜索 | Tavily + RAG（向量数据库） |
| 前端 | 待定（Vue 3 / React） |
| 模型 | DeepSeek V4 Flash（默认，可换） |

## 项目结构

```
deep-research-platform/
├── java-gateway/       Spring Boot 网关
├── agent/              Python Agent（submodule）
├── frontend/           前端 UI
├── docs/               设计文档
├── scripts/            启动/导出脚本
└── docker-compose.yml  一键部署
```

## 开发状态

- 架构设计
- Java 网关
- 前端 UI
- RAG 集成

## License

MIT
