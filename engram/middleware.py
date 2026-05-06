"""
Central wiring module for Engram.

All reads and writes flow through EngramMiddleware, which composes
storage, access control, history, vector clocks, and CRDTs.
"""

from __future__ import annotations

from fastapi import HTTPException
from pydantic_settings import BaseSettings, SettingsConfigDict

from engram.access_control import AccessPolicy
from engram.crdt import MVRegister
from engram.history import HistoryLog
from engram.models import (
    ConflictStrategy,
    ConflictingWrite,
    ConsistencyLevel,
    HistoryEntry,
    MemoryEntry,
    MemoryStatus,
    Ordering,
    ReadRequest,
    RollbackRequest,
    WriteRequest,
    WriteType,
)
from engram.storage.base import StorageAdapter
from engram.storage.memory import InMemoryAdapter
from engram.storage.redis_adapter import RedisAdapter
from engram.vector_clock import VectorClock


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    storage_adapter: str = "memory"
    redis_url: str = "redis://localhost:6379/0"
    engram_host: str = "0.0.0.0"
    engram_port: int = 8000
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env")


def get_storage_adapter(settings: Settings) -> StorageAdapter:
    """
    Factory function that returns the appropriate StorageAdapter based on settings.

    Reads STORAGE_ADAPTER from settings:
    - "memory" -> InMemoryAdapter()
    - "redis"  -> RedisAdapter(settings.redis_url)

    Args:
        settings: The application Settings instance.

    Returns:
        A StorageAdapter instance.

    Raises:
        ValueError: If the adapter name is not recognized.
    """
    adapter_name = settings.storage_adapter.lower()
    if adapter_name == "memory":
        return InMemoryAdapter()
    elif adapter_name == "redis":
        return RedisAdapter(settings.redis_url)
    else:
        raise ValueError(
            f"Unknown storage adapter: {settings.storage_adapter!r}. "
            f"Supported adapters: 'memory', 'redis'."
        )


class EngramMiddleware:
    """
    The central wiring class. All reads and writes flow through here.

    Composes storage, access control, history, vector clocks, and CRDTs.
    """

    def __init__(
        self,
        storage: StorageAdapter,
        access_policy: AccessPolicy,
        history_log: HistoryLog,
    ) -> None:
        self.storage = storage
        self.access_policy = access_policy
        self.history_log = history_log

    def write(self, request: WriteRequest) -> MemoryEntry:
        """
        Full write pipeline.

        Steps:
        1. Check write permission: access_policy.check_write(role, key).
           Raise HTTPException(403) if denied.
        2. Build incoming VectorClock: VectorClock.from_dict(request.vector_clock).
        3. Increment the incoming clock: clock = clock.increment(request.agent_id).
        4. Read existing MemoryEntry from storage: existing = storage.read(key).
        5. If existing entry found:
           a. Build existing clock: VectorClock.from_dict(existing.vector_clock).
              Compare: existing_clock.compare(incoming_clock).
           b. If CONCURRENT: use MVRegister to merge. resolve() with strategy.
              Set status=CONFLICTED if multiple values remain.
              Set status=FLAGGED if strategy is FLAG_FOR_HUMAN.
           c. If BEFORE or EQUAL: safe to overwrite, proceed normally.
           d. If AFTER: incoming write is stale. Raise HTTPException(409).
           e. Merge clocks: final_clock = incoming_clock.merge(existing_clock).
        6. Build MemoryEntry with resolved value and final clock.
        7. Build HistoryEntry and call history_log.append().
        8. Call storage.write(entry).
        9. Return the MemoryEntry.

        Args:
            request: The WriteRequest containing key, value, agent_id,
                     role, consistency_level, conflict_strategy, and vector_clock.

        Returns:
            The resulting MemoryEntry after the write is applied.

        Raises:
            HTTPException(403): If the role lacks write permission for this key.
            HTTPException(409): If the incoming write is stale (AFTER ordering).
        """
        if not self.access_policy.check_write(request.role, request.key):
            raise HTTPException(
                status_code=403,
                detail=f"Role '{request.role}' cannot write key '{request.key}'",
            )

        incoming_clock = VectorClock.from_dict(request.vector_clock).increment(
            request.agent_id
        )

        existing = self.storage.read(request.key)
        resolved_value = request.value
        conflicting_writes: list[ConflictingWrite] = []
        status = MemoryStatus.OK
        final_clock = incoming_clock

        if existing is not None:
            existing_clock = VectorClock.from_dict(existing.vector_clock)
            ordering = existing_clock.compare(incoming_clock)

            if ordering == Ordering.AFTER:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Stale write: incoming clock is behind the stored entry"
                    ),
                )

            if ordering == Ordering.CONCURRENT:
                register = MVRegister()
                existing_conflicts = list(existing.conflicting_writes)
                register._values = [
                    ConflictingWrite(
                        write_id=existing.write_id,
                        agent_id=existing.agent_id,
                        role=existing.role,
                        value=existing.value,
                        vector_clock=dict(existing.vector_clock),
                        timestamp=existing.timestamp,
                    ),
                    *existing_conflicts,
                ]

                register = register.write(
                    request.value,
                    request.agent_id,
                    request.role,
                    incoming_clock,
                )

                resolved_value, conflicting_writes = register.resolve(
                    request.conflict_strategy
                )
                if request.conflict_strategy == ConflictStrategy.FLAG_FOR_HUMAN:
                    status = MemoryStatus.FLAGGED
                elif register.is_conflicted():
                    status = MemoryStatus.CONFLICTED

            final_clock = incoming_clock.merge(existing_clock)

        entry = MemoryEntry(
            key=request.key,
            value=resolved_value,
            agent_id=request.agent_id,
            role=request.role,
            vector_clock=final_clock.to_dict(),
            consistency_level=request.consistency_level,
            conflict_strategy=request.conflict_strategy,
            status=status,
            conflicting_writes=conflicting_writes,
        )

        history_entry = HistoryEntry(
            write_id=entry.write_id,
            key=entry.key,
            value=request.value,
            agent_id=entry.agent_id,
            role=entry.role,
            vector_clock=entry.vector_clock,
            timestamp=entry.timestamp,
            write_type=WriteType.WRITE,
        )
        self.history_log.append(history_entry)
        self.storage.write(entry)
        return entry

    def read(self, key: str, request: ReadRequest) -> MemoryEntry:
        """
        Read pipeline.

        Steps:
        1. Check read permission: access_policy.check_read(role, key).
           Raise HTTPException(403) if denied.
        2. Read from storage.
           Raise HTTPException(404) if key not found.
        3. Apply consistency level:
           - EVENTUAL: return immediately. No further checks.
           - CAUSAL:
             TODO — verify that the returned entry's vector clock does not
             violate causal ordering relative to the requesting agent's last
             known clock. For now raise NotImplementedError with explanation.
           - STRONG:
             TODO — in a distributed setting this requires coordination to
             confirm no pending writes exist. For now raise NotImplementedError
             with explanation.
        4. Return MemoryEntry.

        Args:
            key: The memory key to read.
            request: The ReadRequest containing agent_id, role, and consistency_level.

        Returns:
            The MemoryEntry for the given key.

        Raises:
            HTTPException(403): If the role lacks read permission for this key.
            HTTPException(404): If the key does not exist in storage.
            NotImplementedError: If consistency level is CAUSAL or STRONG.
        """
        if not self.access_policy.check_read(request.role, key):
            raise HTTPException(
                status_code=403,
                detail=f"Role '{request.role}' cannot read key '{key}'",
            )

        entry = self.storage.read(key)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Key '{key}' not found")

        if request.consistency_level == ConsistencyLevel.EVENTUAL:
            return entry
        if request.consistency_level == ConsistencyLevel.CAUSAL:
            raise NotImplementedError(
                "Causal consistency requires validating the returned clock against "
                "the caller's known clock, which is not implemented yet."
            )
        if request.consistency_level == ConsistencyLevel.STRONG:
            raise NotImplementedError(
                "Strong consistency requires coordination with all nodes, which is "
                "not implemented yet."
            )
        return entry

    def rollback(self, write_id: str, request: RollbackRequest) -> MemoryEntry:
        """
        Rollback pipeline.

        Steps:
        1. Get the target history entry: history_log.get_entry(write_id).
           Raise HTTPException(404) if not found.
        2. Check write permission for the entry's key.
           Raise HTTPException(403) if denied.
        3. Call history_log.create_rollback_entry() to build the rollback entry.
        4. Build a new MemoryEntry from the rollback entry's value and metadata.
        5. Append rollback HistoryEntry to history_log.
        6. Write new MemoryEntry to storage.
        7. Return the new MemoryEntry.

        Args:
            write_id: The write_id of the history entry to roll back to.
            request: The RollbackRequest containing initiating_agent_id and
                     initiating_role.

        Returns:
            The new MemoryEntry reflecting the rolled-back state.

        Raises:
            HTTPException(404): If the write_id is not found in history.
            HTTPException(403): If the role lacks write permission for the key.
        """
        original = self.history_log.get_entry(write_id)
        if original is None:
            raise HTTPException(
                status_code=404,
                detail=f"No history entry found for write_id '{write_id}'",
            )

        if not self.access_policy.check_write(
            request.initiating_role, original.key
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Role '{request.initiating_role}' cannot write key '{original.key}'"
                ),
            )

        rollback_entry = self.history_log.create_rollback_entry(
            write_id=write_id,
            initiating_agent_id=request.initiating_agent_id,
            initiating_role=request.initiating_role,
        )
        self.history_log.append(rollback_entry)

        current = self.storage.read(original.key)
        consistency_level = (
            current.consistency_level
            if current is not None
            else ConsistencyLevel.EVENTUAL
        )
        conflict_strategy = (
            current.conflict_strategy
            if current is not None
            else ConflictStrategy.LATEST_CLOCK
        )

        entry = MemoryEntry(
            write_id=rollback_entry.write_id,
            key=rollback_entry.key,
            value=rollback_entry.value,
            agent_id=rollback_entry.agent_id,
            role=rollback_entry.role,
            vector_clock=dict(rollback_entry.vector_clock),
            consistency_level=consistency_level,
            conflict_strategy=conflict_strategy,
            status=MemoryStatus.OK,
            conflicting_writes=[],
            timestamp=rollback_entry.timestamp,
        )
        self.storage.write(entry)
        return entry
