# Deep Research Platform

> Full-stack AI Agent for deep research | Zero-framework, hand-written | Python + Java + Vue + Docker

Ask a question → Agent autonomously searches the web → SSE real-time progress → cited research report.

---

## Quick Start

### 1. Setup

```bash
cp agent/.env.example agent/.env
# Edit .env with your API keys:
#   DEEPSEEK_API_KEY=sk-xxx
#   TAVILY_API_KEY=tvly-xxx
```

### 2. Launch

```bash
docker compose up
# Open http://localhost:3000
```

First launch downloads images and dependencies (~10 min). Subsequent launches take seconds.

### 3. Use

Register → Enter a question → Choose Level → Send. Level 1 for simple queries (15-30s), Level 2-4 for deep research.

---

## Architecture

```
Browser (Vue 3) → nginx (:80)
                     │
                     ▼
               Java Gateway (WebFlux :8080)
              Session mgmt · JWT auth · SSE passthrough · PostgreSQL persistence
                     │
                     ▼
               Python Agent (FastAPI :8000)
               Four-level Agent · RAG KB · Search tools
                     │
                     ▼
          DeepSeek · Tavily/DuckDuckGo · Chroma · PostgreSQL
```

---

## Four Agent Levels

| Level | Architecture | LLM Calls | Best For |
|:---:|------|:---:|------|
| 1 | Search → Report (1 LLM call) | 1 | Simple fact queries, 15-30s |
| 2 | ReAct loop (while + Function Calling) | 3-10 | General research, 1-3 min |
| 3 | LLM decomposes → asyncio.gather N×L2 → merge | many | Multi-angle analysis, 2-5 min |
| 4 | Supervisor dual-loop → batched L2 dispatch → ResearchComplete → merge | many | Complex deep research, 3-10 min |

---

## Key Engineering Practices

- **SSE three-layer streaming**: Python yield → Java `Flux<ServerSentEvent>` → browser `ReadableStream`. Real-time research progress. nginx buffering off, 30-min timeout unified across all layers
- **Queue + create_task pattern**: Agent runs in background (create_task), progress events go through asyncio.Queue via callback, main loop polls queue and yields SSE. Client disconnect → task.cancel stops token consumption
- **Callback decoupling**: `self.emit = on_progress or (lambda e: None)`. Same Agent class works for SSE streaming, sync API, and CLI testing — zero coupling
- **Search optimization**: Tavily primary → DuckDuckGo fallback, cross-round URL dedup, batch LLM summarization (5 calls → 1), 5-minute search cache
- **Memory system**: PostgreSQL JSONB stores full conversation history (including full reports), report JSONB array stores all reports without overwrite, 50-message cap + 500k char truncation + 80%/100% dual-threshold warnings
- **Concurrency control**: Java Semaphore limits 20 concurrent studies, L3/L4 share a single LLMClient to reuse connection pool, asyncio.gather for parallel search
- **Three-layer fault tolerance**: LLM retry (exponential backoff) → Tavily→DDG fallback → Agent loop try/except safety net

---

## Codebase

| Module | Language | Lines |
|--------|------|:---:|
| Python Agent | Python | ~2,000 |
| Java Gateway | Java 21 | ~700 |
| Vue Frontend | Vue 3 | ~700 |
| **Total** | | **~3,400** |

### Core Files

```
agent/src/researcher/
├── agent.py     Four-level Agent + TOOLS + Prompts (~990 lines)
├── server.py    FastAPI + run_agent_with_sse() (~310 lines)
├── search.py    Tavily + DDG + batch summary + dedup + cache (~260 lines)
├── kb.py        Chroma + embedding + chunking (~260 lines)
├── llm.py       OpenAI SDK wrapper + retry (~130 lines)
└── config.py    Environment variables (~40 lines)

java-gateway/.../
├── ResearchController.java   SSE passthrough + session management
├── AgentClient.java          HTTP client to Python
├── SessionService.java       Session CRUD + stale cleanup + context compression
└── JwtTokenProvider.java     JWT signing/verification
```

---

## License

MIT
