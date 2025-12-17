"""
Integration tests for on-chain recording guarantee.

These tests verify the FULL flow from decision engine → execution → websocket client
to ensure that recordAction is actually called when actions_recorded < 8 and NOT called
when actions_recorded >= 8.

This complements the unit tests in decision_making.py which only test the decision engine logic.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from typing import Dict, Any, List, Optional
import asyncio

import sys
import os

# Add the olas-sdk-starter/agent directory to the path
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "olas-sdk-starter", "agent")
)

from decision_engine import (
    ActionType,
    PetStats,
    PetContext,
    ActionDecision,
    PetDecisionMaker,
    execute_decision,
)


# ==============================================================================
# Mock WebSocket Client for Testing
# ==============================================================================


class MockWebSocketClient:
    """Mock websocket client that tracks on-chain recording calls."""

    def __init__(self):
        self._onchain_recording_enabled = True
        self._record_action_calls: List[Dict[str, Any]] = []
        self._action_calls: List[Dict[str, Any]] = []
        self._last_action_error: Optional[str] = None

    def set_onchain_recording_enabled(self, enabled: bool) -> None:
        """Set whether on-chain recording is enabled."""
        self._onchain_recording_enabled = enabled

    def get_record_action_calls(self) -> List[Dict[str, Any]]:
        """Get all recordAction calls that were made."""
        return self._record_action_calls.copy()

    def get_action_calls(self) -> List[Dict[str, Any]]:
        """Get all action calls that were made."""
        return self._action_calls.copy()

    def clear_calls(self) -> None:
        """Clear all recorded calls."""
        self._record_action_calls.clear()
        self._action_calls.clear()

    def get_last_action_error(self) -> Optional[str]:
        """Get the last action error."""
        return self._last_action_error

    # Mock action methods that track calls
    async def sleep_pet(self, record_on_chain: Optional[bool] = None) -> bool:
        """Mock sleep action."""
        record = (
            self._onchain_recording_enabled
            if record_on_chain is None
            else bool(record_on_chain)
        )
        self._action_calls.append(
            {
                "action": "SLEEP",
                "record_on_chain": record,
                "explicit": record_on_chain is not None,
            }
        )
        # Simulate on-chain recording if enabled
        if record:
            self._record_action_calls.append({"action": "SLEEP", "recorded": True})
        return True

    async def shower_pet(self, *, record_on_chain: Optional[bool] = None) -> bool:
        """Mock shower action."""
        record = (
            self._onchain_recording_enabled
            if record_on_chain is None
            else bool(record_on_chain)
        )
        self._action_calls.append(
            {
                "action": "SHOWER",
                "record_on_chain": record,
                "explicit": record_on_chain is not None,
            }
        )
        if record:
            self._record_action_calls.append({"action": "SHOWER", "recorded": True})
        return True

    async def rub_pet(self, *, record_on_chain: Optional[bool] = None) -> bool:
        """Mock rub action."""
        record = (
            self._onchain_recording_enabled
            if record_on_chain is None
            else bool(record_on_chain)
        )
        self._action_calls.append(
            {
                "action": "RUB",
                "record_on_chain": record,
                "explicit": record_on_chain is not None,
            }
        )
        if record:
            self._record_action_calls.append({"action": "RUB", "recorded": True})
        return True

    async def throw_ball(self, *, record_on_chain: Optional[bool] = None) -> bool:
        """Mock throwball action."""
        record = (
            self._onchain_recording_enabled
            if record_on_chain is None
            else bool(record_on_chain)
        )
        self._action_calls.append(
            {
                "action": "THROWBALL",
                "record_on_chain": record,
                "explicit": record_on_chain is not None,
            }
        )
        if record:
            self._record_action_calls.append({"action": "THROWBALL", "recorded": True})
        return True

    async def use_consumable(
        self, consumable_id: str, *, record_on_chain: Optional[bool] = None
    ) -> bool:
        """Mock use consumable action."""
        record = (
            self._onchain_recording_enabled
            if record_on_chain is None
            else bool(record_on_chain)
        )
        self._action_calls.append(
            {
                "action": "CONSUMABLES_USE",
                "consumable_id": consumable_id,
                "record_on_chain": record,
                "explicit": record_on_chain is not None,
            }
        )
        if record:
            self._record_action_calls.append(
                {
                    "action": "CONSUMABLES_USE",
                    "consumable_id": consumable_id,
                    "recorded": True,
                }
            )
        return True

    async def buy_consumable(
        self,
        consumable_id: str,
        amount: int,
        *,
        record_on_chain: Optional[bool] = None,
    ) -> bool:
        """Mock buy consumable action."""
        record = (
            self._onchain_recording_enabled
            if record_on_chain is None
            else bool(record_on_chain)
        )
        self._action_calls.append(
            {
                "action": "CONSUMABLES_BUY",
                "consumable_id": consumable_id,
                "amount": amount,
                "record_on_chain": record,
                "explicit": record_on_chain is not None,
            }
        )
        if record:
            self._record_action_calls.append(
                {
                    "action": "CONSUMABLES_BUY",
                    "consumable_id": consumable_id,
                    "amount": amount,
                    "recorded": True,
                }
            )
        return True


# ==============================================================================
# Mock Action Executor for Decision Engine
# ==============================================================================


class MockActionExecutor:
    """Mock executor that implements ActionExecutor protocol."""

    def __init__(self, client: MockWebSocketClient):
        self.client = client

    async def execute_sleep(
        self, record_on_chain: bool, wake_first: bool = False
    ) -> bool:
        """Execute sleep action."""
        return await self.client.sleep_pet(record_on_chain=record_on_chain)

    async def execute_shower(self, record_on_chain: bool) -> bool:
        """Execute shower action."""
        return await self.client.shower_pet(record_on_chain=record_on_chain)

    async def execute_rub(self, record_on_chain: bool) -> bool:
        """Execute rub action."""
        return await self.client.rub_pet(record_on_chain=record_on_chain)

    async def execute_throwball(self, record_on_chain: bool) -> bool:
        """Execute throwball action."""
        return await self.client.throw_ball(record_on_chain=record_on_chain)

    async def execute_use_consumable(
        self, consumable_id: str, record_on_chain: bool
    ) -> bool:
        """Execute use consumable action."""
        return await self.client.use_consumable(
            consumable_id, record_on_chain=record_on_chain
        )

    async def execute_buy_consumable(
        self, consumable_id: str, amount: int, record_on_chain: bool
    ) -> bool:
        """Execute buy consumable action."""
        return await self.client.buy_consumable(
            consumable_id, amount, record_on_chain=record_on_chain
        )


# ==============================================================================
# Helper Functions
# ==============================================================================


def create_context(
    hunger: float = 50.0,
    health: float = 50.0,
    energy: float = 50.0,
    happiness: float = 50.0,
    hygiene: float = 50.0,
    is_sleeping: bool = False,
    is_dead: bool = False,
    token_balance: float = 100.0,
    owned_consumables: List[str] = None,
    actions_recorded: int = 0,
    required_actions: int = 8,
) -> PetContext:
    """Helper to create a PetContext with specified stats."""
    return PetContext(
        stats=PetStats(
            hunger=hunger,
            health=health,
            energy=energy,
            happiness=happiness,
            hygiene=hygiene,
        ),
        is_sleeping=is_sleeping,
        is_dead=is_dead,
        token_balance=token_balance,
        owned_consumables=owned_consumables or [],
        actions_recorded_this_epoch=actions_recorded,
        required_actions_per_epoch=required_actions,
    )


# ==============================================================================
# Integration Tests - Decision Engine Path
# ==============================================================================


class TestDecisionEngineIntegration:
    """Test that decision engine decisions are properly executed with on-chain recording."""

    @pytest.fixture
    def client(self):
        """Create a fresh mock client for each test."""
        return MockWebSocketClient()

    @pytest.fixture
    def executor(self, client):
        """Create an executor using the mock client."""
        return MockActionExecutor(client)

    @pytest.fixture
    def decision_maker(self):
        """Create a fresh decision maker for each test."""
        return PetDecisionMaker()

    @pytest.mark.asyncio
    async def test_first_8_actions_record_onchain(
        self, decision_maker, executor, client
    ):
        """First 8 actions should trigger on-chain recording."""
        for action_num in range(8):
            client.clear_calls()
            context = create_context(
                hunger=80,
                health=80,
                energy=80,
                happiness=80,
                hygiene=30,  # Low hygiene triggers SHOWER
                actions_recorded=action_num,
                required_actions=8,
            )

            decision = decision_maker.decide(context)
            assert (
                decision.should_record_onchain == True
            ), f"Action {action_num+1}/8: should_record_onchain should be True"

            # Execute the decision
            success = await execute_decision(decision, executor)
            assert success == True, f"Action {action_num+1}/8: execution should succeed"

            # Verify on-chain recording was triggered
            record_calls = client.get_record_action_calls()
            assert (
                len(record_calls) == 1
            ), f"Action {action_num+1}/8: Expected 1 recordAction call, got {len(record_calls)}"
            assert (
                record_calls[0]["recorded"] == True
            ), f"Action {action_num+1}/8: recordAction should have been called"

    @pytest.mark.asyncio
    async def test_actions_after_8_do_not_record_onchain(
        self, decision_maker, executor, client
    ):
        """Actions after the 8th should NOT trigger on-chain recording."""
        for action_num in range(8, 12):
            client.clear_calls()
            context = create_context(
                hunger=80,
                health=80,
                energy=80,
                happiness=80,
                hygiene=30,  # Low hygiene triggers SHOWER
                actions_recorded=action_num,
                required_actions=8,
            )

            decision = decision_maker.decide(context)
            assert (
                decision.should_record_onchain == False
            ), f"Action {action_num+1}: should_record_onchain should be False"

            # Execute the decision
            success = await execute_decision(decision, executor)
            assert success == True, f"Action {action_num+1}: execution should succeed"

            # Verify on-chain recording was NOT triggered
            record_calls = client.get_record_action_calls()
            assert (
                len(record_calls) == 0
            ), f"Action {action_num+1}: Expected 0 recordAction calls, got {len(record_calls)}"

    @pytest.mark.asyncio
    async def test_all_permutations_respect_onchain_flag(
        self, decision_maker, executor, client
    ):
        """
        Test ALL 32 binary permutations (0 or 100 for each stat) with and without tokens.

        For each permutation, verify that:
        1. With actions_recorded=0: should_record_onchain=True and recordAction is called
        2. With actions_recorded=8: should_record_onchain=False and recordAction is NOT called
        """
        from itertools import product

        def generate_all_binary_permutations():
            """Generate all 32 permutations of (0, 100) for 5 stats."""
            values = [0, 100]
            return list(product(values, repeat=5))

        def permutation_to_name(perm):
            """Convert a permutation tuple to a readable name."""
            labels = [
                "H",
                "L",
                "E",
                "P",
                "Y",
            ]  # Hunger, heaLth, Energy, haPpiness, hYgiene
            parts = []
            for i, val in enumerate(perm):
                if val == 100:
                    parts.append(f"{labels[i]}100")
                else:
                    parts.append(f"{labels[i]}0")
            return "_".join(parts)

        all_permutations = generate_all_binary_permutations()

        for perm in all_permutations:
            hunger, health, energy, happiness, hygiene = perm
            perm_name = permutation_to_name(perm)

            # Test without tokens (token_balance=0)
            for token_test_name, token_balance in [
                ("no_tokens", 0.0),
                ("with_tokens", 100.0),
            ]:
                full_name = f"{perm_name}_{token_test_name}"

                # Test with actions_recorded=0 (should record)
                client.clear_calls()
                context = create_context(
                    hunger=hunger,
                    health=health,
                    energy=energy,
                    happiness=happiness,
                    hygiene=hygiene,
                    token_balance=token_balance,
                    owned_consumables=[],  # No consumables for predictable testing
                    actions_recorded=0,
                    required_actions=8,
                )

                decision = decision_maker.decide(context)
                assert (
                    decision.action != ActionType.NONE
                ), f"{full_name}: Should choose a valid action (got NONE)"
                assert (
                    decision.should_record_onchain == True
                ), f"{full_name} with actions_recorded=0: should_record_onchain should be True"

                success = await execute_decision(decision, executor)
                assert success == True, f"{full_name}: Execution should succeed"

                record_calls = client.get_record_action_calls()
                assert len(record_calls) == 1, (
                    f"{full_name} ({decision.action.name}) with actions_recorded=0: "
                    f"Expected 1 recordAction call, got {len(record_calls)}"
                )

                # Test with actions_recorded=8 (should NOT record)
                client.clear_calls()
                context = create_context(
                    hunger=hunger,
                    health=health,
                    energy=energy,
                    happiness=happiness,
                    hygiene=hygiene,
                    token_balance=token_balance,
                    owned_consumables=[],
                    actions_recorded=8,
                    required_actions=8,
                )

                decision = decision_maker.decide(context)
                assert (
                    decision.action != ActionType.NONE
                ), f"{full_name}: Should choose a valid action (got NONE)"
                assert (
                    decision.should_record_onchain == False
                ), f"{full_name} with actions_recorded=8: should_record_onchain should be False"

                success = await execute_decision(decision, executor)
                assert success == True, f"{full_name}: Execution should succeed"

                record_calls = client.get_record_action_calls()
                assert len(record_calls) == 0, (
                    f"{full_name} ({decision.action.name}) with actions_recorded=8: "
                    f"Expected 0 recordAction calls, got {len(record_calls)}"
                )

    @pytest.mark.asyncio
    async def test_8_action_sequence_all_recorded(
        self, decision_maker, executor, client
    ):
        """Run 8 actions in sequence - all should be recorded on-chain."""
        client.clear_calls()
        all_recorded = []

        for action_num in range(8):
            context = create_context(
                hunger=80,
                health=80,
                energy=80,
                happiness=80,
                hygiene=30,  # Low hygiene triggers SHOWER
                actions_recorded=action_num,
                required_actions=8,
            )

            decision = decision_maker.decide(context)
            assert (
                decision.should_record_onchain == True
            ), f"Action {action_num+1}/8: should_record_onchain should be True"

            success = await execute_decision(decision, executor)
            assert success == True

            all_recorded.append(decision.action)

        # Verify all 8 actions were recorded
        record_calls = client.get_record_action_calls()
        assert (
            len(record_calls) == 8
        ), f"Expected 8 recordAction calls for 8 actions, got {len(record_calls)}"

        # Verify all calls were for recording
        for i, call_data in enumerate(record_calls):
            assert (
                call_data["recorded"] == True
            ), f"Record call {i+1}/8: should have recorded=True"


# ==============================================================================
# Integration Tests - Direct Execution Path (simulating pett_agent behavior)
# ==============================================================================


class TestDirectExecutionPath:
    """
    Test the direct execution path that uses _execute_action_with_tracking.

    This path doesn't use the decision engine but relies on the websocket client's
    _onchain_recording_enabled flag which is set based on actions_remaining > 0.
    """

    @pytest.fixture
    def client(self):
        """Create a fresh mock client for each test."""
        return MockWebSocketClient()

    @pytest.mark.asyncio
    async def test_direct_execution_respects_onchain_flag(self, client):
        """Direct execution should respect the onchain_recording_enabled flag."""
        # Simulate actions_remaining > 0 (should record)
        client.set_onchain_recording_enabled(True)
        client.clear_calls()

        # Execute actions directly (simulating _execute_action_with_tracking path)
        await client.sleep_pet()
        await client.shower_pet()
        await client.rub_pet()
        await client.throw_ball()

        # All should have triggered on-chain recording
        record_calls = client.get_record_action_calls()
        assert (
            len(record_calls) == 4
        ), f"Expected 4 recordAction calls, got {len(record_calls)}"

        # Simulate actions_remaining == 0 (should NOT record)
        client.set_onchain_recording_enabled(False)
        client.clear_calls()

        await client.sleep_pet()
        await client.shower_pet()
        await client.rub_pet()
        await client.throw_ball()

        # None should have triggered on-chain recording
        record_calls = client.get_record_action_calls()
        assert (
            len(record_calls) == 0
        ), f"Expected 0 recordAction calls when disabled, got {len(record_calls)}"

    @pytest.mark.asyncio
    async def test_explicit_record_onchain_overrides_flag(self, client):
        """Explicit record_on_chain parameter should override the flag."""
        # Set flag to False (simulating actions_remaining == 0)
        client.set_onchain_recording_enabled(False)
        client.clear_calls()

        # But explicitly request on-chain recording
        await client.sleep_pet(record_on_chain=True)
        await client.shower_pet(record_on_chain=True)

        # Should still record because explicit parameter overrides
        record_calls = client.get_record_action_calls()
        assert (
            len(record_calls) == 2
        ), f"Expected 2 recordAction calls with explicit=True, got {len(record_calls)}"

        # Now test explicit False
        client.clear_calls()
        client.set_onchain_recording_enabled(True)  # Flag says record

        await client.sleep_pet(record_on_chain=False)
        await client.shower_pet(record_on_chain=False)

        # Should NOT record because explicit parameter says False
        record_calls = client.get_record_action_calls()
        assert (
            len(record_calls) == 0
        ), f"Expected 0 recordAction calls with explicit=False, got {len(record_calls)}"


# ==============================================================================
# Integration Tests - Full Flow Simulation
# ==============================================================================


class TestFullFlowIntegration:
    """
    Test the complete flow simulating how pett_agent actually works:
    1. Check actions_remaining
    2. Set onchain_recording_enabled on client
    3. Execute actions (either via decision engine or directly)
    4. Verify recordAction is called appropriately
    """

    @pytest.fixture
    def client(self):
        """Create a fresh mock client for each test."""
        return MockWebSocketClient()

    @pytest.fixture
    def executor(self, client):
        """Create an executor using the mock client."""
        return MockActionExecutor(client)

    @pytest.fixture
    def decision_maker(self):
        """Create a fresh decision maker for each test."""
        return PetDecisionMaker()

    def simulate_set_onchain_recording(
        self,
        client: MockWebSocketClient,
        actions_recorded: int,
        required_actions: int = 8,
    ):
        """Simulate how pett_agent sets onchain_recording_enabled."""
        actions_remaining = required_actions - actions_recorded
        should_record = actions_remaining > 0
        client.set_onchain_recording_enabled(should_record)
        return should_record

    @pytest.mark.asyncio
    async def test_full_flow_first_8_actions(self, decision_maker, executor, client):
        """Full flow: first 8 actions should all be recorded."""
        client.clear_calls()
        recorded_count = 0

        for action_num in range(8):
            # Simulate how pett_agent sets the flag
            should_record = self.simulate_set_onchain_recording(client, action_num)
            assert (
                should_record == True
            ), f"Action {action_num+1}/8: should_record should be True"

            # Create context and decide
            context = create_context(
                hunger=80,
                health=80,
                energy=80,
                happiness=80,
                hygiene=30,
                actions_recorded=action_num,
                required_actions=8,
            )

            decision = decision_maker.decide(context)
            assert decision.should_record_onchain == True

            # Execute via decision engine
            success = await execute_decision(decision, executor)
            assert success == True

            # Count recorded actions
            record_calls = client.get_record_action_calls()
            recorded_count = len(record_calls)
            assert recorded_count == action_num + 1, (
                f"After action {action_num+1}/8: Expected {action_num+1} recorded actions, "
                f"got {recorded_count}"
            )

        # Final verification: all 8 should be recorded
        assert (
            recorded_count == 8
        ), f"Expected 8 total recorded actions, got {recorded_count}"

    @pytest.mark.asyncio
    async def test_full_flow_actions_after_8(self, decision_maker, executor, client):
        """Full flow: actions after 8 should NOT be recorded."""
        client.clear_calls()

        for action_num in range(8, 12):
            # Simulate how pett_agent sets the flag
            should_record = self.simulate_set_onchain_recording(client, action_num)
            assert (
                should_record == False
            ), f"Action {action_num+1}: should_record should be False"

            # Create context and decide
            context = create_context(
                hunger=80,
                health=80,
                energy=80,
                happiness=80,
                hygiene=30,
                actions_recorded=action_num,
                required_actions=8,
            )

            decision = decision_maker.decide(context)
            assert decision.should_record_onchain == False

            # Execute via decision engine
            success = await execute_decision(decision, executor)
            assert success == True

            # Verify no new recordings
            record_calls = client.get_record_action_calls()
            assert (
                len(record_calls) == 0
            ), f"Action {action_num+1}: Expected 0 recorded actions, got {len(record_calls)}"

    @pytest.mark.asyncio
    async def test_full_flow_mixed_paths(self, decision_maker, executor, client):
        """
        Test that both decision engine path and direct execution path
        respect the onchain_recording_enabled flag.
        """
        client.clear_calls()

        # First 4 actions via decision engine
        for action_num in range(4):
            should_record = self.simulate_set_onchain_recording(client, action_num)
            context = create_context(
                hygiene=30, actions_recorded=action_num, required_actions=8
            )
            decision = decision_maker.decide(context)
            await execute_decision(decision, executor)

        # Next 2 actions via direct execution (simulating _execute_action_with_tracking)
        for action_num in range(4, 6):
            should_record = self.simulate_set_onchain_recording(client, action_num)
            await client.shower_pet()  # Direct call, uses _onchain_recording_enabled

        # Last 2 actions via decision engine again
        for action_num in range(6, 8):
            should_record = self.simulate_set_onchain_recording(client, action_num)
            context = create_context(
                hygiene=30, actions_recorded=action_num, required_actions=8
            )
            decision = decision_maker.decide(context)
            await execute_decision(decision, executor)

        # All 8 should be recorded
        record_calls = client.get_record_action_calls()
        assert (
            len(record_calls) == 8
        ), f"Expected 8 recorded actions across mixed paths, got {len(record_calls)}"

        # Action 9 should NOT be recorded
        should_record = self.simulate_set_onchain_recording(client, 8)
        await client.shower_pet()
        record_calls = client.get_record_action_calls()
        assert (
            len(record_calls) == 8
        ), f"Action 9 should not be recorded, but got {len(record_calls)} total"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
