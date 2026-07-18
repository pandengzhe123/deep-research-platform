# Deep Research Platform

> Full-stack AI Agent for deep research | Zero-framework, hand-written | Python + Java + Vue + Docker | Built-in evaluation system

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

### 2. Launch

```bash
docker compose up
# Open http://localhost:3000
```

First launch downloads images and dependencies (~10 min). Subsequent launches take seconds.

### 3. CLI (without Docker)

```bash
cd agent
pip install -e .
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
                     ▼
          DeepSeek · Tavily/DuckDuckGo · Chroma · PostgreSQL
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
- **Context management**: Three-level protection (80% warning → LLM structured compression → hard truncation), original question anchor never lost via independent DB field
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

Tavily primary → DuckDuckGo fallback · Cross-round URL dedup · 5-min search cache · Batch LLM summarization (N→1 call)

### Memory & Persistence

PostgreSQL: history JSONB for conversation chain + report JSONB array for all reports (never truncated). Auto LLM compression at 40+ messages. Report array auto-fills truncated history on follow-up queries. Compressed summaries persisted with reports.

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

## Codebase

| Module | Language | Lines |
|--------|------|:---:|
| Python Agent + RAG + Evaluation + Trace | Python | ~3,500 |
| Java Gateway | Java 21 | ~1,200 |
| Vue Frontend | Vue 3 | ~900 |
| **Total** | | **~5,600** |

### Core Files

```
agent/src/researcher/
├── agent.py     Four-level Agent + 19 Prompts (~1,430 lines)
├── kb.py        Chroma + 5 retrieval modes + embedding (~530 lines)
├── server.py    FastAPI + SSE (~380 lines)
├── search.py    Tavily + DDG + batch summary + dedup + cache (~300 lines)
├── llm.py       AsyncOpenAI + retry (~170 lines)
├── trace.py     JSONL structured tracing (~200 lines)
├── config.py    Environment variables (~50 lines)
├── retrievers/  BM25 + RRF + CrossEncoder + query rewriting (~190 lines)
└── evaluation/  4 metrics + Judge + A/B + ablation + regression (~1,500 lines)

java-gateway/.../
├── ResearchController.java   SSE passthrough + session management
├── SessionService.java       Session CRUD + auto compression + context anchor
├── SecurityConfig.java       WebFlux Security + JWT Filter
└── JwtTokenProvider.java     JWT signing/verification
```

---

## License

MIT
