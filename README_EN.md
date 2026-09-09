# Deep Research Platform

> Full-stack AI Agent for deep research | Zero-framework, hand-written | Python + Java + Vue + PostgreSQL/Redis + Docker | Built-in evaluation system

Ask a question → Agent autonomously searches the web + knowledge base → SSE real-time progress → cited research report. Self-built RAGAS metrics + A/B comparison + LLM-as-Judge + ablation + regression testing.

---

## Quick Start

### 1. Setup

```bash
cp agent/.env.example agent/.env
# Edit .env with your API keys:
#   DEEPSEEK_API_KEY=sk-xxx        (LLM)
#   TAVILY_API_KEY=tvly-xxx        (search)
#   DASHSCOPE_API_KEY=sk-xxx       (Aliyun embedding)
```

> This only affects whether research actually works (no keys → LLM/search calls fail).
> Container startup no longer depends on it: `docker compose up` succeeds without `.env`.

### 2. Launch

```bash
docker compose up
# Brings up 5 services: nginx (frontend) · Java gateway · Python agent · PostgreSQL · Redis
# Open http://localhost:3000
# agent/.env is injected automatically when present (env_file, required: false)
```

First launch downloads images and dependencies (~10 min). Subsequent launches take seconds.

**Without Docker**, the three `start.bat` scripts are directly runnable (they `cd` to their own directory, so they work on any machine/path):

| Script | Starts | Port |
|------|--------|------|
| `agent/start.bat` | Python Agent | 8000 |
| `java-gateway/start.bat` | Java gateway (compiles first) | 8080 |
| `frontend/start.bat` | Vue frontend (auto `npm install` if needed) | 3000 |

### 3. CLI (without Docker)

```bash
cd agent
pip install -e .          # all deps declared in pyproject.toml (incl. python-multipart / pymupdf / python-docx)
python -m src.researcher.agent "Impact of quantum computing on cryptography" 2
```

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
               Four-level Agent · RAG KB · Search tools · Trace
                     │
       ┌─────────────┴─────────────┐
       ▼                           ▼
PostgreSQL (source of truth)   Redis (acceleration + concurrency)
sessions: history/report       search cache · cross-researcher URL dedup
JSONB · token_usage            research lock · context hot layer · rate limit
       │
       ▼
DeepSeek · Tavily/DuckDuckGo · Chroma
```

---

## Four Agent Levels

| Level | Architecture | LLM Calls | Best For |
|:---:|------|:---:|------|
| 1 | Search → Report (1 LLM call) | 1 | Simple fact queries, 15-30s |
| 2 | ReAct loop (while + Function Calling) | 3-10 | General research |
| 3 | LLM decomposes → asyncio.gather N×L2 → merge | many | Multi-angle analysis |
| 4 | Supervisor dual-loop → batched L2 dispatch → ResearchComplete → merge | many | Complex deep research |

---

## Key Features

### Agent Runtime

- **Hand-written from protocol level**: while loop + OpenAI native tool_calls messaging, zero LangChain/LangGraph
- **Callback decoupling**: `self.emit = on_progress or (lambda e: None)` — same Agent class for SSE streaming, sync API, and CLI
- **Fully async**: AsyncOpenAI + asyncio.gather parallel + Queue + create_task background tasks
- **Three-layer fault tolerance**: LLM retry (429/5xx exponential backoff, 4xx/timeout no retry) → Tavily→DDG fallback → Agent per-round try/except
- **Context management**: Three-level protection (80% warning → LLM structured compression → hard truncation). Compression cut points must land on `tool_calls` pairing boundaries (`_safe_window_start`, O(n) difference-array scan), and dangling/partial `tool_calls` at the tail are stripped before every LLM call (`_drop_dangling_tail`) — violating the pairing protocol makes OpenAI-compatible backends return 400; compression summaries use the `user` role (mid-conversation `system` is rejected by some backends); the original question anchor lives in its own DB field and is never lost
- **Trace**: JSONL call-chain recording — token usage, timing, model, success/fail per LLM call; query count, dedup, timing per search

### RAG Knowledge Base

Five retrieval modes, independent degradation per layer:

| Mode | Pipeline | Notes |
|------|------|------|
| v2 | Aliyun text-embedding-v4 pure vector | Baseline, 1024-dim, 0.22s |
| hybrid | Vector + BM25 (jieba + RRF fusion) | Keyword complements semantics |
| rerank | Vector coarse recall + CrossEncoder (bge-reranker-base) rerank | Ranking precision boost |
| full | Query rewrite (LLM) → dual-path hybrid → CrossEncoder | Best quality, slowest |
| default | MiniLM local 384-dim | Legacy compat, zero cost |

Dual embedding pipelines (MiniLM + Aliyun), multi-tenant per-user Collection isolation.

### Search Pipeline

Tavily primary → DuckDuckGo fallback · Cross-round + cross-researcher URL dedup (Redis Set) · 5-min search cache (Redis String + `EX`; cache key includes `max_results`/`include_raw` dimensions) · Batch LLM summarization (N→1 call) · structured_output enforced JSON

### Redis: acceleration layer + concurrency control (5 use sites)

Every use site started from a concrete defect:

| Use site | Redis primitive | Defect it fixes |
|------|-----------|-----------|
| Search cache | String + `SET EX 300` | In-process dict: lost on restart, not shared across workers, hand-rolled TTL sweep |
| Cross-researcher URL dedup | Set + `SADD` (pipelined) | L3/L4 researchers each own a `SearchTool` instance → same URL fetched and summarized repeatedly |
| Session research lock | `SET NX EX` + Lua release + background renewal | Concurrent research on one session (multi-tab / retry / multi-instance) → corrupted reports + doubled token cost |
| Context hot/cold tiering | List + `RPUSH`, `DEL` on compression | Every turn read a full PG row (including all report text in JSONB) just to build context |
| Usage stats / rate limit | `INCR` + `ZINCRBY` + pipelined `EXPIRE` | Per-user quota and abuse protection; 429 + `Retry-After` on limit |

**Design notes**:

- **Failure policy per use site**: cache-class failures degrade and let traffic through (slower, never wrong); lock/limit-class failures let traffic through **and alert** (availability first — a Redis outage must not take research down)
- **Consistency model = "mirror or absent"**: Redis is either a complete mirror of PostgreSQL or the key does not exist (on compression/hard truncation we `DEL` and rebuild from PG) — never a half-synced intermediate state
- **The lock must be renewed**: TTL is 1 hour but deep L3/L4 research can run longer — once it expires, a second request slips in, which is exactly what the lock prevents. A background task renews every `TTL/3` via a Lua token check (and exits if the lock has changed hands)
- **Report text never goes into Redis** (memory blow-up); fetched from PG on demand
- **Deployment decoupled**: `REDIS_URL` (Python) / `REDIS_HOST` (Java) env vars — local container in dev, managed Redis in prod, zero code change

### Memory & Persistence

PostgreSQL: history JSONB for conversation chain + report JSONB array for all reports (never truncated). Auto LLM compression at 40+ messages. Report array auto-fills truncated history on follow-up queries. Compressed summaries persisted with reports. **Context building reads the Redis hot layer first** (List; on miss it rebuilds from PG and renews the TTL) — PostgreSQL remains the single source of truth.

---

## Evaluation System

### Three-Layer Framework

| Layer | What it tests | Metrics | LLM involved |
|------|--------------|--------|:--:|
| Retriever | Can we find the right docs? | Precision@5 / Recall@5 / MRR | No |
| Generator | Can the LLM generate well? (skip retrieval) | Faithfulness / Answer Relevance / Context Relevance / Answer Correctness | Yes |
| E2E Diagnostic | Real-world end-to-end | LLM-as-Judge (5-dim, 10-pt) + per-type breakdown | Yes |

### Data-Driven Insights

**Ablation experiment** (112 questions × 4 modes) exposed a hidden ChromaDB cosine distance bug: distances (range 0-2) were treated as similarities (range 0-1), silently filtering ALL correct documents. Recall jumped from 17% to 88% after fix.

**A/B comparison** discovered three cascading bugs: max_tokens default truncation → Judge 12000-char blind spot → prompt verbosity explosion. Attribution was overturned three times by data before the truth was found.

### Evaluation Tools

```bash
# Ablation: compare 4 retrieval modes
python -m src.researcher.evaluation.ablation

# LLM-as-Judge report scoring
python -m src.researcher.evaluation.run_eval --mode report

# A/B comparison (pre/post prompt change)
python -m src.researcher.evaluation.ab_compare gen
python -m src.researcher.evaluation.ab_compare judge

# Regression: fast validation after code changes
python -m src.researcher.evaluation.run_regression --mode retriever  # 25s
python -m src.researcher.evaluation.run_regression --mode format     # 2min
```

---

## Engineering Quality

| Item | Detail |
|---|---|
| Code review | A dedicated "find the bugs" cross-review of Python / Java / Vue produced **43 defects, all fixed**: Redis client state machine, URL-dedup semantics, hot/cold-layer consistency, authorization checks, blocking calls on the Netty event loop, distributed-lock renewal, `tool_calls` protocol invariants, error-code semantics, cross-account frontend cache leak |
| Unit / integration tests | `test_units.py` (28 cases: compression pairing, dangling `tool_calls` cleanup, KB-injection invariant) + `test_redis_cache.py` (19 cases: cache TTL/degradation, cross-instance dedup, lock exclusion/renewal, rate limit/error codes) + `test_quality.py` |
| Consistency verification | "PG first, Redis second" + Lua atomic append + `DEL`-and-rebuild on compression; full-stack degradation when Redis is down |
| Deployment | `docker compose up` brings up frontend / gateway / agent / PostgreSQL / Redis |

---

## Codebase

| Module | Language | Lines |
|--------|------|:---:|
| Python Agent + RAG | Python | ~3,400 |
| Evaluation (4 metrics + Judge + A/B + ablation + regression) | Python | ~3,100 |
| Tests (unit + Redis integration + quality) | Python | ~930 |
| Java Gateway | Java 21 | ~1,400 |
| Vue Frontend | Vue 3 | ~1,100 |
| **Total** | | **~9,900** |

### Core Files

```
agent/src/researcher/
├── agent.py     Four-level Agent + 19 Prompts (~1,450 lines)
├── kb.py        Chroma + 5 retrieval modes + embedding (~455 lines)
├── server.py    FastAPI + SSE + session lock / rate limit / usage (~510 lines)
├── search.py    Tavily + DDG + Redis cache + cross-instance dedup (~440 lines)
├── trace.py     JSONL structured tracing (~230 lines)
├── llm.py       AsyncOpenAI + retry (~155 lines)
├── config.py    Environment variables (~35 lines)
├── retrievers/  BM25 + RRF + CrossEncoder + query rewriting (~140 lines)
└── evaluation/  4 metrics + Judge + A/B + ablation + regression (~3,060 lines)

java-gateway/.../
├── ResearchController.java   SSE passthrough + session mgmt + JWT + virtual-thread scheduling
├── SessionService.java       Session CRUD + auto compression + hot/cold tiering + stale cleanup + anchor
├── SecurityConfig.java       WebFlux Security + JWT Filter
└── JwtTokenProvider.java     JWT signing/verification

frontend/src/
├── views/ResearchView.vue    SSE consumption + session switching + local cache
├── utils/api.js              Unified auth + 401 cleanup
└── utils/session-cache.js    Credential/chat-cache cleanup (no leakage across accounts)
```

---

## License

MIT
