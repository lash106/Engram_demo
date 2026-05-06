"""
Tests for engram.crdt.MVRegister.

Covers: write, merge, resolve, is_conflicted, values.
"""

from engram.crdt import MVRegister
from engram.models import ConflictStrategy
from engram.vector_clock import VectorClock


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


def test_write_to_empty_register():
    """Writing to an empty register should store exactly one value."""
    reg = MVRegister()
    vc = VectorClock({"A": 1})
    reg2 = reg.write("hello", "agent-A", "admin", vc)
    assert len(reg2.values) == 1
    assert reg2.values[0].value == "hello"
    assert len(reg.values) == 0  # original not mutated


def test_write_supersedes_when_after():
    """A write with a strictly later clock should replace all existing values."""
    reg = MVRegister()
    reg = reg.write("old", "agent-A", "admin", VectorClock({"A": 1}))
    reg = reg.write("new", "agent-A", "admin", VectorClock({"A": 2}))
    assert len(reg.values) == 1
    assert reg.values[0].value == "new"


def test_write_concurrent_creates_conflict():
    """Two writes with concurrent clocks should both be stored."""
    reg = MVRegister()
    reg = reg.write(5000, "agent-A", "admin", VectorClock({"A": 1}))
    reg = reg.write(3000, "agent-B", "admin", VectorClock({"B": 1}))
    assert len(reg.values) == 2
    values = {v.value for v in reg.values}
    assert values == {5000, 3000}


def test_write_stale_is_discarded():
    """A write with a clock BEFORE existing values should be discarded."""
    reg = MVRegister()
    reg = reg.write("new", "agent-A", "admin", VectorClock({"A": 2}))
    reg = reg.write("old", "agent-B", "admin", VectorClock({"A": 1}))
    assert len(reg.values) == 1
    assert reg.values[0].value == "new"


def test_write_does_not_mutate_original():
    """The original MVRegister must remain unchanged after write."""
    reg = MVRegister()
    reg2 = reg.write("hello", "agent-A", "admin", VectorClock({"A": 1}))
    assert len(reg.values) == 0
    assert len(reg2.values) == 1


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


def test_merge_two_diverged_registers():
    """Merging two registers with concurrent values should keep both."""
    reg_a = MVRegister()
    reg_a = reg_a.write(5000, "agent-A", "admin", VectorClock({"A": 1}))

    reg_b = MVRegister()
    reg_b = reg_b.write(3000, "agent-B", "admin", VectorClock({"B": 1}))

    merged = reg_a.merge(reg_b)
    assert len(merged.values) == 2
    values = {v.value for v in merged.values}
    assert values == {5000, 3000}


def test_merge_dominated_values_removed():
    """Values dominated by a newer value in the other register should be dropped."""
    reg_a = MVRegister()
    reg_a = reg_a.write("old", "agent-A", "admin", VectorClock({"A": 1}))

    reg_b = MVRegister()
    reg_b = reg_b.write("new", "agent-B", "admin", VectorClock({"A": 1, "B": 1}))

    merged = reg_a.merge(reg_b)
    assert len(merged.values) == 1
    assert merged.values[0].value == "new"


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


def test_resolve_single_value_returns_it():
    """If only one value exists, resolve should return it regardless of strategy."""
    reg = MVRegister()
    reg = reg.write(5000, "agent-A", "admin", VectorClock({"A": 1}))
    value, losers = reg.resolve(ConflictStrategy.LATEST_CLOCK)
    assert value == 5000
    assert losers == []


def test_resolve_latest_clock():
    """LATEST_CLOCK should pick the value with the highest clock sum."""
    reg = MVRegister()
    reg = reg.write(5000, "agent-A", "admin", VectorClock({"A": 1}))
    reg = reg.write(3000, "agent-B", "admin", VectorClock({"B": 3}))
    value, losers = reg.resolve(ConflictStrategy.LATEST_CLOCK)
    assert value == 3000
    assert len(losers) == 1
    assert losers[0].value == 5000


def test_resolve_latest_clock_tiebreaks_by_timestamp():
    """When clock sums are equal, LATEST_CLOCK should pick the later timestamp."""
    reg = MVRegister()
    reg = reg.write(5000, "agent-A", "admin", VectorClock({"A": 2, "B": 3}))  # sum=5, earlier
    reg = reg.write(3000, "agent-B", "admin", VectorClock({"C": 5}))  # sum=5, later
    value, losers = reg.resolve(ConflictStrategy.LATEST_CLOCK)
    assert value == 3000
    assert losers[0].value == 5000


def test_resolve_lowest_value():
    """LOWEST_VALUE should pick the numerically smallest value."""
    reg = MVRegister()
    reg = reg.write(5000, "agent-A", "admin", VectorClock({"A": 1}))
    reg = reg.write(3000, "agent-B", "admin", VectorClock({"B": 1}))
    value, losers = reg.resolve(ConflictStrategy.LOWEST_VALUE)
    assert value == 3000
    assert losers[0].value == 5000


def test_resolve_highest_value():
    """HIGHEST_VALUE should pick the numerically largest value."""
    reg = MVRegister()
    reg = reg.write(5000, "agent-A", "admin", VectorClock({"A": 1}))
    reg = reg.write(3000, "agent-B", "admin", VectorClock({"B": 1}))
    value, losers = reg.resolve(ConflictStrategy.HIGHEST_VALUE)
    assert value == 5000
    assert losers[0].value == 3000


def test_resolve_union():
    """UNION should return a list of all values with no conflicts."""
    reg = MVRegister()
    reg = reg.write(5000, "agent-A", "admin", VectorClock({"A": 1}))
    reg = reg.write(3000, "agent-B", "admin", VectorClock({"B": 1}))
    value, losers = reg.resolve(ConflictStrategy.UNION)
    assert set(value) == {5000, 3000}
    assert losers == []


def test_resolve_flag_for_human():
    """FLAG_FOR_HUMAN should return None and list all values as conflicts."""
    reg = MVRegister()
    reg = reg.write(5000, "agent-A", "admin", VectorClock({"A": 1}))
    reg = reg.write(3000, "agent-B", "admin", VectorClock({"B": 1}))
    value, conflicts = reg.resolve(ConflictStrategy.FLAG_FOR_HUMAN)
    assert value is None
    assert len(conflicts) == 2
    assert {c.value for c in conflicts} == {5000, 3000}


# ---------------------------------------------------------------------------
# is_conflicted
# ---------------------------------------------------------------------------


def test_is_conflicted_with_single_value():
    """A register with one value should not be conflicted."""
    reg = MVRegister()
    reg = reg.write(5000, "agent-A", "admin", VectorClock({"A": 1}))
    assert reg.is_conflicted() is False


def test_is_conflicted_with_concurrent_writes():
    """A register with concurrent writes should be conflicted."""
    reg = MVRegister()
    reg = reg.write(5000, "agent-A", "admin", VectorClock({"A": 1}))
    reg = reg.write(3000, "agent-B", "admin", VectorClock({"B": 1}))
    assert reg.is_conflicted() is True
