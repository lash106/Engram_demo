"""
End-to-end test: Multi-Agent Budget Conflict Detection & Resolution

Scenario:
Two AI agents (agent_a and agent_b) with different roles attempt to update
a shared budget simultaneously, triggering:
  1. Concurrent write detection via vector clocks
  2. CRDT conflict resolution
  3. Access control enforcement
  4. Full audit trail via history log
  5. Time-travel queries
"""

import pytest
from fastapi.testclient import TestClient

from engram.access_control import AccessPolicy
from engram.api import app
from engram.history import HistoryLog
from engram.middleware import EngramMiddleware, Settings
from engram.models import (
    ConflictStrategy,
    HistoryEntry,
    MemoryStatus,
    Ordering,
    WriteType,
)
from engram.storage.memory import InMemoryAdapter
from engram.vector_clock import VectorClock
import json


@pytest.fixture
def e2e_setup():
    """
    Setup for the E2E test:
    - Two roles: budget-writer (can write to budget.*) and auditor (read-only)
    - In-memory storage
    - Fresh history log
    - Wired middleware
    """
    storage = InMemoryAdapter()
    policy = AccessPolicy()

    # Register budget-writer role: can read and write to budget.* keys
    policy.register_role(
        "budget-writer",
        can_read=["budget.*"],
        can_write=["budget.*"],
    )

    # Register auditor role: read-only access to everything
    policy.register_role("auditor", can_read=["*"], can_write=[])

    history_log = HistoryLog(storage=storage)
    middleware = EngramMiddleware(storage, policy, history_log)

    # Setup FastAPI app with test fixtures
    app.state.settings = Settings(storage_adapter="memory")
    app.state.storage = storage
    app.state.access_policy = policy
    app.state.history_log = history_log
    app.state.middleware = middleware

    client = TestClient(app)

    return {
        "client": client,
        "middleware": middleware,
        "storage": storage,
        "policy": policy,
        "history_log": history_log,
    }


class TestBudgetConflictE2E:
    """End-to-end test suite for multi-agent budget conflict scenario."""

    # ========================================================================
    # PHASE 1: Initial Setup & Authorization
    # ========================================================================

    def test_phase1_health_check(self, e2e_setup):
        """Verify the system is healthy and ready."""
        client = e2e_setup["client"]
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["storage"] == "memory"

    def test_phase1_unauthorized_write_fails(self, e2e_setup):
        """Auditor (read-only) attempts to write and should be rejected."""
        client = e2e_setup["client"]

        payload = {
            "key": "budget.project_x",
            "value": 9999,
            "agent_id": "auditor_agent",
            "role": "auditor",
            "consistency_level": "eventual",
            "conflict_strategy": "highest_value",
            "vector_clock": {},
        }

        resp = client.post("/write", json=payload)
        assert resp.status_code == 403
        assert "cannot write" in resp.json()["detail"]

    # ========================================================================
    # PHASE 2: Concurrent Writes (Trigger Conflict)
    # ========================================================================

    def test_phase2_agent_a_first_write(self, e2e_setup):
        """Agent A writes budget.project_x = 5000 with clock {A: 1}."""
        client = e2e_setup["client"]
        middleware = e2e_setup["middleware"]

        payload = {
            "key": "budget.project_x",
            "value": 5000,
            "agent_id": "agent_a",
            "role": "budget-writer",
            "consistency_level": "eventual",
            "conflict_strategy": "highest_value",
            "vector_clock": {},  # empty, will increment to {A: 1}
        }

        resp = client.post("/write", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["key"] == "budget.project_x"
        assert data["value"] == 5000
        assert data["status"] == "ok"
        assert data["vector_clock"]["agent_a"] == 1

        # Verify it's stored in the storage
        stored = middleware.storage.read("budget.project_x")
        assert stored is not None
        assert stored.value == 5000
        assert stored.vector_clock == {"agent_a": 1}

    def test_phase2_agent_b_concurrent_write(self, e2e_setup):
        """
        Agent B writes budget.project_x = 3000 concurrently.
        Agent B's clock is {B: 1}, which is CONCURRENT with {A: 1}.
        This should trigger conflict detection and merge both values.
        """
        client = e2e_setup["client"]
        middleware = e2e_setup["middleware"]

        # First ensure Agent A has written
        payload_a = {
            "key": "budget.project_x",
            "value": 5000,
            "agent_id": "agent_a",
            "role": "budget-writer",
            "consistency_level": "eventual",
            "conflict_strategy": "highest_value",
            "vector_clock": {},
        }
        client.post("/write", json=payload_a)

        # Now Agent B writes concurrently (different agent, so clock is concurrent)
        payload_b = {
            "key": "budget.project_x",
            "value": 3000,
            "agent_id": "agent_b",
            "role": "budget-writer",
            "consistency_level": "eventual",
            "conflict_strategy": "highest_value",
            "vector_clock": {},  # empty clock from B's perspective
        }

        resp = client.post("/write", json=payload_b)
        assert resp.status_code == 200
        data = resp.json()

        # After conflict resolution with highest_value strategy, should be 5000
        assert data["value"] == 5000
        assert data["status"] == "conflicted"
        # Clock should be merged: {agent_a: 1, agent_b: 1}
        assert data["vector_clock"]["agent_a"] == 1
        assert data["vector_clock"]["agent_b"] == 1

        # Verify storage has the merged entry
        stored = middleware.storage.read("budget.project_x")
        assert stored is not None
        assert stored.status == MemoryStatus.CONFLICTED
        # Should have conflicting_writes with the concurrent value
        assert len(stored.conflicting_writes) >= 1

    # ========================================================================
    # PHASE 3: Conflict Resolution
    # ========================================================================

    def test_phase3_read_with_highest_value_strategy(self, e2e_setup):
        """
        After conflict, read the value.
        With highest_value strategy, should resolve to 5000.
        """
        client = e2e_setup["client"]

        # Setup: Agent A writes 5000
        payload_a = {
            "key": "budget.project_x",
            "value": 5000,
            "agent_id": "agent_a",
            "role": "budget-writer",
            "consistency_level": "eventual",
            "conflict_strategy": "highest_value",
            "vector_clock": {},
        }
        client.post("/write", json=payload_a)

        # Setup: Agent B writes 3000 concurrently
        payload_b = {
            "key": "budget.project_x",
            "value": 3000,
            "agent_id": "agent_b",
            "role": "budget-writer",
            "consistency_level": "eventual",
            "conflict_strategy": "highest_value",
            "vector_clock": {},
        }
        client.post("/write", json=payload_b)

        # Now read: should resolve to 5000 (highest value)
        resp = client.get(
            "/read/budget.project_x",
            params={
                "agent_id": "agent_a",
                "role": "budget-writer",
                "consistency_level": "eventual",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["key"] == "budget.project_x"
        assert data["value"] == 5000
        assert data["status"] == "conflicted"

    def test_phase3_read_with_lowest_value_strategy(self, e2e_setup):
        """
        With lowest_value strategy, should resolve to 3000.
        This tests that the resolution strategy actually affects the outcome.
        """
        client = e2e_setup["client"]

        # Setup: Agent A writes 5000 with lowest_value strategy
        payload_a = {
            "key": "budget.project_y",
            "value": 5000,
            "agent_id": "agent_a",
            "role": "budget-writer",
            "consistency_level": "eventual",
            "conflict_strategy": "lowest_value",
            "vector_clock": {},
        }
        client.post("/write", json=payload_a)

        # Setup: Agent B writes 3000 concurrently with lowest_value strategy
        payload_b = {
            "key": "budget.project_y",
            "value": 3000,
            "agent_id": "agent_b",
            "role": "budget-writer",
            "consistency_level": "eventual",
            "conflict_strategy": "lowest_value",
            "vector_clock": {},
        }
        client.post("/write", json=payload_b)

        # Read should resolve to 3000 (lowest value)
        resp = client.get(
            "/read/budget.project_y",
            params={
                "agent_id": "agent_a",
                "role": "budget-writer",
                "consistency_level": "eventual",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["value"] == 3000

    # ========================================================================
    # PHASE 4: History & Audit Trail
    # ========================================================================

    def test_phase4_history_log_has_all_writes(self, e2e_setup):
        """
        History log should contain entries for:
        1. Agent A's initial write
        2. Agent B's concurrent write
        3. The conflict record
        """
        client = e2e_setup["client"]
        history_log = e2e_setup["history_log"]

        # Setup: trigger conflict
        payload_a = {
            "key": "budget.project_x",
            "value": 5000,
            "agent_id": "agent_a",
            "role": "budget-writer",
            "consistency_level": "eventual",
            "conflict_strategy": "highest_value",
            "vector_clock": {},
        }
        client.post("/write", json=payload_a)

        payload_b = {
            "key": "budget.project_x",
            "value": 3000,
            "agent_id": "agent_b",
            "role": "budget-writer",
            "consistency_level": "eventual",
            "conflict_strategy": "highest_value",
            "vector_clock": {},
        }
        client.post("/write", json=payload_b)

        # Query history for budget.project_x
        history_entries = history_log.get_history("budget.project_x")
        assert len(history_entries) >= 2

        # Should have writes from both agents
        agent_a_writes = [
            e
            for e in history_entries
            if e.agent_id == "agent_a" and e.write_type == WriteType.WRITE
        ]
        agent_b_writes = [
            e
            for e in history_entries
            if e.agent_id == "agent_b" and e.write_type == WriteType.WRITE
        ]

        assert len(agent_a_writes) >= 1
        assert len(agent_b_writes) >= 1

    def test_phase4_history_endpoint(self, e2e_setup):
        """History endpoint should return full audit trail."""
        client = e2e_setup["client"]

        # Setup: trigger conflict
        payload_a = {
            "key": "budget.audit_test",
            "value": 100,
            "agent_id": "agent_a",
            "role": "budget-writer",
            "consistency_level": "eventual",
            "conflict_strategy": "highest_value",
            "vector_clock": {},
        }
        client.post("/write", json=payload_a)

        payload_b = {
            "key": "budget.audit_test",
            "value": 200,
            "agent_id": "agent_b",
            "role": "budget-writer",
            "consistency_level": "eventual",
            "conflict_strategy": "highest_value",
            "vector_clock": {},
        }
        client.post("/write", json=payload_b)

        # Query via endpoint
        resp = client.get("/history/budget.audit_test")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2

    # ========================================================================
    # PHASE 5: Time-Travel & Snapshots
    # ========================================================================

    def test_phase5_snapshot_at_agent_a_clock(self, e2e_setup):
        """
        Query snapshot at Agent A's clock {A: 1}.
        Should return the value as it was when only Agent A had written.
        """
        client = e2e_setup["client"]

        # Setup: Agent A writes
        payload_a = {
            "key": "budget.time_travel",
            "value": 5000,
            "agent_id": "agent_a",
            "role": "budget-writer",
            "consistency_level": "eventual",
            "conflict_strategy": "highest_value",
            "vector_clock": {},
        }
        resp_a = client.post("/write", json=payload_a)
        clock_a = resp_a.json()["vector_clock"]

        # Setup: Agent B writes concurrently
        payload_b = {
            "key": "budget.time_travel",
            "value": 3000,
            "agent_id": "agent_b",
            "role": "budget-writer",
            "consistency_level": "eventual",
            "conflict_strategy": "highest_value",
            "vector_clock": {},
        }
        client.post("/write", json=payload_b)

        # Query snapshot at Agent A's clock (JSON-encoded)
        snapshot_params = {"at_clock": json.dumps(clock_a)}
        resp = client.get("/snapshot/budget.time_travel", params=snapshot_params)
        assert resp.status_code == 200
        data = resp.json()
        assert data["key"] == "budget.time_travel"
        assert data["value"] == 5000

    # ========================================================================
    # PHASE 6: Rollback
    # ========================================================================

    def test_phase6_rollback_creates_history_entry(self, e2e_setup):
        """
        Rollback should create a ROLLBACK entry in the history,
        not delete the original write.
        """
        client = e2e_setup["client"]
        history_log = e2e_setup["history_log"]

        # Setup: Agent A writes
        payload = {
            "key": "budget.rollback_test",
            "value": 5000,
            "agent_id": "agent_a",
            "role": "budget-writer",
            "consistency_level": "eventual",
            "conflict_strategy": "highest_value",
            "vector_clock": {},
        }
        resp = client.post("/write", json=payload)
        write_id = resp.json()["write_id"]

        # Perform rollback
        rollback_payload = {
            "initiating_agent_id": "agent_a",
            "initiating_role": "budget-writer",
        }
        resp = client.post(f"/rollback/{write_id}", json=rollback_payload)
        assert resp.status_code == 200

        # Check history: should have WRITE and ROLLBACK entries
        history_entries = history_log.get_history("budget.rollback_test")
        write_entries = [
            e for e in history_entries if e.write_type == WriteType.WRITE
        ]
        rollback_entries = [
            e for e in history_entries if e.write_type == WriteType.ROLLBACK
        ]

        assert len(write_entries) >= 1
        assert len(rollback_entries) >= 1

    # ========================================================================
    # PHASE 7: Full Workflow (Integration)
    # ========================================================================

    def test_phase7_full_workflow(self, e2e_setup):
        """
        Complete workflow:
        1. Agent A writes 5000
        2. Agent B writes 3000 concurrently
        3. Read resolves to 5000 (highest_value)
        4. Verify history has 2+ entries
        5. Verify access control prevents auditor from writing
        6. Auditor can read
        """
        client = e2e_setup["client"]
        middleware = e2e_setup["middleware"]

        # Step 1: Agent A writes 5000
        payload_a = {
            "key": "budget.integration_test",
            "value": 5000,
            "agent_id": "agent_a",
            "role": "budget-writer",
            "consistency_level": "eventual",
            "conflict_strategy": "highest_value",
            "vector_clock": {},
        }
        resp_a = client.post("/write", json=payload_a)
        assert resp_a.status_code == 200
        assert resp_a.json()["value"] == 5000

        # Step 2: Agent B writes 3000 concurrently
        payload_b = {
            "key": "budget.integration_test",
            "value": 3000,
            "agent_id": "agent_b",
            "role": "budget-writer",
            "consistency_level": "eventual",
            "conflict_strategy": "highest_value",
            "vector_clock": {},
        }
        resp_b = client.post("/write", json=payload_b)
        assert resp_b.status_code == 200
        assert resp_b.json()["status"] == "conflicted"

        # Step 3: Read resolves to 5000
        resp_read = client.get(
            "/read/budget.integration_test",
            params={
                "agent_id": "agent_a",
                "role": "budget-writer",
                "consistency_level": "eventual",
            },
        )
        assert resp_read.status_code == 200
        assert resp_read.json()["value"] == 5000
        assert resp_read.json()["status"] == "conflicted"

        # Step 4: Verify history
        history_entries = middleware.history_log.get_history(
            "budget.integration_test"
        )
        assert len(history_entries) >= 2

        # Step 5: Auditor cannot write
        payload_auditor_write = {
            "key": "budget.integration_test",
            "value": 9999,
            "agent_id": "auditor_1",
            "role": "auditor",
            "consistency_level": "eventual",
            "conflict_strategy": "highest_value",
            "vector_clock": {},
        }
        resp_denied = client.post("/write", json=payload_auditor_write)
        assert resp_denied.status_code == 403

        # Step 6: Auditor can read
        resp_auditor_read = client.get(
            "/read/budget.integration_test",
            params={"agent_id": "auditor_1", "role": "auditor"},
        )
        assert resp_auditor_read.status_code == 200
        assert resp_auditor_read.json()["value"] == 5000
