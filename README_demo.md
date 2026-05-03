# Engram — Multi-Agent Memory Demo

> Memory middleware for multi-agent AI systems. Detects concurrent writes using vector clocks, resolves conflicts via CRDTs, and enforces role-based access control.

## 🚀 Try it instantly (no setup)

**Demo UI:** https://engram-demo.vercel.app

1. Open the link
2. Click **Connect** — the server is pre-configured
3. Hit **⚡ Trigger Conflict** and watch it go

---

## 🎮 How to use the demo

### The setup
Two AI agents share the same memory store. Each agent can read and write keys independently — without knowing what the other is doing.

### Trigger a conflict
Click **⚡ Trigger Conflict** — this fires both agents at the exact same millisecond via `Promise.all`. Neither agent sees the other's write before sending. Engram detects the concurrent write using vector clocks and resolves it using your chosen strategy.

### Switch conflict strategies
Use the **Conflict Strategy** dropdown in the left sidebar:

| Strategy | What happens |
|---|---|
| `latest_clock` | Picks the value with the highest vector clock sum |
| `highest_value` | Picks the numerically largest value |
| `lowest_value` | Picks the numerically smallest value |
| `union` | Keeps all values as a list — nothing discarded |
| `flag_for_human` | Does not resolve — marks entry as FLAGGED |

### Switch scenarios
Three scenarios in the left sidebar:
- **Budget negotiation** — two finance agents writing budget keys
- **Inventory sync** — warehouse vs shipping agent on stock levels
- **Task planning** — planner vs executor on task priority
- **Free play** — type any key/value yourself

### Read the memory card
Each key in the shared memory store shows:
- **Value** — the resolved value after conflict resolution
- **Vector clock** — causal history e.g. `{ A:1, B:1 }`
- **Conflict detail** — what the two values were and how it resolved

### Read the HTTP trace
The right panel shows every real HTTP request to the Engram server — `POST /write`, `GET /keys`, status codes, and conflict outcomes.

---

## 🏗 Run it locally

### Prerequisites
- Python 3.12
- Node.js (for Vercel CLI, optional)

### Setup
```bash
git clone https://github.com/lash106/Engram_demo
cd Engram_demo

python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

### Add CORS (one-time)
Open `engram/api.py`, find `app = FastAPI(...)` and add right after it:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Start the server
```bash
uvicorn engram.api:app --reload
```

Server runs at `http://localhost:8000`.

### Open the UI
```bash
open -a "Google Chrome" demo/ui/index.html
```

Change the server field to `http://localhost:8000` and click **Connect**.

---

## 🔑 Core concepts

### Vector clocks
Every write carries a vector clock — a dict mapping each agent ID to a counter. By comparing two clocks, Engram determines if one write causally precedes another or if they are **concurrent** (a conflict).

```
Agent A writes budget = 85000  →  clock: { budget-agent-A: 1 }
Agent B writes budget = 72000  →  clock: { budget-agent-B: 1 }

Neither clock dominates → CONCURRENT → conflict detected
```

### MVRegister (CRDT)
When a conflict is detected, Engram does not silently pick a winner. It stores all concurrent values in a Multi-Value Register and defers resolution until a strategy is applied.

### Write pipeline
Every write goes through:
```
Permission check → Clock increment → Conflict detection → CRDT resolution → History append → Storage write
```

### RBAC
Role-based access control enforces which keys each agent can read and write:
```
admin        → can read/write *
budget-agent → can read/write budget.*, headcount
inventory    → can read/write stock.*, orders.*
```

---

## 📡 API reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/write` | Write a value to shared memory |
| `GET` | `/read/{key}` | Read a value |
| `GET` | `/history/{key}` | Full write history for a key |
| `GET` | `/keys` | List all keys |
| `POST` | `/roles` | Register a role definition |
| `GET` | `/health` | Health check |

### Write example
```bash
curl -X POST https://engram-demo.onrender.com/write \
  -H "Content-Type: application/json" \
  -d '{
    "key": "budget.q3",
    "value": "85000",
    "agent_id": "budget-agent-A",
    "role": "admin",
    "consistency_level": "eventual",
    "conflict_strategy": "latest_clock",
    "vector_clock": {}
  }'
```

---

## 🗂 Project structure

```
Engram_demo/
├── engram/
│   ├── api.py              # FastAPI routes
│   ├── middleware.py       # Write pipeline
│   ├── crdt.py             # MVRegister CRDT
│   ├── vector_clock.py     # Vector clock logic
│   ├── access_control.py   # RBAC
│   ├── history.py          # Audit log
│   └── storage/
│       ├── memory.py       # In-memory adapter
│       └── redis_adapter.py
├── demo/
│   └── ui/
│       ├── index.html      # Interactive demo UI
│       └── vercel.json     # Vercel config
├── tests/
├── requirements.txt
└── runtime.txt
```

---

## 🌐 Deployment

| Service | URL |
|---|---|
| Demo UI | https://engram-demo.vercel.app |
| API Server | https://engram-demo.onrender.com |

The UI is hosted on Vercel (static). The server is hosted on Render (Python). Both are free tier.
