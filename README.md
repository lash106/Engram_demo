# Engram

Memory middleware for multi-agent AI systems.

Engram sits between your AI agents and shared state, providing conflict detection, causal ordering, access control, and a full audit trail — so multiple agents can read and write to the same memory without silently overwriting each other.

## The Problem

When multiple AI agents share state via a plain dict or key-value store, the last write wins. Agent A sets `budget = 5000`, Agent B sets `budget = 3000` a millisecond later without seeing A's write, and A's value is gone. No error, no trace, no way to recover.

Engram detects these concurrent writes using vector clocks, stores all conflicting values in a CRDT (Multi-Value Register), and resolves them using a configurable strategy — or flags them for human review.

## Demo
[This is the github link to the demo](https://github.com/lash106/Engram_demo/blob/main/README_demo.md)

## Core Concepts

### Vector Clocks

Every write carries a vector clock — a dict mapping each agent ID to a monotonically increasing counter. By comparing two clocks, Engram determines if one write causally precedes another or if they are concurrent (a conflict).

```
Agent A writes budget = 5000  →  clock: {"A": 1}
Agent B writes budget = 3000  →  clock: {"B": 1}

Neither clock dominates → CONCURRENT → conflict detected
```

### Multi-Value Register (CRDT)

When a conflict is detected, Engram does not silently pick a winner. It stores all concurrent values in an MVRegister and defers resolution until a value is needed. This is the same approach used in systems like Amazon Dynamo and Riak.

### Conflict Resolution Strategies

| Strategy | Behavior |
|---|---|
| `latest_clock` | Pick the value with the highest vector clock sum. Tie-break by timestamp. |
| `lowest_value` | Pick the numerically smallest value. |
| `highest_value` | Pick the numerically largest value. |
| `union` | Keep all values as a list. Nothing is discarded. |
| `flag_for_human` | Don't resolve. Mark the entry as `FLAGGED` for manual review. |

### Access Control

Role-based permissions with fnmatch-style wildcard patterns:

```python
# Full access
policy.register_role("admin", can_read=["*"], can_write=["*"])

# Read-only
policy.register_role("summarizer", can_read=["*"], can_write=[])

# Scoped to budget keys
policy.register_role("budget-agent", can_read=["budget.*"], can_write=["budget.*"])
```

Every read and write is checked before touching storage. Unauthorized writes return HTTP 403.

### History & Time-Travel

Every write is appended to an immutable history log. Rollbacks don't delete entries — they create new entries of type `ROLLBACK` that point back to the original. You can query the value of any key at any point in causal time:

```
GET /snapshot/budget?at_clock={"A": 1}
→ returns the value of "budget" as it was when Agent A's clock was at 1
```

## Architecture

```
HTTP Request
    │
    ▼
┌─────────────────────────────────────────────┐
│              EngramMiddleware                │
│                                             │
│  ┌──────────────┐    ┌──────────────────┐   │
│  │ AccessPolicy │    │   VectorClock    │   │
│  │   (RBAC)     │    │ (causal order)   │   │
│  └──────────────┘    └──────────────────┘   │
│                                             │
│  ┌──────────────┐    ┌──────────────────┐   │
│  │  MVRegister  │    │   HistoryLog     │   │
│  │   (CRDT)     │    │  (audit trail)   │   │
│  └──────────────┘    └──────────────────┘   │
│                                             │
│  ┌──────────────────────────────────────┐   │
│  │         StorageAdapter (ABC)         │   │
│  │  InMemoryAdapter │ RedisAdapter │ …  │   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

The middleware orchestrates the full pipeline for every operation:

1. **Permission check** — AccessPolicy verifies the agent's role can read/write the key
2. **Clock increment** — VectorClock is incremented for the writing agent
3. **Conflict detection** — incoming clock is compared against the existing entry's clock
4. **Resolution** — if concurrent, MVRegister stores both values and resolves with the chosen strategy
5. **History append** — an immutable HistoryEntry is logged
6. **Storage write** — the resolved MemoryEntry is persisted via the storage adapter

## API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/write` | Write a value to shared memory |
| `GET` | `/read/{key}` | Read a value (with permission and consistency checks) |
| `GET` | `/history/{key}` | Get the full write history for a key |
| `GET` | `/snapshot/{key}` | Time-travel: value at a specific vector clock |
| `POST` | `/rollback/{write_id}` | Restore a previous value |
| `POST` | `/roles` | Register or update a role definition |
| `GET` | `/roles/{role_name}` | Get a role's permissions |
| `GET` | `/keys` | List all keys (optional prefix filter) |
| `GET` | `/health` | Health check with storage status |
| `WS` | `/ws/memory` | Real-time memory updates (not yet implemented) |

### Write Example

```bash
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{
    "key": "budget",
    "value": 5000,
    "agent_id": "budget-agent-1",
    "role": "budget-agent",
    "consistency_level": "eventual",
    "conflict_strategy": "latest_clock",
    "vector_clock": {"budget-agent-1": 0}
  }'
```

### Read Example

```bash
curl "http://localhost:8000/read/budget?agent_id=summarizer-1&role=summarizer"
```

## Setup

### Prerequisites

- Python 3.11+
- Docker (for Redis, optional)

### Install

```bash
git clone <repo-url> && cd Engram
brew install python@3.12
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Run with In-Memory Storage (zero dependencies)

```bash
uvicorn engram.api:app --reload
```

The default `STORAGE_ADAPTER=memory` uses a Python dict — no Redis needed. Suitable for development and testing.

### Run with Redis

```bash
# Start Redis
docker compose up -d

# Update .env
STORAGE_ADAPTER=redis
REDIS_URL=redis://localhost:6379/0

# Start the server
uvicorn engram.api:app --reload
```

Redis uses AOF persistence (`--appendonly yes`), so data survives container restarts.

### Run Tests

```bash
pytest
```

Tests use the in-memory adapter by default. No external services required.

## Configuration

All configuration is via environment variables (or `.env` file):

| Variable | Default | Description |
|---|---|---|
| `STORAGE_ADAPTER` | `memory` | Storage backend: `memory` or `redis` |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `ENGRAM_HOST` | `0.0.0.0` | API bind host |
| `ENGRAM_PORT` | `8000` | API bind port |
| `LOG_LEVEL` | `INFO` | Logging level |

## Storage Adapters

Engram uses a pluggable storage layer. All adapters implement `StorageAdapter`:

| Adapter | Status | Use Case |
|---|---|---|
| `InMemoryAdapter` | Stubbed | Development, testing, demos |
| `RedisAdapter` | Stubbed | Production, persistent state |
| `ChromaAdapter` | Stubbed | Semantic search via ChromaDB |
| `PineconeAdapter` | Stubbed | Production-scale vector search |

To add a new backend, implement the `StorageAdapter` ABC in `engram/storage/base.py`:

```python
class StorageAdapter(ABC):
    def write(self, entry: MemoryEntry) -> None: ...
    def read(self, key: str) -> Optional[MemoryEntry]: ...
    def write_history(self, entry: HistoryEntry) -> None: ...
    def read_history(self, key, agent_id, since, until) -> list[HistoryEntry]: ...
    def delete(self, key: str) -> None: ...
    def list_keys(self, prefix: Optional[str] = None) -> list[str]: ...
    def ping(self) -> bool: ...
```

## Project Structure

```
engram/
├── engram/
│   ├── models.py            # All enums, Pydantic models, request/response types
│   ├── vector_clock.py      # Immutable VectorClock with increment, merge, compare
│   ├── crdt.py              # MVRegister CRDT for conflict detection and resolution
│   ├── access_control.py    # Role-based access control with fnmatch patterns
│   ├── history.py           # Append-only history log with time-travel queries
│   ├── middleware.py         # Central pipeline: wires all components together
│   ├── api.py               # FastAPI routes and lifespan setup
│   └── storage/
│       ├── base.py           # StorageAdapter abstract base class
│       ├── memory.py         # In-memory adapter (dict-backed, thread-safe)
│       ├── redis_adapter.py  # Redis adapter (JSON serialization)
│       ├── chroma_adapter.py # ChromaDB adapter (stub)
│       └── pinecone_adapter.py # Pinecone adapter (stub)
├── tests/                    # pytest suite with fixtures in conftest.py
├── demo/
│   ├── agents.py             # HTTP client classes for demo agents
│   ├── run_demo.py           # Five-moment demo script
│   └── ui/                   # Browser dashboard (static HTML/JS)
├── docker-compose.yml        # Redis 7.2 Alpine
├── requirements.txt          # Pinned dependencies
└── pyproject.toml            # Project metadata, pytest & ruff config
```

## Consistency Levels

| Level | Behavior | Status |
|---|---|---|
| `eventual` | Return immediately from local storage. No coordination. | Stubbed |
| `causal` | Verify the returned value does not violate causal ordering relative to the requesting agent. | Stub |
| `strong` | Coordinate to confirm no pending writes exist before returning. | Stub |

## Tech Stack

- **FastAPI** — async web framework
- **Pydantic v2** — data validation and serialization
- **pydantic-settings** — configuration from environment variables
- **Redis** — optional persistent storage backend
- **Docker Compose** — one-command Redis setup
- **pytest** — test framework with async support
- **httpx** — HTTP client for TestClient and demo agents

## License

MIT
