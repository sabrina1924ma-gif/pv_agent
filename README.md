# PV Agent

<div align="center">

**AI Agent for photovoltaic power station data query, fault analysis, and report generation — powered by LangGraph + DeepSeek V4**

[![Python](https://img.shields.io/badge/Python-3.13+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136+-009688.svg)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-green.svg)](https://langchain-ai.github.io/langgraph/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## Overview

PV Agent is an intelligent assistant for photovoltaic power station operations. It accepts natural language queries about equipment status, power generation curves, fault detection, and equipment health — then routes them through a multi-step LLM pipeline to generate structured reports with Markdown tables, charts, and actionable recommendations.

Key capabilities:

- **Natural language querying** — Ask questions like "check fault logs for station 003 last quarter" or "compare total generation across all five stations for 2025"
- **7 intent classifications** — device status, power curve, fault detection, life assessment, report generation, multi-station summary, and general chat
- **Streaming responses** — WebSocket-based real-time token output with node-by-node progress visibility
- **Anti-hallucination pipeline** — Four-layer validation including prompt constraints, mandatory data verification section, and post-generation year-range checking
- **Multi-output reports** — Markdown (web / Feishu cards), HTML, and PDF with CJK font support
- **Multi-session persistence** — Redis hot cache (chat history + tool cache) + PostgreSQL cold storage (full audit log + LangGraph checkpointing)

## Architecture

```
User Query (HTTP / WebSocket / Feishu Callback)
        │
        ▼
┌──────────────────────────────────────────┐
│              FastAPI Layer               │
│  Middleware: RateLimit → RequestID →     │
│  Session → SecurityHeaders → CORS        │
│  Routes: REST + WebSocket + Feishu       │
└──────────────────┬───────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────┐
│          LangGraph Agent Pipeline        │
│                                          │
│  load_history → intent_router →          │
│    ├─ needs_tools → tool_executor →      │
│    └─ no_tools ─────────────────┐        │
│                                 ▼        │
│                          report_node     │
└──────────────────┬───────────────────────┘
                   │
        ┌──────────┼──────────┐
        ▼          ▼          ▼
   ┌────────┐ ┌────────┐ ┌────────┐
   │ Redis  │ │PostgreSQL│ │DeepSeek│
   │ (Hot)  │ │ (Cold)  │ │  V4    │
   └────────┘ └────────┘ └────────┘
```

The agent graph is a compiled LangGraph state machine with conditional edges. Each node emits streaming events via a contextvar-based callback system, enabling real-time progress visibility without coupling the graph to the transport layer.

### Data Flow

1. **load_history** — Fetches recent conversation from Redis and prepends it to the message list for multi-turn context
2. **intent_router** — Runs deterministic regex entity extraction first (year, month, station ID, metric type), then calls LLM at temperature=0 for intent classification. Routes to either tool_executor or directly to report_node via conditional edge
3. **tool_executor** — Dispatches tools concurrently with `asyncio.gather`, each with independent timeout and exception isolation. Failed tools don't block others
4. **report_node** — Aggregates all context, compresses tool results to reduce prompt tokens, streams LLM output through a TokenBuffer (first chunk immediately, then every ~20 chars or newline), runs post-generation year-range validation

### Anti-Hallucination Design

Four layers of defense against fabricated data:

1. **System prompt constraints** — Explicit instruction: only output data present in tool results
2. **Mandatory verification section** — Every report must end with a "## 数据校验" (Data Verification) block listing sources and confirming key numbers
3. **Post-generation regex validation** — `_validate_report()` scans the generated Markdown for year mentions, cross-references against actual tool data, and appends warnings if discrepancies are found
4. **Tool result compression** — `_compress_tool_results()` strips raw arrays and keeps only summary fields, reducing the LLM's temptation to invent details

## Quick Start

### Prerequisites

- Python 3.13+
- Redis 7+ (for session caching and tool result cache)
- PostgreSQL 16+ (for persistent storage and LangGraph checkpointing)
- DeepSeek API key ([get one here](https://platform.deepseek.com))

### 1. Clone and Configure

```bash
git clone <your-repo-url>
cd pv_agent

# Copy and edit environment variables
cp .env.example .env
```

Edit `.env` and set your DeepSeek API key:

```env
DEEPSEEK_API_KEY=sk-your-actual-key-here
```

The default `.env` is pre-configured for local development with PostgreSQL user `sabrina` and database `pv_agent`. Adjust `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` to match your setup.

### 2. Install Dependencies

```bash
pip install -e .
```

Or with dev tools:

```bash
pip install -e ".[dev]"
```

### 3. Start Infrastructure (Docker)

```bash
docker compose up -d redis postgres
```

This starts Redis on port 6379 and PostgreSQL on port 5432 with persistent volumes.

### 4. Run the Application

```bash
# Development mode with hot reload
uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000

# Production mode (4 workers)
uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### 5. Full Docker Deployment

```bash
docker compose up -d
```

Starts all three services (app + redis + postgres) with health checks and automatic restarts.

## API Reference

### REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/chat` | Non-streaming chat — returns full response with intent, report, and tool results |
| `GET` | `/api/v1/sessions/{id}` | Get session metadata (message count) |
| `DELETE` | `/api/v1/sessions/{id}` | Clear session context from Redis and PostgreSQL |
| `POST` | `/api/v1/report/pdf` | Convert Markdown report to downloadable PDF |
| `GET` | `/health` | Liveness probe for container orchestration |

### WebSocket

```
ws://localhost:8000/ws/{session_id}
```

Real-time bidirectional streaming with event types: `thinking`, `node_start`, `node_end`, `tool_start`, `tool_end`, `token`, `done`, `error`.

### Example Request

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: $(uuidgen)" \
  -d '{"message": "查一下3号电站上个月的发电量曲线", "station_id": "station-003"}'
```

Response:

```json
{
  "session_id": "a1b2c3d4-...",
  "message": "已生成 3 号电站 2025年4月 发电量报告",
  "intent": "power_curve",
  "report_md": "# 3号电站 2025年4月 发电量报告\n\n## 摘要\n...",
  "tool_results": [...],
  "error": null
}
```

## Project Structure

```
pv_agent/
├── app/
│   ├── __init__.py              # Package metadata
│   ├── config/
│   │   └── settings.py          # Pydantic-settings, .env driven
│   ├── api/
│   │   ├── main.py              # FastAPI app factory + lifespan
│   │   ├── middleware.py        # RateLimit, RequestID, Session, SecurityHeaders
│   │   ├── router.py            # REST endpoints (/chat, /sessions, /report/pdf)
│   │   └── websocket.py         # WebSocket streaming endpoint + heartbeat
│   ├── agent/
│   │   ├── schema.py            # AgentState TypedDict definition
│   │   ├── graph.py             # LangGraph StateGraph builder + PostgresCheckpointer
│   │   ├── streaming.py         # Async generator wrapping graph execution
│   │   ├── events.py            # StreamEvent dataclass + contextvar callback
│   │   └── nodes/
│   │       ├── load_history.py  # Redis history loader
│   │       ├── intent_router.py # Regex entity extraction + LLM intent classification
│   │       ├── tool_executor.py # Concurrent tool dispatch with timeout isolation
│   │       └── report_node.py   # Prompt builder, TokenBuffer, anti-hallucination validation
│   ├── llm/
│   │   └── provider.py          # DeepSeek via langchain-openai, retry wrappers
│   ├── tools/
│   │   ├── decorator.py         # @tool registration decorator
│   │   └── impl/
│   │       ├── device_status.py
│   │       ├── power_curve.py
│   │       ├── fault_detection.py
│   │       ├── life_assessment.py
│   │       ├── report_generator.py
│   │       └── multi_station_summary.py
│   ├── storage/
│   │   ├── redis_client.py      # Async Redis: chat history, tool cache, report cache
│   │   ├── db.py                # SQLAlchemy async engine + session management
│   │   ├── models.py            # ORM: ChatHistory, Checkpoint, CheckpointWrite
│   │   └── repositories.py      # Data access layer
│   ├── data/
│   │   └── mock.py              # Mock data for 5 stations with realistic PV profiles
│   └── utils/
│       └── pdf.py               # Markdown → PDF (fpdf2, CJK fonts)
├── frontend/                    # Vue 3 SPA (served as static files)
├── docker-compose.yml           # App + Redis + PostgreSQL
├── Dockerfile                   # Production image
├── pyproject.toml               # Project metadata + dependencies + tool config
└── .env                         # Environment variables (git-ignored)
```

## Configuration Reference

All settings are managed through `.env` with Pydantic-settings validation. Key groups:

| Category | Setting | Default | Description |
|----------|---------|---------|-------------|
| **Server** | `PORT` | `8000` | HTTP/WS listen port |
| | `WORKERS` | `4` | Uvicorn worker count |
| **DeepSeek** | `DEEPSEEK_API_KEY` | — | API key (required) |
| | `DEEPSEEK_MODEL` | `deepseek-v4-flash` | Model for generation |
| | `LLM_TEMPERATURE` | `0.3` | Generation creativity |
| | `LLM_MAX_TOKENS` | `4096` | Max output tokens |
| **Redis** | `REDIS_HOST` | `localhost` | Redis server |
| | `REDIS_SESSION_TTL` | `86400` | Chat history TTL (24h) |
| | `REDIS_TOOL_CACHE_TTL` | `300` | Tool result cache TTL (5m) |
| **PostgreSQL** | `POSTGRES_HOST` | `localhost` | Database server |
| | `DB_POOL_SIZE` | `20` | Connection pool size |
| **LangGraph** | `LANGGRAPH_MAX_RECURSION` | `25` | Max graph iterations |
| | `LANGGRAPH_CHECKPOINT_PERSIST` | `true` | Enable PostgreSQL checkpoints |

See `.env` for the full list with inline documentation.

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Web Framework** | FastAPI 0.136+ | REST + WebSocket server |
| **Agent Framework** | LangGraph 0.2+ | State machine orchestration |
| **LLM** | DeepSeek V4 (via langchain-openai) | Intent classification + report generation |
| **Hot Cache** | Redis 7 (aioredis) | Session history, tool cache, report cache |
| **Cold Storage** | PostgreSQL 16 (asyncpg + SQLAlchemy) | Audit logs, LangGraph checkpoints |
| **Frontend** | Vue 3 + Vite | SPA served as static files |
| **PDF** | fpdf2 + markdown | Server-side report rendering with CJK fonts |
| **Config** | Pydantic-settings | Typed .env configuration |
| **Logging** | Loguru | Structured logging with request-id context |
| **Retry** | Tenacity | LLM call retry with exponential backoff |
| **Containerization** | Docker + docker-compose | Multi-service orchestration |

## Design Decisions

### Why LangGraph instead of raw LLM chaining?

The agent pipeline has conditional branching (tools needed vs. not needed), state accumulation across steps, and streaming requirements. LangGraph provides a formal state machine with compile-time validation, checkpoint persistence for fault recovery, and native async streaming support — all of which would require significant custom code with a manual chain.

### Why Redis + PostgreSQL dual storage?

Redis serves as a hot cache with TTL-based eviction: chat history for multi-turn context, tool results for deduplication, generated reports for fast re-fetch. PostgreSQL is the system of record: full audit trail, LangGraph checkpointing for graph recovery, and analytical queries. The dual-write pattern in the repository layer ensures consistency without blocking the hot path on cold storage latency.

### Why compressed tool results before LLM generation?

Raw tool results can contain thousands of data points (12 months × 30 days of hourly generation data). Passing all of this to the LLM wastes tokens, increases TTFT (Time To First Token), and actually increases hallucination risk by giving the model more numbers to potentially confuse. The compression function keeps only summary statistics, trend descriptions, and key metrics — reducing prompt size by ~70% while preserving all information needed for a useful report.

### Why contextvar-based streaming callbacks?

The graph runs in a background asyncio task while the main coroutine consumes an event queue. Using contextvars for the callback avoids threading issues (coroutine-safe by design), keeps the streaming system decoupled from the graph nodes (no signature changes), and works correctly with asyncio's cooperative multitasking.

### Why regex entity extraction before LLM classification?

Entity extraction (station ID, year, month, metric type) is a deterministic pattern-matching problem. Running regex first gives us fast, reliable results without consuming LLM tokens or adding latency. The LLM focuses on what it does best — intent classification and language understanding — while regex handles the structured data extraction that doesn't require intelligence.

## License

MIT
