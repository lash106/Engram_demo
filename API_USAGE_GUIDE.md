# Engram API Usage Guide

A comprehensive guide to using the Engram API with real-world examples and curl commands.

**Table of Contents**

1. [Getting Started](#getting-started)
2. [Authentication & Role Management](#authentication--role-management)
3. [Writing to Memory](#writing-to-memory)
4. [Reading from Memory](#reading-from-memory)
5. [Conflict Detection & Resolution](#conflict-detection--resolution)
6. [History & Auditing](#history--auditing)
7. [Time-Travel Queries](#time-travel-queries)
8. [Rollbacks](#rollbacks)
9. [Common Workflows](#common-workflows)
10. [Error Handling](#error-handling)
11. [Performance Considerations](#performance-considerations)

---

## Getting Started

### Start the Engram Service

```bash
# Option 1: Using Docker Compose
docker-compose up -d

# Option 2: Running locally with Python
python -m uvicorn engram.api:app --host 0.0.0.0 --port 8000
```

### Verify the API is Running

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{
  "status": "healthy",
  "storage": "memory",
  "version": "0.1.0"
}
```

---

## Authentication & Role Management

Engram uses **role-based access control** at the API level. Before you can read or write, you must:

1. **Register a role** with specific permissions
2. **Use that role** in all subsequent operations

### Understanding Roles

A role defines **what keys an agent can read and write** using fnmatch patterns:

- `*` - matches ALL keys (wildcard)
- `budget.*` - matches keys starting with "budget." (e.g., `budget.flights`, `budget.hotels`)
- `transaction_*` - matches keys starting with "transaction\_"
- `audit.log` - matches exactly "audit.log" (literal match)
- `*.log` - matches any key ending with ".log"

### Register an Admin Role

Admin has full read/write access:

```bash
curl -X POST http://localhost:8000/roles \
  -H "Content-Type: application/json" \
  -d '{
    "role_name": "admin",
    "can_read": ["*"],
    "can_write": ["*"]
  }'
```

### Register a Scoped Role

Budget agent can only access budget-related keys:

```bash
curl -X POST http://localhost:8000/roles \
  -H "Content-Type: application/json" \
  -d '{
    "role_name": "budget-agent",
    "can_read": ["budget.*"],
    "can_write": ["budget.*"]
  }'
```

### Register a Read-Only Role

Auditor can read everything but not write:

```bash
curl -X POST http://localhost:8000/roles \
  -H "Content-Type: application/json" \
  -d '{
    "role_name": "auditor",
    "can_read": ["*"],
    "can_write": []
  }'
```

### Get Role Definition

```bash
curl http://localhost:8000/roles/admin
```

Response:

```json
{
  "role_name": "admin",
  "can_read": ["*"],
  "can_write": ["*"]
}
```

---

## Writing to Memory

Writing to Engram happens through the **POST /write** endpoint with a full write pipeline:

1. **Permission check** - Verify role can write to key
2. **Vector clock increment** - Track causal ordering
3. **Conflict detection** - Compare with existing value's clock
4. **Conflict resolution** - Apply configured strategy
5. **History logging** - Append to immutable audit trail
6. **Persistence** - Save to storage

### Vector Clocks Explained

A vector clock is a dictionary mapping agent IDs to monotonically increasing counters:

```json
{ "agent_a": 1, "agent_b": 0, "agent_c": 2 }
```

- Increment YOUR counter when YOU write
- Keep OTHER agents' counters from what you last saw
- Engram merges clocks on conflict to preserve causal history

### Simple Write (No Previous Value)

```bash
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{
    "key": "budget",
    "value": 5000,
    "agent_id": "budget-agent-1",
    "role": "budget-agent",
    "vector_clock": { "budget-agent-1": 1 }
  }'
```

Response (status = "ok" because no conflict):

```json
{
  "write_id": "550e8400-e29b-41d4-a716-446655440000",
  "key": "budget",
  "value": 5000,
  "agent_id": "budget-agent-1",
  "role": "budget-agent",
  "vector_clock": { "budget-agent-1": 1 },
  "consistency_level": "eventual",
  "conflict_strategy": "latest_clock",
  "status": "ok",
  "conflicting_writes": [],
  "timestamp": "2026-05-05T10:30:00Z"
}
```

### Write with Explicit Consistency & Strategy

```bash
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{
    "key": "budget",
    "value": 4800,
    "agent_id": "budget-agent-2",
    "role": "budget-agent",
    "consistency_level": "eventual",
    "conflict_strategy": "highest_value",
    "vector_clock": { "budget-agent-2": 1 }
  }'
```

### Typical Write Sequence (Two Agents)

**Agent A writes first:**

```bash
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{
    "key": "user_budget",
    "value": 1000,
    "agent_id": "agent_a",
    "role": "admin",
    "vector_clock": { "agent_a": 1, "agent_b": 0 }
  }'
```

**Agent B writes concurrently:**

```bash
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{
    "key": "user_budget",
    "value": 1200,
    "agent_id": "agent_b",
    "role": "admin",
    "vector_clock": { "agent_a": 0, "agent_b": 1 }
  }'
```

**Result**: Conflict detected! Response has `status: "conflicted"` and `conflicting_writes` list.

---

## Reading from Memory

Reading with the **GET /read/{key}** endpoint includes:

1. **Permission check** - Verify role can read key
2. **Storage lookup** - Retrieve current value
3. **Consistency enforcement** - Apply consistency level (eventually = immediate)

### Basic Read

```bash
curl "http://localhost:8000/read/budget?agent_id=agent_a&role=admin"
```

Response:

```json
{
  "write_id": "550e8400-e29b-41d4-a716-446655440000",
  "key": "budget",
  "value": 5000,
  "agent_id": "agent_a",
  "role": "admin",
  "vector_clock": { "agent_a": 1 },
  "consistency_level": "eventual",
  "conflict_strategy": "latest_clock",
  "status": "ok",
  "conflicting_writes": [],
  "timestamp": "2026-05-05T10:30:00Z"
}
```

### Read with Consistency Level

```bash
curl "http://localhost:8000/read/budget?agent_id=agent_a&role=admin&consistency_level=eventual"
```

### Permission Denied Example

Try to read with insufficient permissions:

```bash
curl "http://localhost:8000/read/budget?agent_id=agent_x&role=auditor"
```

If `auditor` role's `can_read` doesn't include "budget", response:

```json
{
  "detail": "Permission denied: role 'auditor' cannot read 'budget'"
}
```

Status: **403 Forbidden**

### Key Not Found

```bash
curl "http://localhost:8000/read/nonexistent?agent_id=agent_a&role=admin"
```

Response:

```json
{
  "detail": "Key 'nonexistent' not found"
}
```

Status: **404 Not Found**

---

## Conflict Detection & Resolution

### Understanding Conflicts

Conflicts occur when two agents write **concurrently** (their vector clocks are incomparable).

**Example**:

- Agent A's clock: `{"A": 1, "B": 0}`
- Agent B's clock: `{"A": 0, "B": 1}`
- These are **CONCURRENT** (neither happened after the other)

### Conflict Strategies

| Strategy         | Behavior                          | Use Case                    |
| ---------------- | --------------------------------- | --------------------------- |
| `latest_clock`   | Pick value with highest clock sum | Default; most recent write  |
| `highest_value`  | Pick numerically highest value    | Monetary amounts (take max) |
| `lowest_value`   | Pick numerically lowest value     | Constraints (take minimum)  |
| `union`          | Keep all values as a list         | Multiple valid options      |
| `flag_for_human` | Return None; mark as FLAGGED      | Complex decisions           |

### Detecting Conflicts

Write from Agent A:

```bash
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{
    "key": "decision",
    "value": "approve",
    "agent_id": "agent_a",
    "role": "admin",
    "conflict_strategy": "flag_for_human",
    "vector_clock": { "agent_a": 1, "agent_b": 0 }
  }'
```

Write from Agent B (concurrent):

```bash
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{
    "key": "decision",
    "value": "reject",
    "agent_id": "agent_b",
    "role": "admin",
    "conflict_strategy": "flag_for_human",
    "vector_clock": { "agent_a": 0, "agent_b": 1 }
  }'
```

**Result** (from second write):

```json
{
  "write_id": "550e8400-e29b-41d4-a716-446655440001",
  "key": "decision",
  "value": null,
  "status": "flagged",
  "conflicting_writes": [
    {
      "write_id": "550e8400-e29b-41d4-a716-446655440000",
      "agent_id": "agent_a",
      "value": "approve",
      "vector_clock": { "agent_a": 1, "agent_b": 0 },
      "timestamp": "2026-05-05T10:29:00Z"
    }
  ]
}
```

---

## History & Auditing

The **GET /history/{key}** endpoint provides a complete immutable audit trail.

### Get All History for a Key

```bash
curl "http://localhost:8000/history/budget"
```

Response (array of HistoryEntry):

```json
[
  {
    "write_id": "550e8400-e29b-41d4-a716-446655440000",
    "key": "budget",
    "value": 5000,
    "agent_id": "agent_a",
    "role": "admin",
    "vector_clock": { "agent_a": 1 },
    "timestamp": "2026-05-05T10:00:00Z",
    "write_type": "write",
    "rollback_of": null
  },
  {
    "write_id": "550e8400-e29b-41d4-a716-446655440001",
    "key": "budget",
    "value": 4200,
    "agent_id": "agent_b",
    "role": "admin",
    "vector_clock": { "agent_b": 1 },
    "timestamp": "2026-05-05T10:01:00Z",
    "write_type": "write",
    "rollback_of": null
  }
]
```

### Filter by Agent

```bash
curl "http://localhost:8000/history/budget?agent_id=agent_a"
```

### Filter by Time Range

```bash
curl "http://localhost:8000/history/budget?since=2026-05-05T10:00:00Z&until=2026-05-05T11:00:00Z"
```

### Audit Trail Benefits

- **Complete traceability**: Every write has write_id, agent_id, timestamp
- **Immutability**: Nothing is deleted; rollbacks create new entries
- **Compliance**: Full record for audits
- **Debugging**: See exact sequence of writes

---

## Time-Travel Queries

The **GET /snapshot/{key}** endpoint returns the value at a specific point in causal time using vector clocks.

### Understanding Snapshots

A snapshot answers: "What would an agent with this clock have seen?"

**Example Timeline**:

1. Agent A writes value=100 with clock `{"A":1}`
2. Agent B writes value=200 with clock `{"B":1}`
3. Query at clock `{"A":1,"B":0}` → returns A's value (100)
4. Query at clock `{"A":0,"B":1}` → returns B's value (200)

### Get a Snapshot

```bash
curl "http://localhost:8000/snapshot/budget?at_clock=%7B%22agent_a%22%3A1%7D"
```

URL-decoded query: `at_clock={"agent_a":1}`

Response:

```json
{
  "write_id": "550e8400-e29b-41d4-a716-446655440000",
  "key": "budget",
  "value": 5000,
  "agent_id": "agent_a",
  "vector_clock": { "agent_a": 1 },
  "timestamp": "2026-05-05T10:00:00Z",
  "write_type": "write",
  "rollback_of": null
}
```

### Use Cases for Time-Travel

- **Debugging**: Reconstruct what each agent saw at specific times
- **Reproducibility**: Re-create system state at any causal point
- **Forensics**: Investigate how conflicts occurred
- **Testing**: Verify behavior at specific clock states

---

## Rollbacks

The **POST /rollback/{write_id}** endpoint undoes a write by creating a new history entry.

### How Rollbacks Work

1. Finds the original write by write_id
2. Creates a new HistoryEntry with `write_type: "rollback"`
3. Sets the new entry's `rollback_of` to point to original
4. Reverts live memory to previous value
5. **Nothing is deleted** - full audit trail preserved

### Perform a Rollback

```bash
curl -X POST http://localhost:8000/rollback/550e8400-e29b-41d4-a716-446655440001 \
  -H "Content-Type: application/json" \
  -d '{
    "initiating_agent_id": "admin_agent",
    "initiating_role": "admin"
  }'
```

Response (live memory reverted):

```json
{
  "write_id": "550e8400-e29b-41d4-a716-446655440000",
  "key": "budget",
  "value": 5000,
  "agent_id": "agent_a",
  "role": "admin",
  "vector_clock": { "agent_a": 1 },
  "status": "ok",
  "conflicting_writes": [],
  "timestamp": "2026-05-05T10:05:00Z"
}
```

### Viewing Rollback in History

```bash
curl "http://localhost:8000/history/budget"
```

History now shows:

```json
[
  {
    "write_id": "550e8400-e29b-41d4-a716-446655440000",
    "key": "budget",
    "value": 5000,
    "write_type": "write"
  },
  {
    "write_id": "550e8400-e29b-41d4-a716-446655440001",
    "key": "budget",
    "value": 4200,
    "write_type": "write"
  },
  {
    "write_id": "550e8400-e29b-41d4-a716-446655440002",
    "key": "budget",
    "value": null,
    "write_type": "rollback",
    "rollback_of": "550e8400-e29b-41d4-a716-446655440001"
  }
]
```

---

## Common Workflows

### Workflow 1: Concurrent Budget Updates

**Scenario**: Multiple agents updating a shared budget simultaneously.

**Setup**:

```bash
# Register agents
curl -X POST http://localhost:8000/roles -H "Content-Type: application/json" \
  -d '{"role_name":"flight-agent","can_read":["budget.*"],"can_write":["budget.*"]}'

curl -X POST http://localhost:8000/roles -H "Content-Type: application/json" \
  -d '{"role_name":"hotel-agent","can_read":["budget.*"],"can_write":["budget.*"]}'

# Initialize budget
curl -X POST http://localhost:8000/write -H "Content-Type: application/json" \
  -d '{"key":"budget","value":10000,"agent_id":"init","role":"admin","vector_clock":{"init":1}}'
```

**Agent A (Flight) decreases budget**:

```bash
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{
    "key": "budget",
    "value": 7500,
    "agent_id": "flight-agent",
    "role": "flight-agent",
    "conflict_strategy": "highest_value",
    "vector_clock": { "flight-agent": 1, "hotel-agent": 0 }
  }'
```

**Agent B (Hotel) concurrently decreases budget**:

```bash
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{
    "key": "budget",
    "value": 6000,
    "agent_id": "hotel-agent",
    "role": "hotel-agent",
    "conflict_strategy": "highest_value",
    "vector_clock": { "flight-agent": 0, "hotel-agent": 1 }
  }'
```

**Result**: Conflict detected. With `highest_value` strategy, 7500 wins (higher value).

---

### Workflow 2: Distributed Decision Making with Human Review

**Scenario**: Multiple agents propose values; human reviews conflicts.

**Setup** (register agents):

```bash
curl -X POST http://localhost:8000/roles -H "Content-Type: application/json" \
  -d '{"role_name":"proposal-agent","can_read":["proposal"],"can_write":["proposal"]}'
```

**Agent A proposes first decision**:

```bash
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{
    "key": "proposal",
    "value": "strategy_a",
    "agent_id": "agent_a",
    "role": "proposal-agent",
    "conflict_strategy": "flag_for_human",
    "vector_clock": { "agent_a": 1, "agent_b": 0 }
  }'
```

**Agent B proposes concurrently**:

```bash
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{
    "key": "proposal",
    "value": "strategy_b",
    "agent_id": "agent_b",
    "role": "proposal-agent",
    "conflict_strategy": "flag_for_human",
    "vector_clock": { "agent_a": 0, "agent_b": 1 }
  }'
```

**Check status**:

```bash
curl "http://localhost:8000/read/proposal?agent_id=agent_a&role=proposal-agent"
```

Response shows:

```json
{
  "status": "flagged",
  "value": null,
  "conflicting_writes": [
    { "value": "strategy_a", "agent_id": "agent_a", ... },
    { "value": "strategy_b", "agent_id": "agent_b", ... }
  ]
}
```

**Human resolves by rolling back the wrong proposal**:

```bash
curl -X POST http://localhost:8000/rollback/550e8400-xxx \
  -H "Content-Type: application/json" \
  -d '{
    "initiating_agent_id": "human-reviewer",
    "initiating_role": "admin"
  }'
```

---

### Workflow 3: Complete Audit Scenario

**Scenario**: Track all changes to a key over time with full traceability.

**Initialize**:

```bash
curl -X POST http://localhost:8000/roles -H "Content-Type: application/json" \
  -d '{"role_name":"admin","can_read":["*"],"can_write":["*"]}'
```

**Series of writes**:

```bash
# Write 1
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"key":"audit_test","value":"v1","agent_id":"agent_1","role":"admin","vector_clock":{"agent_1":1}}'

# Write 2 (sequential)
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"key":"audit_test","value":"v2","agent_id":"agent_1","role":"admin","vector_clock":{"agent_1":2}}'

# Write 3 (concurrent from different agent)
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"key":"audit_test","value":"v3","agent_id":"agent_2","role":"admin","vector_clock":{"agent_2":1}}'
```

**View complete history**:

```bash
curl "http://localhost:8000/history/audit_test"
```

**Time-travel query** (what was the state after agent_1's first write?):

```bash
curl "http://localhost:8000/snapshot/audit_test?at_clock=%7B%22agent_1%22%3A1%7D"
```

---

## Error Handling

### Common Error Scenarios

#### 1. 403 Forbidden - Permission Denied

**Cause**: Role lacks permission for the key.

```bash
curl "http://localhost:8000/read/budget?agent_id=agent_a&role=auditor"
```

**Fix**: Ensure the role's `can_read` or `can_write` includes the key pattern.

#### 2. 404 Not Found - Key Not Found

**Cause**: Key doesn't exist in memory.

```bash
curl "http://localhost:8000/read/nonexistent?agent_id=agent_a&role=admin"
```

**Fix**: Write a value to the key first, or verify the key name is correct.

#### 3. 422 Unprocessable Entity - Validation Failed

**Cause**: Missing required fields or invalid types.

```bash
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"key":"budget","value":5000}' # Missing required fields
```

**Error**:

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "agent_id"],
      "msg": "Field required"
    }
  ]
}
```

**Fix**: Include all required fields: `key`, `value`, `agent_id`, `role`, `vector_clock`.

#### 4. 400 Bad Request - Invalid Input

**Cause**: Malformed input or invalid format.

```bash
curl "http://localhost:8000/snapshot/budget?at_clock=invalid-json"
```

**Error**:

```json
{
  "detail": "Invalid clock format: not valid JSON"
}
```

**Fix**: Ensure clock is valid JSON-encoded string.

#### 5. 500 Internal Server Error

**Cause**: Unexpected server error (storage failure, etc.).

**Fix**: Check server logs, verify storage is healthy (`GET /health`).

---

## Performance Considerations

### Memory Usage

- **In-memory storage**: Each write creates a new entry in history
- **Redis storage**: More efficient; cleans old entries (configurable)
- **Vector clocks**: Minimal overhead (dict of agent_ids)

### Optimization Tips

1. **Batch writes cautiously**: Vector clocks require synchronization
2. **Prune history periodically**: Old entries can be archived
3. **Use prefix filtering**: `GET /keys?prefix=budget` reduces data
4. **Monitor conflicts**: High conflict rates indicate design issues

### Scaling Strategies

1. **Multiple agents**: Partition keys by prefix (e.g., `user_*`, `budget_*`)
2. **Consistency levels**: Use `eventual` for most operations
3. **Storage selection**:
   - **Memory**: Fast, single-process
   - **Redis**: Distributed, persistence
4. **Load balancing**: Multiple Engram instances sharing Redis backend

---

## Best Practices

1. **Always start with role registration**: Enables access control
2. **Use meaningful agent_ids**: Helps with auditing (e.g., `service-name-instance-1`)
3. **Monitor conflict rates**: High conflicts indicate concurrent write patterns
4. **Leverage history**: Complete audit trail is your advantage
5. **Implement rollback policies**: Define who can initiate rollbacks
6. **Test with actual clock values**: Don't use dummy clocks in production
7. **Document role hierarchies**: Keep `can_read` and `can_write` patterns clear

---

## Integration Examples

### Python Client

```python
import httpx
import json

BASE_URL = "http://localhost:8000"

# Register role
httpx.post(f"{BASE_URL}/roles", json={
    "role_name": "app-agent",
    "can_read": ["*"],
    "can_write": ["*"]
})

# Write value
response = httpx.post(f"{BASE_URL}/write", json={
    "key": "state",
    "value": {"temp": 25, "humidity": 60},
    "agent_id": "sensor-1",
    "role": "app-agent",
    "vector_clock": {"sensor-1": 1}
})

entry = response.json()
print(f"Written: {entry['write_id']}")

# Read value
response = httpx.get(
    f"{BASE_URL}/read/state",
    params={"agent_id": "sensor-1", "role": "app-agent"}
)

data = response.json()
print(f"Current value: {data['value']}")
```

### JavaScript Client

```javascript
const BASE_URL = "http://localhost:8000";

// Register role
await fetch(`${BASE_URL}/roles`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    role_name: "app-agent",
    can_read: ["*"],
    can_write: ["*"],
  }),
});

// Write value
const writeResponse = await fetch(`${BASE_URL}/write`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    key: "state",
    value: { temp: 25, humidity: 60 },
    agent_id: "sensor-1",
    role: "app-agent",
    vector_clock: { "sensor-1": 1 },
  }),
});

const entry = await writeResponse.json();
console.log(`Written: ${entry.write_id}`);

// Read value
const readResponse = await fetch(
  `${BASE_URL}/read/state?agent_id=sensor-1&role=app-agent`,
);

const data = await readResponse.json();
console.log(`Current value:`, data.value);
```

---

**Next Steps**: Explore the [full OpenAPI specification](./openapi.yaml) or check out [example workflows](../demo/demo_presentation.py).
