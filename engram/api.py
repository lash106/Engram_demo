"""
FastAPI application for Engram.

Defines all HTTP endpoints and the WebSocket stub.
Uses a lifespan handler to initialize middleware components at startup.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket

from engram import __version__
from engram.access_control import AccessPolicy
from engram.history import HistoryLog
from engram.middleware import EngramMiddleware, Settings, get_storage_adapter
from engram.models import (
    ConsistencyLevel,
    HealthResponse,
    HistoryEntry,
    MemoryEntry,
    ReadRequest,
    RoleDefinition,
    RollbackRequest,
    WriteRequest,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan handler: initialize all Engram components at startup.

    1. Read settings from environment / .env
    2. Create storage adapter via get_storage_adapter()
    3. Create AccessPolicy()
    4. Create HistoryLog()
    5. Create EngramMiddleware(storage, access_policy, history_log)
    6. Store all on app.state so routes can access them
    """
    settings = Settings()
    storage = get_storage_adapter(settings)
    access_policy = AccessPolicy()
    history_log = HistoryLog(storage=storage)
    middleware = EngramMiddleware(storage, access_policy, history_log)

    app.state.settings = settings
    app.state.storage = storage
    app.state.access_policy = access_policy
    app.state.history_log = history_log
    app.state.middleware = middleware

    yield


app = FastAPI(
    title="Engram",
    description="Memory middleware for multi-agent AI systems",
    version=__version__,
    lifespan=lifespan,
)

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_middleware(request: Request) -> EngramMiddleware:
    """Dependency that retrieves the EngramMiddleware from app.state."""
    return request.app.state.middleware


def get_access_policy(request: Request) -> AccessPolicy:
    """Dependency that retrieves the AccessPolicy from app.state."""
    return request.app.state.access_policy


def get_history_log(request: Request) -> HistoryLog:
    """Dependency that retrieves the HistoryLog from app.state."""
    return request.app.state.history_log


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/write", response_model=MemoryEntry)
def write_memory(
    request: WriteRequest,
    middleware: EngramMiddleware = Depends(get_middleware),
) -> MemoryEntry:
    """
    Write a value to shared memory.

    Runs the full write pipeline: permission check, vector clock increment,
    conflict detection via CRDT, resolution, history logging, and storage.
    """
    return middleware.write(request)


@app.get("/read/{key}", response_model=MemoryEntry)
def read_memory(
    key: str,
    agent_id: str = Query(...),
    role: str = Query(...),
    consistency_level: ConsistencyLevel = Query(ConsistencyLevel.EVENTUAL),
    middleware: EngramMiddleware = Depends(get_middleware),
) -> MemoryEntry:
    """
    Read a value from shared memory.

    Runs the read pipeline: permission check, storage lookup, and
    consistency level enforcement.
    """
    read_req = ReadRequest(
        agent_id=agent_id,
        role=role,
        consistency_level=consistency_level,
    )
    return middleware.read(key, read_req)


@app.get("/history/{key}", response_model=list[HistoryEntry])
def get_history(
    key: str,
    agent_id: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    history_log: HistoryLog = Depends(get_history_log),
) -> list[HistoryEntry]:
    """
    Get the write history for a key.

    Returns all history entries in chronological order, with optional
    filters for agent_id, since, and until timestamps.
    """
    from datetime import datetime

    since_dt = datetime.fromisoformat(since) if since else None
    until_dt = datetime.fromisoformat(until) if until else None
    return history_log.get_history(key, agent_id=agent_id, since=since_dt, until=until_dt)


@app.get("/snapshot/{key}", response_model=HistoryEntry)
def get_snapshot(
    key: str,
    at_clock: str = Query(..., description="JSON-encoded vector clock dict"),
    history_log: HistoryLog = Depends(get_history_log),
) -> HistoryEntry:
    """
    Time-travel query: get the value of a key at a specific point in causal time.

    The at_clock parameter should be a JSON-encoded dict of agent_id -> counter.
    """
    import json

    clock_dict = json.loads(at_clock)
    entry = history_log.get_snapshot(key, clock_dict)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No snapshot found for key '{key}' at the given clock")
    return entry


@app.post("/rollback/{write_id}", response_model=MemoryEntry)
def rollback(
    write_id: str,
    request: RollbackRequest,
    middleware: EngramMiddleware = Depends(get_middleware),
) -> MemoryEntry:
    """
    Roll back to a previous write.

    Creates a new history entry of type ROLLBACK and updates the live memory
    to the value from the specified write_id.
    """
    return middleware.rollback(write_id, request)


@app.post("/roles", response_model=RoleDefinition)
def register_role(
    role_def: RoleDefinition,
    access_policy: AccessPolicy = Depends(get_access_policy),
) -> RoleDefinition:
    """
    Register or update a role definition.

    Defines what keys a role can read and write using glob patterns.
    """
    access_policy.register_role(
        role_name=role_def.role_name,
        can_read=role_def.can_read,
        can_write=role_def.can_write,
    )
    return role_def


@app.get("/roles/{role_name}", response_model=RoleDefinition)
def get_role(
    role_name: str,
    access_policy: AccessPolicy = Depends(get_access_policy),
) -> RoleDefinition:
    """Get a role definition by name."""
    role = access_policy.get_role(role_name)
    if role is None:
        raise HTTPException(status_code=404, detail=f"Role '{role_name}' not found")
    return RoleDefinition(
        role_name=role.role_name,
        can_read=role.can_read,
        can_write=role.can_write,
    )


@app.get("/keys", response_model=list[str])
def list_keys(
    prefix: Optional[str] = Query(None),
    request: Request = None,
) -> list[str]:
    """List all keys in storage, optionally filtered by prefix."""
    storage = request.app.state.storage
    return storage.list_keys(prefix=prefix)


@app.get("/health", response_model=HealthResponse)
def health_check(request: Request) -> HealthResponse:
    """Health check endpoint."""
    settings: Settings = request.app.state.settings
    storage = request.app.state.storage
    storage_healthy = False
    try:
        storage_healthy = storage.ping()
    except Exception:
        storage_healthy = False

    return HealthResponse(
        status="healthy" if storage_healthy else "degraded",
        storage=settings.storage_adapter,
        version=__version__,
    )


@app.websocket("/ws/memory")
async def websocket_memory(websocket: WebSocket) -> None:
    """
    WebSocket endpoint — NOT YET IMPLEMENTED.

    TODO: Stream real-time memory state updates to connected clients.
    On each write, broadcast the updated MemoryEntry to all connected clients.
    Use FastAPI WebSocket with a connection manager pattern.
    See: https://fastapi.tiangolo.com/advanced/websockets/

    Current behavior: accepts connection, sends a status message, then closes.
    """
    await websocket.accept()
    await websocket.send_json({"status": "not_implemented"})
    await websocket.close()
