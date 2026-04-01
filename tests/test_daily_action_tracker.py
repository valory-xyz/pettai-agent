"""Tests for DailyActionTracker reserve/release slot pattern."""

import json
import sys
import os
import tempfile
from pathlib import Path

# Allow importing from olas-sdk-starter/agent without package install
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "olas-sdk-starter", "agent")
)

from daily_action_tracker import DailyActionTracker


def _make_tracker(tmp_path, required=9, **kwargs):
    return DailyActionTracker(
        storage_path=Path(tmp_path) / "state.json",
        required_actions=required,
        **kwargs,
    )


def test_reserve_slot_basic():
    """Reserve 9 slots, 10th must fail."""
    with tempfile.TemporaryDirectory() as tmp:
        t = _make_tracker(tmp)
        for i in range(9):
            assert t.reserve_slot() is True, f"reserve_slot #{i+1} should succeed"
        assert t.reserve_slot() is False, "10th reserve_slot should fail"


def test_actions_remaining_includes_in_flight():
    """actions_remaining accounts for in-flight reservations."""
    with tempfile.TemporaryDirectory() as tmp:
        t = _make_tracker(tmp)
        assert t.actions_remaining() == 9
        t.reserve_slot()
        assert t.actions_remaining() == 8
        t.reserve_slot()
        assert t.actions_remaining() == 7


def test_release_slot_success_increments_completed():
    """Releasing with success=True records the action and frees in-flight."""
    with tempfile.TemporaryDirectory() as tmp:
        t = _make_tracker(tmp)
        t.reserve_slot()
        assert t._in_flight == 1
        assert t.actions_completed() == 0

        t.release_slot(success=True, action_name="RUB")
        assert t._in_flight == 0
        assert t.actions_completed() == 1
        assert t.actions_remaining() == 8


def test_release_slot_failure_frees_slot():
    """Releasing with success=False frees the slot without recording."""
    with tempfile.TemporaryDirectory() as tmp:
        t = _make_tracker(tmp)
        t.reserve_slot()
        assert t.actions_remaining() == 8

        t.release_slot(success=False)
        assert t._in_flight == 0
        assert t.actions_completed() == 0
        assert t.actions_remaining() == 9


def test_release_slot_clamps_to_zero():
    """Releasing without a prior reserve doesn't go negative."""
    with tempfile.TemporaryDirectory() as tmp:
        t = _make_tracker(tmp)
        t.release_slot(success=False)
        assert t._in_flight == 0


def test_mixed_reserve_release_respects_limit():
    """Simulate concurrent txs: reserve all 9, release some as failed, re-reserve."""
    with tempfile.TemporaryDirectory() as tmp:
        t = _make_tracker(tmp)
        # Reserve all 9
        for _ in range(9):
            assert t.reserve_slot() is True
        assert t.reserve_slot() is False

        # 2 fail
        t.release_slot(success=False)
        t.release_slot(success=False)
        assert t._in_flight == 7
        assert t.actions_remaining() == 2

        # Can reserve 2 more
        assert t.reserve_slot() is True
        assert t.reserve_slot() is True
        assert t.reserve_slot() is False

        # 9 succeed
        for _ in range(9):
            t.release_slot(success=True, action_name="RUB")
        assert t.actions_completed() == 9
        assert t._in_flight == 0
        assert t.actions_remaining() == 0


def test_completed_plus_inflight_blocks_reserve():
    """5 completed + 4 in-flight = 9 used, no more reserves."""
    with tempfile.TemporaryDirectory() as tmp:
        t = _make_tracker(tmp)
        # Record 5 completed actions directly
        for i in range(5):
            t.record_action(f"ACTION_{i}")
        assert t.actions_completed() == 5

        # Reserve 4 in-flight
        for _ in range(4):
            assert t.reserve_slot() is True
        assert t._in_flight == 4
        assert t.actions_remaining() == 0

        # 10th must fail
        assert t.reserve_slot() is False


def test_epoch_reset_clears_in_flight():
    """reset_for_new_epoch clears in-flight counter."""
    with tempfile.TemporaryDirectory() as tmp:
        t = _make_tracker(tmp)
        for _ in range(5):
            t.reserve_slot()
        assert t._in_flight == 5

        t.reset_for_new_epoch("new-epoch-id")
        assert t._in_flight == 0
        assert t.actions_completed() == 0
        assert t.actions_remaining() == 9


def test_snapshot_includes_in_flight():
    """snapshot() exposes in_flight count."""
    with tempfile.TemporaryDirectory() as tmp:
        t = _make_tracker(tmp)
        t.reserve_slot()
        t.reserve_slot()
        snap = t.snapshot()
        assert snap["in_flight"] == 2
        assert snap["remaining"] == 7
        assert snap["completed"] == 0


def test_in_flight_not_persisted():
    """in_flight resets to 0 on reload (simulating crash recovery)."""
    with tempfile.TemporaryDirectory() as tmp:
        t = _make_tracker(tmp)
        for _ in range(5):
            t.reserve_slot()
        assert t._in_flight == 5

        # Create a new tracker from the same storage (simulates restart)
        t2 = _make_tracker(tmp)
        assert t2._in_flight == 0
        assert t2.actions_remaining() == 9


def test_small_required_actions():
    """Works with required_actions < 9."""
    with tempfile.TemporaryDirectory() as tmp:
        t = _make_tracker(tmp, required=3)
        assert t.reserve_slot() is True
        assert t.reserve_slot() is True
        assert t.reserve_slot() is True
        assert t.reserve_slot() is False


if __name__ == "__main__":
    tests = [
        test_reserve_slot_basic,
        test_actions_remaining_includes_in_flight,
        test_release_slot_success_increments_completed,
        test_release_slot_failure_frees_slot,
        test_release_slot_clamps_to_zero,
        test_mixed_reserve_release_respects_limit,
        test_completed_plus_inflight_blocks_reserve,
        test_epoch_reset_clears_in_flight,
        test_snapshot_includes_in_flight,
        test_in_flight_not_persisted,
        test_small_required_actions,
    ]
    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            print(f"  PASS  {test_fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {test_fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
