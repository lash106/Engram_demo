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

        Logic:
        1. Compare the incoming clock against each existing value's clock.
        2. If incoming clock is AFTER all existing values: this write supersedes
           everything. Return new MVRegister with only this value.
        3. If incoming clock is CONCURRENT with any existing value: this is a
           conflict. Keep the concurrent value(s) AND add this new value.
           Result has multiple values.
        4. If incoming clock is BEFORE any existing value: this is a stale write.
           Discard it. Return self unchanged.
        5. If the register is empty: just add the value.

        Must NOT mutate self.

        Args:
            value: The value to write.
            agent_id: ID of the agent performing the write.
            role: Role of the agent performing the write.
            clock: The VectorClock accompanying the write.

        Returns:
            A new MVRegister reflecting the updated state.
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

        # Empty register — just add it
        if not self._values:
            result = MVRegister()
            result._values = [new_entry]
            return result

        # Compare incoming clock against each existing value's clock
        has_concurrent = False
        has_before = False

        for existing in self._values:
            existing_clock = VectorClock.from_dict(existing.vector_clock)
            ordering = existing_clock.compare(clock)

            if ordering == Ordering.BEFORE:
                # existing is BEFORE incoming → incoming is newer
                continue
            elif ordering == Ordering.AFTER:
                # existing is AFTER incoming → incoming is stale
                has_before = True
                break
            elif ordering == Ordering.CONCURRENT:
                has_concurrent = True
            # EQUAL → treat like BEFORE (incoming supersedes or ties)

        # Stale write — discard
        if has_before:
            return self

        # Incoming is after everything — replace all
        if not has_concurrent:
            result = MVRegister()
            result._values = [new_entry]
            return result

        # Concurrent — keep existing concurrent values + add new one
        result = MVRegister()
        result._values = list(self._values) + [new_entry]
        return result

    def merge(self, other: MVRegister) -> MVRegister:
        """
        Merge two MVRegisters. Used when two nodes have diverged and need to sync.

        Logic:
        For each value in other._values:
          - If its clock is AFTER any value in self._values: it replaces those.
          - If its clock is CONCURRENT with values in self._values: add it.
          - If its clock is BEFORE all values in self._values: discard it.

        The result contains exactly the set of values that are not dominated
        by any other value across both registers.

        Must NOT mutate self or other.

        Returns:
            A new MVRegister containing the merged set of non-dominated values.
        """
        # Pool all values from both registers
        all_values = list(self._values) + list(other._values)

        # Keep only values that aren't dominated by any other value
        survivors = []
        for candidate in all_values:
            candidate_clock = VectorClock.from_dict(candidate.vector_clock)
            dominated = False

            for other_val in all_values:
                if other_val is candidate:
                    continue
                other_clock = VectorClock.from_dict(other_val.vector_clock)
                if candidate_clock.compare(other_clock) == Ordering.BEFORE:
                    # candidate is older than other_val → dominated
                    dominated = True
                    break

            if not dominated:
                survivors.append(candidate)

        result = MVRegister()
        result._values = survivors
        return result

    def resolve(
        self, strategy: ConflictStrategy
    ) -> tuple[Any, list[ConflictingWrite]]:
        """
        Apply a conflict resolution strategy and return the resolved value.

        Returns:
            A tuple of (resolved_value, list_of_conflicting_writes_that_were_not_chosen).

        Strategies:
        - LOWEST_VALUE: return the numerically lowest value. Assumes values
          are comparable with <. Raise ValueError if they are not.
        - HIGHEST_VALUE: return the numerically highest value.
        - LATEST_CLOCK: return the value whose vector clock has the highest
          sum of all counter values. Tie-break by latest timestamp.
        - UNION: return a list containing all values. No value is discarded.
          conflicting_writes will be empty since all are kept.
        - FLAG_FOR_HUMAN: do NOT resolve. Return None as value and ALL values
          as conflicting_writes. The caller must mark the entry as FLAGGED.

        If only one value exists (no conflict), return it directly with
        empty conflicting_writes regardless of strategy.

        Raises:
            ValueError: If strategy is LOWEST_VALUE or HIGHEST_VALUE and values
                        are not comparable.
        """
        # No conflict — return the single value directly
        if len(self._values) == 1:
            return self._values[0].value, []

        if strategy == ConflictStrategy.FLAG_FOR_HUMAN:
            return None, list(self._values)

        if strategy == ConflictStrategy.UNION:
            return [v.value for v in self._values], []

        if strategy == ConflictStrategy.LATEST_CLOCK:
            winner = max(
                self._values,
                key=lambda v: (sum(v.vector_clock.values()), v.timestamp),
            )
        elif strategy == ConflictStrategy.LOWEST_VALUE:
            winner = min(self._values, key=lambda v: v.value)
        elif strategy == ConflictStrategy.HIGHEST_VALUE:
            winner = max(self._values, key=lambda v: v.value)

        losers = [v for v in self._values if v is not winner]
        return winner.value, losers

    def is_conflicted(self) -> bool:
        """
        Return True if and only if there are two or more values in this register,
        meaning concurrent writes exist that have not been resolved.

        Returns:
            True if conflicted, False otherwise.
        """
        return len(self._values) >= 2

    @property
    def values(self) -> list[ConflictingWrite]:
        """
        Return a copy of all current values in this register.

        Returns:
            A list of ConflictingWrite objects (copy, not reference).
        """
        return list(self._values)
