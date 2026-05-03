"""
Multi-Value Register CRDT implementation.

A CRDT (Conflict-free Replicated Data Type) that stores all concurrent values
when conflicts are detected, instead of silently overwriting. Conflict resolution
is deferred until a value is needed for an agent to act on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from engram.models import ConflictStrategy, ConflictingWrite, Ordering
from engram.vector_clock import VectorClock


class MVRegister:
    """
    Multi-Value Register CRDT.

    Stores all concurrent values when conflicts are detected instead of
    silently overwriting. This is how Google Docs-style conflict handling works.

    A single logical key can hold multiple conflicting values simultaneously.
    Conflict resolution is applied when a value is needed for an agent to act on.
    """

    def __init__(self) -> None:
        self._values: list[ConflictingWrite] = []

    def write(
        self,
        value: Any,
        agent_id: str,
        role: str,
        clock: VectorClock,
    ) -> MVRegister:
        """
        Return a new MVRegister reflecting this write.
        """
        import uuid
        new_entry = ConflictingWrite(
            write_id=str(uuid.uuid4()),
            agent_id=agent_id,
            role=role,
            value=value,
            vector_clock=clock.to_dict(),
            timestamp=datetime.now(timezone.utc),
        )

        if not self._values:
            result = MVRegister()
            result._values = [new_entry]
            return result

        kept: list[ConflictingWrite] = []
        incoming_dominated = False

        for existing in self._values:
            existing_clock = VectorClock.from_dict(existing.vector_clock)
            ordering = existing_clock.compare(clock)

            if ordering == Ordering.AFTER:
                # existing dominates incoming — incoming is stale
                incoming_dominated = True
                kept.append(existing)
            elif ordering == Ordering.CONCURRENT:
                # concurrent — keep both
                kept.append(existing)
            # if BEFORE or EQUAL — existing is dominated by incoming, drop it

        if incoming_dominated:
            # incoming is stale, return unchanged
            result = MVRegister()
            result._values = list(self._values)
            return result

        kept.append(new_entry)
        result = MVRegister()
        result._values = kept
        return result

    def merge(self, other: MVRegister) -> MVRegister:
        """
        Merge two MVRegisters.
        """
        combined = list(self._values) + list(other._values)
        # Keep only values not dominated by any other
        result_values: list[ConflictingWrite] = []
        for candidate in combined:
            candidate_clock = VectorClock.from_dict(candidate.vector_clock)
            dominated = False
            for other_entry in combined:
                if other_entry is candidate:
                    continue
                other_clock = VectorClock.from_dict(other_entry.vector_clock)
                if other_clock.compare(candidate_clock) == Ordering.AFTER:
                    dominated = True
                    break
            if not dominated:
                result_values.append(candidate)

        result = MVRegister()
        result._values = result_values
        return result

    def resolve(
        self, strategy: ConflictStrategy
    ) -> tuple[Any, list[ConflictingWrite]]:
        """
        Apply a conflict resolution strategy and return the resolved value.
        """
        if not self._values:
            return None, []

        if len(self._values) == 1:
            return self._values[0].value, []

        if strategy == ConflictStrategy.FLAG_FOR_HUMAN:
            return None, list(self._values)

        if strategy == ConflictStrategy.UNION:
            all_vals = [cw.value for cw in self._values]
            return all_vals, []

        if strategy == ConflictStrategy.LATEST_CLOCK:
            def clock_sum(cw: ConflictingWrite) -> tuple[int, datetime]:
                s = sum(VectorClock.from_dict(cw.vector_clock).to_dict().values())
                return (s, cw.timestamp)
            winner = max(self._values, key=clock_sum)
            losers = [cw for cw in self._values if cw is not winner]
            return winner.value, losers

        if strategy == ConflictStrategy.LOWEST_VALUE:
            try:
                winner = min(self._values, key=lambda cw: float(cw.value))
            except (TypeError, ValueError):
                winner = min(self._values, key=lambda cw: str(cw.value))
            losers = [cw for cw in self._values if cw is not winner]
            return winner.value, losers

        if strategy == ConflictStrategy.HIGHEST_VALUE:
            try:
                winner = max(self._values, key=lambda cw: float(cw.value))
            except (TypeError, ValueError):
                winner = max(self._values, key=lambda cw: str(cw.value))
            losers = [cw for cw in self._values if cw is not winner]
            return winner.value, losers

        # fallback — latest clock
        winner = max(self._values, key=lambda cw: sum(VectorClock.from_dict(cw.vector_clock).to_dict().values()))
        losers = [cw for cw in self._values if cw is not winner]
        return winner.value, losers

    def is_conflicted(self) -> bool:
        """Return True if two or more concurrent values exist."""
        return len(self._values) >= 2

    @property
    def values(self) -> list[ConflictingWrite]:
        """Return a copy of all current values."""
        return list(self._values)
