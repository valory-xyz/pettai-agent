"""
Tests for the health recovery race condition fix and low-stats pet handling.

Verifies that:
1. The error handler sets _pending_health_recovery flag instead of calling
   _recover_low_health() directly (no concurrent actions)
2. The main action loop checks health recovery FIRST on failure
3. _last_action_failed_due_to_low_health() detects both the flag and error text
4. Health recovery is attempted before low-balance fallback
5. After successful health recovery, the original action is retried
6. Fallback order is RUB -> THROWBALL -> SLEEP (RUB first, not THROWBALL)
7. Low-stats pets don't get stuck in failing THROWBALL loops
"""

import asyncio
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

# Add the agent module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "olas-sdk-starter"))

AGENT_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "olas-sdk-starter",
    "agent",
    "pett_agent.py",
)


def _read_source():
    with open(AGENT_PATH, "r") as f:
        return f.read()


def _extract_method(source, method_sig):
    """Extract a method body from source by its signature."""
    start = source.find(method_sig)
    assert start != -1, f"{method_sig} not found"
    # Find next top-level method (4-space indent)
    next_async = source.find("\n    async def ", start + 1)
    next_sync = source.find("\n    def ", start + 1)
    candidates = [c for c in (next_async, next_sync) if c != -1]
    end = min(candidates) if candidates else len(source)
    return source[start:end]


class TestHealthRecoveryFlag(unittest.TestCase):
    """Test that the error handler sets a flag instead of calling _recover_low_health."""

    def test_error_handler_does_not_call_recover_low_health(self):
        """Verify _on_client_error_message sets flag instead of awaiting recovery."""
        handler_body = _extract_method(
            _read_source(), "async def _on_client_error_message"
        )

        assert (
            "await self._recover_low_health()" not in handler_body
        ), "Error handler still calls _recover_low_health() directly - race condition not fixed!"

        assert (
            "self._pending_health_recovery = True" in handler_body
        ), "Error handler does not set _pending_health_recovery flag"

    def test_pending_health_recovery_flag_initialized(self):
        """Verify the flag is initialized in __init__."""
        source = _read_source()
        assert (
            "self._pending_health_recovery: bool = False" in source
            or "self._pending_health_recovery = False" in source
        ), "_pending_health_recovery not initialized to False"


class TestLowHealthDetection(unittest.TestCase):
    """Test _last_action_failed_due_to_low_health detection."""

    def test_detects_pending_flag(self):
        """Should check _pending_health_recovery flag."""
        method_body = _extract_method(
            _read_source(), "def _last_action_failed_due_to_low_health"
        )
        assert (
            "_pending_health_recovery" in method_body
        ), "Method does not check _pending_health_recovery flag"

    def test_detects_health_error_text(self):
        """Should check for health-related error text."""
        method_body = _extract_method(
            _read_source(), "def _last_action_failed_due_to_low_health"
        )
        assert (
            "not enough health" in method_body
            or "not have enough health" in method_body
        ), "Method does not check for health error text"

    def test_checks_websocket_error(self):
        """Should query websocket client's last action error."""
        method_body = _extract_method(
            _read_source(), "def _last_action_failed_due_to_low_health"
        )
        assert (
            "get_last_action_error" in method_body
        ), "Method does not check websocket client's last action error"


class TestMainLoopHealthRecoveryPriority(unittest.TestCase):
    """Test that health recovery is first priority in the main action loop."""

    def test_health_recovery_before_low_balance_fallback(self):
        """Health recovery should come BEFORE low-balance fallback in the main loop."""
        source = _read_source()

        # Find after first _execute_decision call in the main loop
        execute_pos = source.find("await self._execute_decision(decision)")
        assert execute_pos != -1
        after_decision = source[execute_pos:]

        health_pos = after_decision.find("_last_action_failed_due_to_low_health")
        fallback_pos = after_decision.find("_execute_low_balance_fallback_action")

        assert health_pos != -1, "Health check not found after _execute_decision"
        assert fallback_pos != -1, "Fallback not found after _execute_decision"
        assert health_pos < fallback_pos, (
            f"Health recovery (offset {health_pos}) must come BEFORE "
            f"low-balance fallback (offset {fallback_pos})"
        )

    def test_health_recovery_retries_original_action(self):
        """After health recovery, the original action should be retried."""
        source = _read_source()
        health_pos = source.find("_last_action_failed_due_to_low_health")
        after_health = source[health_pos : health_pos + 1500]

        assert "_recover_low_health" in after_health, (
            "_recover_low_health not called after health detection"
        )

        recover_pos = after_health.find("_recover_low_health")
        after_recover = after_health[recover_pos:]
        assert "_execute_decision" in after_recover, (
            "Original action not retried after health recovery"
        )

    def test_pending_flag_cleared_before_recovery(self):
        """The flag should be cleared before recovery to prevent re-entry."""
        source = _read_source()
        health_pos = source.find("_last_action_failed_due_to_low_health")
        after_health = source[health_pos : health_pos + 1500]

        assert "_pending_health_recovery = False" in after_health, (
            "Flag not cleared before recovery"
        )

        clear_pos = after_health.find("_pending_health_recovery = False")
        recover_pos = after_health.find("_recover_low_health")
        assert clear_pos < recover_pos, (
            "Flag must be cleared BEFORE calling _recover_low_health"
        )


class TestNoRaceCondition(unittest.TestCase):
    """Test that there's no race condition between error handler and main loop."""

    def test_error_handler_is_non_blocking(self):
        """The error handler must not await any action-sending methods."""
        handler_body = _extract_method(
            _read_source(), "async def _on_client_error_message"
        )

        action_methods = [
            "use_consumable",
            "buy_consumable",
            "throw_ball",
            "rub_pet",
            "sleep_pet",
            "_execute_action_with_tracking",
            "_execute_decision",
            "_execute_low_balance_fallback_action",
            "_recover_low_health",
        ]
        for method in action_methods:
            assert f"await self.{method}" not in handler_body and \
                   f"await self.websocket_client.{method}" not in handler_body, (
                f"Error handler awaits {method} - this causes race conditions!"
            )

    def test_no_send_and_wait_in_error_handler(self):
        """Error handler must not trigger any websocket sends."""
        handler_body = _extract_method(
            _read_source(), "async def _on_client_error_message"
        )
        assert "send_and_wait" not in handler_body, (
            "Error handler should not send messages"
        )
        assert "_execute_action_with_tracking" not in handler_body, (
            "Error handler should not execute actions"
        )


class TestRecoverLowHealthMethod(unittest.TestCase):
    """Test _recover_low_health method structure."""

    def test_recover_low_health_exists(self):
        """The method should still exist for the main loop to call."""
        assert "async def _recover_low_health" in _read_source()

    def test_recover_low_health_has_guard(self):
        """The method should have the _low_health_recovery_in_progress guard."""
        method_body = _extract_method(
            _read_source(), "async def _recover_low_health"
        )
        assert "_low_health_recovery_in_progress" in method_body

    def test_recover_low_health_tries_salad_first(self):
        """Health recovery should try SALAD before POTION."""
        method_body = _extract_method(
            _read_source(), "async def _recover_low_health"
        )
        salad_pos = method_body.find("SALAD")
        potion_pos = method_body.find("SMALL_POTION")
        assert salad_pos != -1, "SALAD not found in _recover_low_health"
        assert potion_pos != -1, "SMALL_POTION not found in _recover_low_health"
        assert salad_pos < potion_pos, "SALAD should be tried before SMALL_POTION"

    def test_recover_low_health_buys_if_not_owned(self):
        """Health recovery should buy consumables if not owned."""
        method_body = _extract_method(
            _read_source(), "async def _recover_low_health"
        )
        assert "buy_consumable" in method_body, (
            "_recover_low_health should buy consumables if not owned"
        )


class TestFallbackOrder(unittest.TestCase):
    """Test that fallback action order is optimal for low-stats pets."""

    def test_rub_before_throwball(self):
        """RUB should come before THROWBALL in fallback (no stat requirements)."""
        method_body = _extract_method(
            _read_source(), "async def _execute_low_balance_fallback_action"
        )

        # Find first occurrence of each in the fallback list
        rub_pos = method_body.find('"RUB"')
        throwball_pos = method_body.find('"THROWBALL"')
        sleep_pos = method_body.find('"SLEEP"')

        assert rub_pos != -1, "RUB not found in fallback actions"
        assert throwball_pos != -1, "THROWBALL not found in fallback actions"
        assert sleep_pos != -1, "SLEEP not found in fallback actions"

        assert rub_pos < throwball_pos, (
            f"RUB (pos {rub_pos}) must come BEFORE THROWBALL (pos {throwball_pos}) "
            "because THROWBALL requires stats >= 15 and fails on low-stats pets"
        )
        assert throwball_pos < sleep_pos, (
            f"THROWBALL (pos {throwball_pos}) should come before SLEEP (pos {sleep_pos})"
        )

    def test_fallback_does_not_treat_already_clean_as_success(self):
        """Fallback must not accept 'already clean' as success (no on-chain tx)."""
        method_body = _extract_method(
            _read_source(), "async def _execute_low_balance_fallback_action"
        )
        assert "treat_already_clean_as_success=False" in method_body, (
            "Fallback must pass treat_already_clean_as_success=False"
        )

    def test_fallback_always_has_sleep_as_last_resort(self):
        """SLEEP should always be the last fallback (always succeeds)."""
        method_body = _extract_method(
            _read_source(), "async def _execute_low_balance_fallback_action"
        )
        # SLEEP should be the last action in the list
        last_action_pos = max(
            method_body.find('"RUB"'),
            method_body.find('"THROWBALL"'),
            method_body.find('"SLEEP"'),
        )
        assert last_action_pos == method_body.find('"SLEEP"'), (
            "SLEEP should be the LAST fallback action (it always succeeds)"
        )


class TestLowStatsPetScenarios(unittest.TestCase):
    """Test scenarios specific to pets with very low stats."""

    def test_consumable_use_failure_detected_by_main_loop(self):
        """When CONSUMABLES_USE fails due to low health, main loop should detect it."""
        source = _read_source()
        # In _execute_decision, CONSUMABLES_USE failure returns False
        # Main loop then checks _last_action_failed_due_to_low_health
        decision_method = _extract_method(source, "async def _execute_decision")

        # CONSUMABLES_USE path should return False on failure (not just token check)
        consumables_section = decision_method[
            decision_method.find("ActionType.CONSUMABLES_USE") :
        ]
        # After insufficient tokens check, there should be a return False
        token_check = consumables_section.find(
            "_last_action_failed_due_to_insufficient_tokens"
        )
        assert token_check != -1, (
            "CONSUMABLES_USE path should check for insufficient tokens"
        )
        # After the token check block, should return False for other errors
        after_token = consumables_section[token_check:]
        assert "return False" in after_token, (
            "CONSUMABLES_USE should return False for non-token failures "
            "(main loop health check will handle it)"
        )

    def test_throwball_min_stat_exists_in_decision_engine(self):
        """Decision engine should have a minimum stat check for THROWBALL."""
        engine_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "olas-sdk-starter",
            "agent",
            "decision_engine.py",
        )
        with open(engine_path, "r") as f:
            engine_source = f.read()

        assert "THROWBALL_MIN_STAT" in engine_source, (
            "Decision engine should define THROWBALL_MIN_STAT"
        )
        assert "can_throwball" in engine_source, (
            "Decision engine should have can_throwball check"
        )

    def test_health_recovery_uses_execute_action_with_tracking(self):
        """Health recovery should use _execute_action_with_tracking for proper sequencing."""
        method_body = _extract_method(
            _read_source(), "async def _recover_low_health"
        )
        assert "_execute_action_with_tracking" in method_body, (
            "_recover_low_health should use _execute_action_with_tracking "
            "to ensure proper sequencing and on-chain recording"
        )


class TestDecisionEngineSleepingWithCriticalStats(unittest.TestCase):
    """Test that sleeping pets with critical stats don't stay asleep."""

    def test_sleeping_management_checks_other_stats(self):
        """Sleeping management should NOT short-circuit when other stats are critical."""
        engine_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "olas-sdk-starter",
            "agent",
            "decision_engine.py",
        )
        with open(engine_path, "r") as f:
            source = f.read()

        # Find the sleeping management section (the actual code, not docstring)
        sleep_mgmt = source.find("# --- Sleeping management ---")
        assert sleep_mgmt != -1

        # Find the survival check section
        survival_check = source.find("Critical survival check", sleep_mgmt)
        assert survival_check != -1

        between = source[sleep_mgmt:survival_check]

        assert "health" in between.lower(), (
            "Sleeping management must check health before defaulting to SLEEP"
        )
        assert "hunger" in between.lower(), (
            "Sleeping management must check hunger before defaulting to SLEEP"
        )
        assert "critical_threshold" in between.lower(), (
            "Sleeping management must compare stats against CRITICAL_THRESHOLD"
        )

    def test_sleeping_with_zero_stats_falls_through_to_survival(self):
        """A sleeping pet with all stats at 0 must NOT get SLEEP decision."""
        engine_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "olas-sdk-starter",
            "agent",
            "decision_engine.py",
        )
        with open(engine_path, "r") as f:
            source = f.read()

        sleep_mgmt_start = source.find("# --- Sleeping management ---")
        survival_start = source.find("Critical survival check")
        sleep_section = source[sleep_mgmt_start:survival_start]

        returns_in_sleep = sleep_section.count("return self._finalize")
        stat_guards = sleep_section.count("_other_stats_ok") or \
                      sleep_section.count("CRITICAL_THRESHOLD")

        assert stat_guards >= returns_in_sleep, (
            f"Found {returns_in_sleep} return(s) in sleeping management but only "
            f"{stat_guards} stat guard(s). Every SLEEP return must be guarded "
            "so critical pets fall through to survival mode"
        )


class TestSurvivalModePriority(unittest.TestCase):
    """Test survival mode checks health/hunger before energy."""

    def _read_engine(self):
        engine_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "olas-sdk-starter",
            "agent",
            "decision_engine.py",
        )
        with open(engine_path, "r") as f:
            return f.read()

    def test_health_checked_before_energy(self):
        """Health must be checked before energy in survival mode."""
        source = self._read_engine()
        survival_start = source.find("def _check_survival")
        assert survival_start != -1
        survival_body = source[survival_start:]

        health_pos = survival_body.find("Health critical")
        energy_pos = survival_body.find("Energy critical")
        assert health_pos != -1, "Health critical check not found in survival"
        assert energy_pos != -1, "Energy critical check not found in survival"
        assert health_pos < energy_pos, (
            "Health must be checked BEFORE energy in survival mode "
            "(health doesn't recover passively, energy does via sleep)"
        )

    def test_hunger_checked_before_energy(self):
        """Hunger must be checked before energy in survival mode."""
        source = self._read_engine()
        survival_start = source.find("def _check_survival")
        survival_body = source[survival_start:]

        hunger_pos = survival_body.find("Hunger critical")
        energy_pos = survival_body.find("Energy critical")
        assert hunger_pos != -1, "Hunger critical check not found"
        assert energy_pos != -1, "Energy critical check not found"
        assert hunger_pos < energy_pos, (
            "Hunger must be checked BEFORE energy in survival mode"
        )

    def test_health_always_returns_action(self):
        """When health is critical, survival must ALWAYS return an action (never fall through)."""
        source = self._read_engine()
        survival_start = source.find("def _check_survival")
        survival_body = source[survival_start:]

        # Extract the health critical section
        health_start = survival_body.find("Health critical")
        hunger_start = survival_body.find("Hunger critical")
        health_section = survival_body[health_start:hunger_start]

        # The health section must have an unconditional return (auto-buy)
        # not guarded by can_buy
        returns = health_section.count("return self._finalize")
        assert returns >= 2, (
            f"Health critical section has {returns} return(s), needs at least 2: "
            "one for owned potion, one for auto-buy (unconditional)"
        )

    def test_hunger_always_returns_action(self):
        """When hunger is critical, survival must ALWAYS return an action."""
        source = self._read_engine()
        survival_start = source.find("def _check_survival")
        survival_body = source[survival_start:]

        hunger_start = survival_body.find("Hunger critical")
        energy_start = survival_body.find("Energy critical")
        hunger_section = survival_body[hunger_start:energy_start]

        returns = hunger_section.count("return self._finalize")
        assert returns >= 2, (
            f"Hunger critical section has {returns} return(s), needs at least 2: "
            "one for owned food, one for auto-buy (unconditional)"
        )


class TestActionSequencing(unittest.TestCase):
    """Test that actions are always sent one at a time."""

    def test_main_loop_awaits_each_step(self):
        """Each step in the main loop should be awaited (not fire-and-forget)."""
        source = _read_source()
        # Find the action execution section in _pet_action_loop
        execute_pos = source.find("await self._execute_decision(decision)")
        assert execute_pos != -1

        section = source[execute_pos : execute_pos + 3000]

        # Health recovery should be awaited
        assert "await self._recover_low_health()" in section, (
            "_recover_low_health must be awaited in main loop"
        )

        # Fallback should be awaited
        assert "await self._execute_low_balance_fallback_action" in section, (
            "Fallback must be awaited in main loop"
        )

    def test_no_create_task_for_actions(self):
        """Actions should never be launched as background tasks (create_task)."""
        source = _read_source()
        # Check that _recover_low_health is never launched as a task
        assert "create_task(self._recover_low_health" not in source, (
            "_recover_low_health should never be launched as a background task"
        )
        assert "create_task(self._execute_low_balance_fallback" not in source, (
            "Fallback should never be launched as a background task"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
