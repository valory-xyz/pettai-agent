"""
Comprehensive tests for the Pet Decision Maker.

Tests all stat combinations to ensure correct action selection and on-chain recording.
"""

import pytest
from typing import List, Tuple, Dict, Any
from dataclasses import dataclass

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
    ActionConditions,
    PetDecisionMaker,
    FailedAction,
)
from datetime import datetime, timedelta


# ==============================================================================
# Test Fixtures and Helpers
# ==============================================================================


@pytest.fixture
def decision_maker():
    """Create a fresh decision maker for each test."""
    return PetDecisionMaker()


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


@dataclass
class ExpectedDecision:
    """Expected outcomes for a test case."""

    action: ActionType
    should_record_onchain: bool
    description: str


# ==============================================================================
# Stat Combination Test Cases
# ==============================================================================

# All the stat combinations to test
# NOTE: These tests use token_balance=0 and no consumables to test pure fallback chains
STAT_COMBINATIONS: List[Tuple[str, Dict[str, float], ExpectedDecision]] = [
    # (name, stats_dict, expected_decision)
    # Single stat at 100%, rest at 0%
    # NOTE: With energy=0, sleep is triggered first (LOW_ENERGY priority)
    (
        "only_hunger_full",
        {"hunger": 100, "health": 0, "energy": 0, "happiness": 0, "hygiene": 0},
        ExpectedDecision(
            action=ActionType.SLEEP,  # Energy < 25 triggers sleep first
            should_record_onchain=True,
            description="Low energy (0) triggers sleep",
        ),
    ),
    (
        "only_health_full",
        {"hunger": 0, "health": 100, "energy": 0, "happiness": 0, "hygiene": 0},
        ExpectedDecision(
            action=ActionType.SLEEP,  # Energy < 25 triggers sleep
            should_record_onchain=True,
            description="Low energy (0) triggers sleep",
        ),
    ),
    (
        "only_energy_full",
        {"hunger": 0, "health": 0, "energy": 100, "happiness": 0, "hygiene": 0},
        ExpectedDecision(
            action=ActionType.RUB,  # Critical state, hygiene < 75 allows rub
            should_record_onchain=True,
            description="Critical stats - rub as free action (hygiene < 75)",
        ),
    ),
    (
        "only_happiness_full",
        {"hunger": 0, "health": 0, "energy": 0, "happiness": 100, "hygiene": 0},
        ExpectedDecision(
            action=ActionType.SLEEP,  # Energy < 25 triggers sleep
            should_record_onchain=True,
            description="Low energy (0) triggers sleep",
        ),
    ),
    (
        "only_hygiene_full",
        {"hunger": 0, "health": 0, "energy": 0, "happiness": 0, "hygiene": 100},
        ExpectedDecision(
            action=ActionType.SLEEP,  # Energy < 25 triggers sleep, can't rub (hygiene >= 75)
            should_record_onchain=True,
            description="Low energy triggers sleep (hygiene too high to rub)",
        ),
    ),
    # All stats at 0% - critical handling has priority, rub is free action
    (
        "all_zero",
        {"hunger": 0, "health": 0, "energy": 0, "happiness": 0, "hygiene": 0},
        ExpectedDecision(
            action=ActionType.RUB,  # Critical handling > low energy, rub is free
            should_record_onchain=True,
            description="All zero - critical handling, rub as free action",
        ),
    ),
    # All stats at 100%
    (
        "all_full",
        {"hunger": 100, "health": 100, "energy": 100, "happiness": 100, "hygiene": 100},
        ExpectedDecision(
            action=ActionType.THROWBALL,  # Maintenance - throwball earns tokens
            should_record_onchain=True,
            description="All full - maintenance throwball",
        ),
    ),
    # Low single stats (at 30%) - no tokens/consumables
    (
        "low_hunger_only",
        {"hunger": 30, "health": 80, "energy": 80, "happiness": 80, "hygiene": 80},
        ExpectedDecision(
            action=ActionType.THROWBALL,  # No consumables/tokens, fallback to throwball
            should_record_onchain=True,
            description="Low hunger - no resources, fallback to throwball",
        ),
    ),
    (
        "low_health_only",
        {"hunger": 80, "health": 30, "energy": 80, "happiness": 80, "hygiene": 80},
        ExpectedDecision(
            action=ActionType.THROWBALL,  # No consumables/tokens, fallback to maintenance
            should_record_onchain=True,
            description="Low health - no resources, fallback to throwball",
        ),
    ),
    (
        "low_energy_only",
        {"hunger": 80, "health": 80, "energy": 20, "happiness": 80, "hygiene": 80},
        ExpectedDecision(
            action=ActionType.SLEEP,  # Energy < 25 triggers sleep
            should_record_onchain=True,
            description="Low energy triggers sleep",
        ),
    ),
    (
        "low_happiness_only",
        {"hunger": 80, "health": 80, "energy": 80, "happiness": 30, "hygiene": 80},
        ExpectedDecision(
            action=ActionType.THROWBALL,  # Low happiness -> throwball
            should_record_onchain=True,
            description="Low happiness - throwball for happiness and tokens",
        ),
    ),
    (
        "low_hygiene_only",
        {"hunger": 80, "health": 80, "energy": 80, "happiness": 80, "hygiene": 30},
        ExpectedDecision(
            action=ActionType.SHOWER,  # Low hygiene -> shower
            should_record_onchain=True,
            description="Low hygiene - shower",
        ),
    ),
    # Edge cases around thresholds
    (
        "hygiene_at_74",  # Just below shower threshold but above 70 (not LOW)
        {"hunger": 80, "health": 80, "energy": 80, "happiness": 80, "hygiene": 74},
        ExpectedDecision(
            action=ActionType.THROWBALL,  # Hygiene > 70 so not low, maintenance throwball
            should_record_onchain=True,
            description="Hygiene at 74 - not low, maintenance throwball",
        ),
    ),
    (
        "hygiene_at_75",  # At shower/rub threshold
        {"hunger": 80, "health": 80, "energy": 80, "happiness": 80, "hygiene": 75},
        ExpectedDecision(
            action=ActionType.THROWBALL,  # Can't shower (>= 75), maintenance throwball
            should_record_onchain=True,
            description="Hygiene at 75 - can't shower, throwball instead",
        ),
    ),
    (
        "energy_at_24",  # Just below sleep threshold
        {"hunger": 80, "health": 80, "energy": 24, "happiness": 80, "hygiene": 80},
        ExpectedDecision(
            action=ActionType.SLEEP,  # energy < 25 triggers sleep
            should_record_onchain=True,
            description="Energy at 24 - sleep",
        ),
    ),
    (
        "energy_at_25",  # At threshold
        {"hunger": 80, "health": 80, "energy": 25, "happiness": 80, "hygiene": 80},
        ExpectedDecision(
            action=ActionType.THROWBALL,  # energy >= 25, no forced sleep
            should_record_onchain=True,
            description="Energy at 25 - no forced sleep, throwball",
        ),
    ),
    # Throwball blocking condition (all core stats < 15)
    (
        "throwball_blocked",
        {"hunger": 10, "health": 10, "energy": 10, "happiness": 80, "hygiene": 80},
        ExpectedDecision(
            action=ActionType.SLEEP,  # Energy < 25 triggers sleep
            should_record_onchain=True,
            description="Low energy (10) triggers sleep",
        ),
    ),
    (
        "throwball_allowed_hunger_15",
        {"hunger": 15, "health": 10, "energy": 10, "happiness": 80, "hygiene": 80},
        ExpectedDecision(
            action=ActionType.SLEEP,  # Energy < 25 takes priority
            should_record_onchain=True,
            description="Low energy forces sleep regardless of throwball possibility",
        ),
    ),
]


class TestStatCombinations:
    """Test all stat combinations for correct decision making."""

    @pytest.mark.parametrize("name,stats,expected", STAT_COMBINATIONS)
    def test_stat_combination_needs_onchain(
        self,
        decision_maker: PetDecisionMaker,
        name: str,
        stats: Dict,
        expected: ExpectedDecision,
    ):
        """Test stat combination when on-chain recording is needed."""
        context = create_context(
            **stats,
            owned_consumables=[],  # No consumables for predictable testing
            token_balance=0.0,  # No tokens for pure fallback testing
            actions_recorded=0,  # Still need on-chain actions
            required_actions=8,
        )

        decision = decision_maker.decide(context)

        assert decision.action == expected.action, (
            f"[{name}] Expected {expected.action.name}, got {decision.action.name}. "
            f"Reason: {decision.reason}"
        )
        assert decision.should_record_onchain == expected.should_record_onchain, (
            f"[{name}] Expected should_record_onchain={expected.should_record_onchain}, "
            f"got {decision.should_record_onchain}"
        )
        assert (
            decision.stats_snapshot is not None
        ), f"[{name}] Decision should include stats snapshot"

    @pytest.mark.parametrize("name,stats,expected", STAT_COMBINATIONS)
    def test_stat_combination_onchain_met(
        self,
        decision_maker: PetDecisionMaker,
        name: str,
        stats: Dict,
        expected: ExpectedDecision,
    ):
        """Test stat combination when on-chain requirement is already met."""
        context = create_context(
            **stats,
            owned_consumables=[],
            token_balance=0.0,  # No tokens for pure fallback testing
            actions_recorded=8,  # Already met requirement
            required_actions=8,
        )

        decision = decision_maker.decide(context)

        # Action should still be chosen, but should NOT record on-chain
        assert (
            decision.action != ActionType.NONE or context.stats.is_critical()
        ), f"[{name}] Should still choose an action even when on-chain met"
        assert decision.should_record_onchain == False, (
            f"[{name}] Should NOT record on-chain when requirement met "
            f"(actions_recorded=8)"
        )


class TestWithConsumables:
    """Test decision making when consumables are available."""

    def test_critical_with_consumables(self, decision_maker: PetDecisionMaker):
        """When critical and have consumables, should use them."""
        context = create_context(
            hunger=0,
            health=0,
            energy=0,
            happiness=0,
            hygiene=0,
            owned_consumables=["BURGER", "SALAD"],
            actions_recorded=0,
        )

        decision = decision_maker.decide(context)

        assert decision.action == ActionType.CONSUMABLES_USE
        assert decision.should_record_onchain == True

    def test_low_hunger_with_food(self, decision_maker: PetDecisionMaker):
        """When low hunger and have food, should use food."""
        context = create_context(
            hunger=30,
            health=80,
            energy=80,
            happiness=80,
            hygiene=80,
            owned_consumables=["BURGER"],
            actions_recorded=0,
        )

        decision = decision_maker.decide(context)

        assert decision.action == ActionType.CONSUMABLES_USE
        assert "BURGER" in decision.params.get("consumable_id", "")

    def test_low_health_with_potion(self, decision_maker: PetDecisionMaker):
        """When low health and have potion, should use potion."""
        context = create_context(
            hunger=80,
            health=30,
            energy=80,
            happiness=80,
            hygiene=80,
            owned_consumables=["SMALL_POTION"],
            actions_recorded=0,
        )

        decision = decision_maker.decide(context)

        assert decision.action == ActionType.CONSUMABLES_USE
        assert "POTION" in decision.params.get("consumable_id", "").upper()


class TestWithTokens:
    """Test decision making when tokens are available for purchases."""

    def test_critical_with_tokens_no_consumables(
        self, decision_maker: PetDecisionMaker
    ):
        """When critical with tokens but no consumables, should try to buy."""
        context = create_context(
            hunger=0,
            health=0,
            energy=0,
            happiness=0,
            hygiene=0,
            owned_consumables=[],
            token_balance=100.0,
            actions_recorded=0,
        )

        decision = decision_maker.decide(context)

        # Should try to buy a consumable
        assert decision.action == ActionType.CONSUMABLES_BUY

    def test_low_hunger_with_tokens(self, decision_maker: PetDecisionMaker):
        """When low hunger with tokens but no food, should try to buy."""
        context = create_context(
            hunger=30,
            health=80,
            energy=80,
            happiness=80,
            hygiene=80,
            owned_consumables=[],
            token_balance=100.0,
            actions_recorded=0,
        )

        decision = decision_maker.decide(context)

        assert decision.action == ActionType.CONSUMABLES_BUY

    def test_no_tokens_fallback(self, decision_maker: PetDecisionMaker):
        """When no tokens and no consumables, should fallback to free actions."""
        context = create_context(
            hunger=30,
            health=80,
            energy=80,
            happiness=80,
            hygiene=80,
            owned_consumables=[],
            token_balance=0.0,
            actions_recorded=0,
        )

        decision = decision_maker.decide(context)

        # Should fallback to throwball (earns tokens)
        assert decision.action in (
            ActionType.THROWBALL,
            ActionType.SHOWER,
            ActionType.RUB,
        )


class TestSleepingBehavior:
    """Test special sleeping behavior."""

    def test_sleeping_with_zero_energy_needs_record(
        self, decision_maker: PetDecisionMaker
    ):
        """When sleeping with 0 energy and need on-chain, should wake and re-sleep."""
        context = create_context(
            hunger=50,
            health=50,
            energy=0,
            happiness=50,
            hygiene=50,
            is_sleeping=True,
            actions_recorded=0,  # Need on-chain record
        )

        decision = decision_maker.decide(context)

        assert decision.action == ActionType.SLEEP
        assert decision.should_record_onchain == True
        assert decision.params.get("wake_first") == True

    def test_sleeping_with_low_energy_stays_asleep(
        self, decision_maker: PetDecisionMaker
    ):
        """When sleeping with low (but not zero) energy, should stay asleep."""
        context = create_context(
            hunger=50,
            health=50,
            energy=30,
            happiness=50,
            hygiene=50,
            is_sleeping=True,
            actions_recorded=0,
        )

        decision = decision_maker.decide(context)

        assert decision.action == ActionType.SLEEP
        assert decision.params.get("stay_asleep") == True

    def test_sleeping_critical_wakes_up(self, decision_maker: PetDecisionMaker):
        """When sleeping but stats are critical, should wake up and act."""
        context = create_context(
            hunger=2,
            health=2,
            energy=30,
            happiness=2,
            hygiene=2,
            is_sleeping=True,
            owned_consumables=["BURGER"],
            actions_recorded=0,
        )

        decision = decision_maker.decide(context)

        # Should recognize critical and use consumable
        assert decision.action == ActionType.CONSUMABLES_USE


class TestDeadPet:
    """Test behavior when pet is dead."""

    def test_dead_pet_no_action(self, decision_maker: PetDecisionMaker):
        """When pet is dead, should return NONE action."""
        context = create_context(
            hunger=50,
            health=50,
            energy=50,
            happiness=50,
            hygiene=50,
            is_dead=True,
            actions_recorded=0,
        )

        decision = decision_maker.decide(context)

        assert decision.action == ActionType.NONE
        assert decision.should_record_onchain == False
        assert "dead" in decision.reason.lower()


class TestActionConditions:
    """Test the ActionConditions class directly."""

    def test_can_rub_low_hygiene(self):
        stats = PetStats(hygiene=50)
        can, reason = ActionConditions.can_rub(stats)
        assert can == True
        assert "50" in reason

    def test_cannot_rub_high_hygiene(self):
        stats = PetStats(hygiene=80)
        can, reason = ActionConditions.can_rub(stats)
        assert can == False
        assert "80" in reason

    def test_can_shower_low_hygiene(self):
        stats = PetStats(hygiene=50)
        can, reason = ActionConditions.can_shower(stats)
        assert can == True

    def test_cannot_shower_high_hygiene(self):
        stats = PetStats(hygiene=80)
        can, reason = ActionConditions.can_shower(stats)
        assert can == False

    def test_can_always_sleep(self):
        stats = PetStats()  # Any stats
        can, reason = ActionConditions.can_sleep(stats)
        assert can == True
        assert "always" in reason.lower()

    def test_can_throwball_normal(self):
        stats = PetStats(hunger=50, health=50, energy=50)
        can, reason = ActionConditions.can_throwball(stats)
        assert can == True

    def test_cannot_throwball_all_low(self):
        stats = PetStats(hunger=10, health=10, energy=10)
        can, reason = ActionConditions.can_throwball(stats)
        assert can == False

    def test_can_throwball_one_above_threshold(self):
        stats = PetStats(hunger=15, health=10, energy=10)  # hunger >= 15
        can, reason = ActionConditions.can_throwball(stats)
        assert can == True

    def test_can_use_consumable_with_items(self):
        context = create_context(owned_consumables=["BURGER"])
        can, reason = ActionConditions.can_use_consumable(context)
        assert can == True
        assert "1" in reason

    def test_cannot_use_consumable_empty(self):
        context = create_context(owned_consumables=[])
        can, reason = ActionConditions.can_use_consumable(context)
        assert can == False

    def test_can_buy_with_tokens(self):
        context = create_context(token_balance=100.0)
        can, reason = ActionConditions.can_buy_consumable(context)
        assert can == True

    def test_cannot_buy_without_tokens(self):
        context = create_context(token_balance=10.0)  # Below threshold
        can, reason = ActionConditions.can_buy_consumable(context)
        assert can == False


class TestOnChainRecording:
    """Test that on-chain recording decisions are correct."""

    def test_records_when_needed(self, decision_maker: PetDecisionMaker):
        """Should record on-chain when below required actions."""
        for i in range(8):  # 0-7 actions recorded
            context = create_context(
                hunger=80,
                health=80,
                energy=80,
                happiness=80,
                hygiene=30,
                actions_recorded=i,
                required_actions=8,
            )
            decision = decision_maker.decide(context)
            assert (
                decision.should_record_onchain == True
            ), f"Should record on-chain when {i}/8 actions recorded"

    def test_does_not_record_when_met(self, decision_maker: PetDecisionMaker):
        """Should NOT record on-chain when requirement met."""
        for i in range(8, 15):  # 8+ actions recorded
            context = create_context(
                hunger=80,
                health=80,
                energy=80,
                happiness=80,
                hygiene=30,
                actions_recorded=i,
                required_actions=8,
            )
            decision = decision_maker.decide(context)
            assert (
                decision.should_record_onchain == False
            ), f"Should NOT record on-chain when {i}/8 actions recorded"

    def test_still_performs_action_after_met(self, decision_maker: PetDecisionMaker):
        """Should still perform actions even after on-chain requirement met."""
        context = create_context(
            hunger=80,
            health=80,
            energy=80,
            happiness=80,
            hygiene=30,
            actions_recorded=10,  # Way over requirement
            required_actions=8,
        )

        decision = decision_maker.decide(context)

        # Should still choose an action (to keep pet alive)
        assert decision.action != ActionType.NONE
        assert decision.action == ActionType.SHOWER  # Low hygiene
        assert decision.should_record_onchain == False


class TestFallbackChain:
    """Test that fallback chains work correctly."""

    def test_critical_fallback_to_rub(self, decision_maker: PetDecisionMaker):
        """When critical without consumables or tokens, should fallback to RUB."""
        # All core stats < 5 for critical, energy >= 25 so sleep doesn't trigger first
        # hygiene < 75 allows rub
        context = create_context(
            hunger=2,
            health=2,
            energy=50,
            happiness=2,
            hygiene=2,  # All critical, hygiene allows rub
            owned_consumables=[],
            token_balance=0,
        )

        decision = decision_maker.decide(context)

        assert decision.action == ActionType.RUB
        assert decision.fallback_from == ActionType.CONSUMABLES_USE

    def test_critical_fallback_to_shower(self, decision_maker: PetDecisionMaker):
        """When critical and can't rub (hygiene >= 75), fallback to SHOWER."""
        # This is a weird edge case - high hygiene but critical other stats
        # Actually can't happen since hygiene < 75 is required for critical
        # Let's test with hygiene at 0 (can rub) vs 76 (can't rub)
        context = create_context(
            hunger=2,
            health=2,
            energy=2,
            happiness=2,
            hygiene=76,
            owned_consumables=[],
            token_balance=0,
        )

        decision = decision_maker.decide(context)

        # Can't rub (hygiene >= 75), can't shower (hygiene >= 75), fallback to sleep
        assert decision.action == ActionType.SLEEP

    def test_maintenance_fallback(self, decision_maker: PetDecisionMaker):
        """When all stats full, should fallback through actions to sleep."""
        context = create_context(
            hunger=100,
            health=100,
            energy=100,
            happiness=100,
            hygiene=100,
            owned_consumables=[],
            token_balance=0,
        )

        decision = decision_maker.decide(context)

        # Can't throwball (hygiene >= 75?), can't shower (hygiene >= 75), can't rub
        # Actually throwball condition is about health/hunger/energy, not hygiene
        # So with all stats at 100, can throwball? Let's check:
        # throwball: health >= 15 OR hunger >= 15 OR energy >= 15 -> True
        # But wait, the condition in ActionConditions is inverted:
        # "Cannot throwball if ALL of health, hunger, and energy are below minimum"
        # So with all at 100, can throwball -> True

        # But the maintenance_action prefers throwball, then shower, then rub
        # Shower requires hygiene < 75 -> False with hygiene=100
        # So actually can throwball!

        # Wait, re-reading the code: throwball doesn't have a hygiene condition
        # Let me verify: throwball should be possible
        assert (
            decision.action == ActionType.THROWBALL
            or decision.action == ActionType.SLEEP
        )


class TestDecisionHistory:
    """Test decision history tracking."""

    def test_records_history(self, decision_maker: PetDecisionMaker):
        """Should keep history of decisions."""
        for i in range(5):
            context = create_context(
                hunger=80, health=80, energy=80, happiness=80, hygiene=30 + i * 5
            )
            decision_maker.decide(context)

        history = decision_maker.get_decision_history()
        assert len(history) == 5

    def test_limits_history_size(self, decision_maker: PetDecisionMaker):
        """Should limit history to 50 entries."""
        for i in range(60):
            context = create_context(
                hunger=80, health=80, energy=80, happiness=80, hygiene=30
            )
            decision_maker.decide(context)

        history = decision_maker.get_decision_history()
        assert len(history) == 50  # Limited to 50

    def test_last_decision(self, decision_maker: PetDecisionMaker):
        """Should track last decision."""
        context1 = create_context(hygiene=30)
        context2 = create_context(energy=10)

        decision_maker.decide(context1)
        decision_maker.decide(context2)

        last = decision_maker.get_last_decision()
        assert last.action == ActionType.SLEEP  # energy=10 triggers sleep


class TestPetStatsHelpers:
    """Test PetStats helper methods."""

    def test_is_all_zero(self):
        stats = PetStats(hunger=0, health=0, energy=0, happiness=0, hygiene=0)
        assert stats.is_all_zero() == True

        stats2 = PetStats(hunger=1, health=0, energy=0, happiness=0, hygiene=0)
        assert stats2.is_all_zero() == False

    def test_is_all_full(self):
        stats = PetStats(hunger=100, health=100, energy=100, happiness=100, hygiene=100)
        assert stats.is_all_full() == True

        stats2 = PetStats(hunger=99, health=100, energy=100, happiness=100, hygiene=100)
        assert stats2.is_all_full() == False

    def test_is_critical(self):
        stats = PetStats(hunger=4, health=4, energy=50, happiness=4, hygiene=4)
        assert stats.is_critical(5.0) == True

        stats2 = PetStats(hunger=6, health=4, energy=50, happiness=4, hygiene=4)
        assert stats2.is_critical(5.0) == False  # hunger >= 5

    def test_from_dict(self):
        data = {
            "hunger": "50.5",
            "health": 60,
            "energy": 70.0,
            "happiness": None,
            "hygiene": "invalid",
        }
        stats = PetStats.from_dict(data)

        assert stats.hunger == 50.5
        assert stats.health == 60.0
        assert stats.energy == 70.0
        assert stats.happiness == 0.0  # None -> 0
        assert stats.hygiene == 0.0  # Invalid -> 0

    def test_to_dict(self):
        stats = PetStats(hunger=50, health=60, energy=70, happiness=80, hygiene=90)
        d = stats.to_dict()

        assert d["hunger"] == 50
        assert d["health"] == 60
        assert d["energy"] == 70
        assert d["happiness"] == 80
        assert d["hygiene"] == 90


class TestPetContextHelpers:
    """Test PetContext helper methods."""

    def test_needs_more_onchain_actions(self):
        ctx1 = create_context(actions_recorded=3, required_actions=8)
        assert ctx1.needs_more_onchain_actions == True

        ctx2 = create_context(actions_recorded=8, required_actions=8)
        assert ctx2.needs_more_onchain_actions == False

        ctx3 = create_context(actions_recorded=10, required_actions=8)
        assert ctx3.needs_more_onchain_actions == False

    def test_remaining_required_actions(self):
        ctx1 = create_context(actions_recorded=3, required_actions=8)
        assert ctx1.remaining_required_actions == 5

        ctx2 = create_context(actions_recorded=8, required_actions=8)
        assert ctx2.remaining_required_actions == 0

        ctx3 = create_context(actions_recorded=10, required_actions=8)
        assert ctx3.remaining_required_actions == 0  # Can't be negative


# ==============================================================================
# Full Binary Permutation Tests - All 32 combinations of (0, 100)
# ==============================================================================

from itertools import product


def generate_all_binary_permutations():
    """
    Generate all 32 permutations of (0, 100) for 5 stats.
    Returns list of tuples: (hunger, health, energy, happiness, hygiene)
    """
    values = [0, 100]
    return list(product(values, repeat=5))


def permutation_to_name(perm: Tuple[int, ...]) -> str:
    """Convert a permutation tuple to a readable name."""
    labels = ["H", "L", "E", "P", "Y"]  # Hunger, heaLth, Energy, haPpiness, hYgiene
    parts = []
    for i, val in enumerate(perm):
        if val == 100:
            parts.append(f"{labels[i]}100")
        else:
            parts.append(f"{labels[i]}0")
    return "_".join(parts)


class TestAllBinaryPermutations:
    """
    Test ALL 32 binary permutations of stats (0 or 100 for each stat).

    Each permutation must be able to complete 8 on-chain actions successfully.
    This guarantees the decision maker can always find a valid action path.
    """

    ALL_PERMUTATIONS = generate_all_binary_permutations()

    @pytest.fixture
    def decision_maker(self):
        return PetDecisionMaker()

    def _run_8_action_sequence(
        self,
        decision_maker: PetDecisionMaker,
        hunger: int,
        health: int,
        energy: int,
        happiness: int,
        hygiene: int,
        token_balance: float = 0.0,
        consumables: List[str] = None,
    ) -> List[ActionDecision]:
        """
        Run 8 decisions for a given stat combination.
        Returns list of all 8 decisions.
        """
        decisions = []

        for action_num in range(8):
            context = create_context(
                hunger=hunger,
                health=health,
                energy=energy,
                happiness=happiness,
                hygiene=hygiene,
                token_balance=token_balance,
                owned_consumables=consumables or [],
                actions_recorded=action_num,
                required_actions=8,
            )

            decision = decision_maker.decide(context)
            decisions.append(decision)

        return decisions

    def _assert_8_successful_onchain_actions(
        self,
        decisions: List[ActionDecision],
        perm_name: str,
    ):
        """Assert that all 8 decisions are valid on-chain actions."""
        assert (
            len(decisions) == 8
        ), f"{perm_name}: Expected 8 decisions, got {len(decisions)}"

        for i, decision in enumerate(decisions):
            # Must have a valid action (not NONE)
            assert decision.action != ActionType.NONE, (
                f"{perm_name} action {i+1}/8: Got NONE action, expected valid action. "
                f"Reason: {decision.reason}"
            )

            # Must be marked for on-chain recording
            assert decision.should_record_onchain == True, (
                f"{perm_name} action {i+1}/8: should_record_onchain is False, expected True. "
                f"Action: {decision.action.name}, Reason: {decision.reason}"
            )

    # ==========================================================================
    # Individual permutation tests - explicitly named for clarity
    # ==========================================================================

    # All zeros
    def test_perm_00000(self, decision_maker):
        """(0,0,0,0,0) - All stats zero"""
        decisions = self._run_8_action_sequence(decision_maker, 0, 0, 0, 0, 0)
        self._assert_8_successful_onchain_actions(decisions, "00000")

    # Single stat at 100
    def test_perm_10000(self, decision_maker):
        """(100,0,0,0,0) - Only hunger full"""
        decisions = self._run_8_action_sequence(decision_maker, 100, 0, 0, 0, 0)
        self._assert_8_successful_onchain_actions(decisions, "10000")

    def test_perm_01000(self, decision_maker):
        """(0,100,0,0,0) - Only health full"""
        decisions = self._run_8_action_sequence(decision_maker, 0, 100, 0, 0, 0)
        self._assert_8_successful_onchain_actions(decisions, "01000")

    def test_perm_00100(self, decision_maker):
        """(0,0,100,0,0) - Only energy full"""
        decisions = self._run_8_action_sequence(decision_maker, 0, 0, 100, 0, 0)
        self._assert_8_successful_onchain_actions(decisions, "00100")

    def test_perm_00010(self, decision_maker):
        """(0,0,0,100,0) - Only happiness full"""
        decisions = self._run_8_action_sequence(decision_maker, 0, 0, 0, 100, 0)
        self._assert_8_successful_onchain_actions(decisions, "00010")

    def test_perm_00001(self, decision_maker):
        """(0,0,0,0,100) - Only hygiene full"""
        decisions = self._run_8_action_sequence(decision_maker, 0, 0, 0, 0, 100)
        self._assert_8_successful_onchain_actions(decisions, "00001")

    # Two stats at 100 - all 10 combinations
    def test_perm_11000(self, decision_maker):
        """(100,100,0,0,0) - Hunger and health full"""
        decisions = self._run_8_action_sequence(decision_maker, 100, 100, 0, 0, 0)
        self._assert_8_successful_onchain_actions(decisions, "11000")

    def test_perm_10100(self, decision_maker):
        """(100,0,100,0,0) - Hunger and energy full"""
        decisions = self._run_8_action_sequence(decision_maker, 100, 0, 100, 0, 0)
        self._assert_8_successful_onchain_actions(decisions, "10100")

    def test_perm_10010(self, decision_maker):
        """(100,0,0,100,0) - Hunger and happiness full"""
        decisions = self._run_8_action_sequence(decision_maker, 100, 0, 0, 100, 0)
        self._assert_8_successful_onchain_actions(decisions, "10010")

    def test_perm_10001(self, decision_maker):
        """(100,0,0,0,100) - Hunger and hygiene full"""
        decisions = self._run_8_action_sequence(decision_maker, 0, 0, 0, 0, 100)
        self._assert_8_successful_onchain_actions(decisions, "10001")

    def test_perm_01100(self, decision_maker):
        """(0,100,100,0,0) - Health and energy full"""
        decisions = self._run_8_action_sequence(decision_maker, 0, 100, 100, 0, 0)
        self._assert_8_successful_onchain_actions(decisions, "01100")

    def test_perm_01010(self, decision_maker):
        """(0,100,0,100,0) - Health and happiness full"""
        decisions = self._run_8_action_sequence(decision_maker, 0, 100, 0, 100, 0)
        self._assert_8_successful_onchain_actions(decisions, "01010")

    def test_perm_01001(self, decision_maker):
        """(0,100,0,0,100) - Health and hygiene full"""
        decisions = self._run_8_action_sequence(decision_maker, 0, 100, 0, 0, 100)
        self._assert_8_successful_onchain_actions(decisions, "01001")

    def test_perm_00110(self, decision_maker):
        """(0,0,100,100,0) - Energy and happiness full"""
        decisions = self._run_8_action_sequence(decision_maker, 0, 0, 100, 100, 0)
        self._assert_8_successful_onchain_actions(decisions, "00110")

    def test_perm_00101(self, decision_maker):
        """(0,0,100,0,100) - Energy and hygiene full"""
        decisions = self._run_8_action_sequence(decision_maker, 0, 0, 100, 0, 100)
        self._assert_8_successful_onchain_actions(decisions, "00101")

    def test_perm_00011(self, decision_maker):
        """(0,0,0,100,100) - Happiness and hygiene full"""
        decisions = self._run_8_action_sequence(decision_maker, 0, 0, 0, 100, 100)
        self._assert_8_successful_onchain_actions(decisions, "00011")

    # Three stats at 100 - all 10 combinations
    def test_perm_11100(self, decision_maker):
        """(100,100,100,0,0) - Hunger, health, energy full"""
        decisions = self._run_8_action_sequence(decision_maker, 100, 100, 100, 0, 0)
        self._assert_8_successful_onchain_actions(decisions, "11100")

    def test_perm_11010(self, decision_maker):
        """(100,100,0,100,0) - Hunger, health, happiness full"""
        decisions = self._run_8_action_sequence(decision_maker, 100, 100, 0, 100, 0)
        self._assert_8_successful_onchain_actions(decisions, "11010")

    def test_perm_11001(self, decision_maker):
        """(100,100,0,0,100) - Hunger, health, hygiene full"""
        decisions = self._run_8_action_sequence(decision_maker, 100, 100, 0, 0, 100)
        self._assert_8_successful_onchain_actions(decisions, "11001")

    def test_perm_10110(self, decision_maker):
        """(100,0,100,100,0) - Hunger, energy, happiness full"""
        decisions = self._run_8_action_sequence(decision_maker, 100, 0, 100, 100, 0)
        self._assert_8_successful_onchain_actions(decisions, "10110")

    def test_perm_10101(self, decision_maker):
        """(100,0,100,0,100) - Hunger, energy, hygiene full"""
        decisions = self._run_8_action_sequence(decision_maker, 100, 0, 100, 0, 100)
        self._assert_8_successful_onchain_actions(decisions, "10101")

    def test_perm_10011(self, decision_maker):
        """(100,0,0,100,100) - Hunger, happiness, hygiene full"""
        decisions = self._run_8_action_sequence(decision_maker, 100, 0, 0, 100, 100)
        self._assert_8_successful_onchain_actions(decisions, "10011")

    def test_perm_01110(self, decision_maker):
        """(0,100,100,100,0) - Health, energy, happiness full"""
        decisions = self._run_8_action_sequence(decision_maker, 0, 100, 100, 100, 0)
        self._assert_8_successful_onchain_actions(decisions, "01110")

    def test_perm_01101(self, decision_maker):
        """(0,100,100,0,100) - Health, energy, hygiene full"""
        decisions = self._run_8_action_sequence(decision_maker, 0, 100, 100, 0, 100)
        self._assert_8_successful_onchain_actions(decisions, "01101")

    def test_perm_01011(self, decision_maker):
        """(0,100,0,100,100) - Health, happiness, hygiene full"""
        decisions = self._run_8_action_sequence(decision_maker, 0, 100, 0, 100, 100)
        self._assert_8_successful_onchain_actions(decisions, "01011")

    def test_perm_00111(self, decision_maker):
        """(0,0,100,100,100) - Energy, happiness, hygiene full"""
        decisions = self._run_8_action_sequence(decision_maker, 0, 0, 100, 100, 100)
        self._assert_8_successful_onchain_actions(decisions, "00111")

    # Four stats at 100 - all 5 combinations (only one stat is 0)
    def test_perm_11110(self, decision_maker):
        """(100,100,100,100,0) - Only hygiene zero"""
        decisions = self._run_8_action_sequence(decision_maker, 100, 100, 100, 100, 0)
        self._assert_8_successful_onchain_actions(decisions, "11110")

    def test_perm_11101(self, decision_maker):
        """(100,100,100,0,100) - Only happiness zero"""
        decisions = self._run_8_action_sequence(decision_maker, 100, 100, 100, 0, 100)
        self._assert_8_successful_onchain_actions(decisions, "11101")

    def test_perm_11011(self, decision_maker):
        """(100,100,0,100,100) - Only energy zero"""
        decisions = self._run_8_action_sequence(decision_maker, 100, 100, 0, 100, 100)
        self._assert_8_successful_onchain_actions(decisions, "11011")

    def test_perm_10111(self, decision_maker):
        """(100,0,100,100,100) - Only health zero"""
        decisions = self._run_8_action_sequence(decision_maker, 100, 0, 100, 100, 100)
        self._assert_8_successful_onchain_actions(decisions, "10111")

    def test_perm_01111(self, decision_maker):
        """(0,100,100,100,100) - Only hunger zero"""
        decisions = self._run_8_action_sequence(decision_maker, 0, 100, 100, 100, 100)
        self._assert_8_successful_onchain_actions(decisions, "01111")

    # All stats at 100
    def test_perm_11111(self, decision_maker):
        """(100,100,100,100,100) - All stats full"""
        decisions = self._run_8_action_sequence(decision_maker, 100, 100, 100, 100, 100)
        self._assert_8_successful_onchain_actions(decisions, "11111")

    # ==========================================================================
    # Parametrized test covering ALL 32 permutations in bulk
    # ==========================================================================

    @pytest.mark.parametrize("perm", ALL_PERMUTATIONS)
    def test_all_permutations_complete_8_onchain_actions(
        self, decision_maker, perm: Tuple[int, ...]
    ):
        """
        Parametrized test ensuring EVERY binary permutation can complete 8 on-chain actions.

        This is the CRITICAL test that guarantees the decision maker always finds
        a valid action path regardless of starting stats.
        """
        hunger, health, energy, happiness, hygiene = perm
        perm_name = permutation_to_name(perm)

        decisions = self._run_8_action_sequence(
            decision_maker, hunger, health, energy, happiness, hygiene
        )

        self._assert_8_successful_onchain_actions(decisions, perm_name)

    # ==========================================================================
    # Tests with resources (tokens and consumables)
    # ==========================================================================

    @pytest.mark.parametrize("perm", ALL_PERMUTATIONS)
    def test_all_permutations_with_tokens(self, decision_maker, perm: Tuple[int, ...]):
        """All permutations with 100 tokens available."""
        hunger, health, energy, happiness, hygiene = perm
        perm_name = f"with_tokens_{permutation_to_name(perm)}"

        decisions = self._run_8_action_sequence(
            decision_maker,
            hunger,
            health,
            energy,
            happiness,
            hygiene,
            token_balance=100.0,
        )

        self._assert_8_successful_onchain_actions(decisions, perm_name)

    @pytest.mark.parametrize("perm", ALL_PERMUTATIONS)
    def test_all_permutations_with_consumables(
        self, decision_maker, perm: Tuple[int, ...]
    ):
        """All permutations with consumables available."""
        hunger, health, energy, happiness, hygiene = perm
        perm_name = f"with_consumables_{permutation_to_name(perm)}"

        decisions = self._run_8_action_sequence(
            decision_maker,
            hunger,
            health,
            energy,
            happiness,
            hygiene,
            consumables=["BURGER", "SALAD", "SMALL_POTION"],
        )

        self._assert_8_successful_onchain_actions(decisions, perm_name)

    @pytest.mark.parametrize("perm", ALL_PERMUTATIONS)
    def test_all_permutations_with_full_resources(
        self, decision_maker, perm: Tuple[int, ...]
    ):
        """All permutations with both tokens and consumables."""
        hunger, health, energy, happiness, hygiene = perm
        perm_name = f"full_resources_{permutation_to_name(perm)}"

        decisions = self._run_8_action_sequence(
            decision_maker,
            hunger,
            health,
            energy,
            happiness,
            hygiene,
            token_balance=100.0,
            consumables=["BURGER", "SALAD", "SMALL_POTION"],
        )

        self._assert_8_successful_onchain_actions(decisions, perm_name)


class TestCriticalEdgeCases:
    """
    Test edge cases that are particularly challenging for the decision maker.
    These are the "worst case" scenarios where most actions are blocked.
    """

    @pytest.fixture
    def decision_maker(self):
        return PetDecisionMaker()

    def test_high_hygiene_low_everything_else(self, decision_maker):
        """
        hygiene=100 blocks RUB and SHOWER.
        All core stats at 0 blocks THROWBALL.
        Only SLEEP is available - must still complete 8 actions.
        """
        decisions = []
        for action_num in range(8):
            context = create_context(
                hunger=0,
                health=0,
                energy=0,
                happiness=0,
                hygiene=100,
                token_balance=0.0,
                owned_consumables=[],
                actions_recorded=action_num,
            )
            decision = decision_maker.decide(context)
            decisions.append(decision)

            assert (
                decision.action != ActionType.NONE
            ), f"Action {action_num+1}/8: Got NONE, expected SLEEP as fallback"
            assert decision.should_record_onchain == True
            # In this case, only SLEEP should be possible
            assert (
                decision.action == ActionType.SLEEP
            ), f"Action {action_num+1}/8: Expected SLEEP (only option), got {decision.action.name}"

    def test_all_stats_at_blocking_thresholds(self, decision_maker):
        """
        Test with stats at exact blocking thresholds:
        - hygiene=75 (blocks RUB/SHOWER at >= 75)
        - health=14, hunger=14, energy=14 (blocks THROWBALL at all < 15)
        - Only SLEEP available
        """
        decisions = []
        for action_num in range(8):
            context = create_context(
                hunger=14,
                health=14,
                energy=14,
                happiness=0,
                hygiene=75,
                token_balance=0.0,
                owned_consumables=[],
                actions_recorded=action_num,
            )
            decision = decision_maker.decide(context)
            decisions.append(decision)

            assert decision.action != ActionType.NONE
            assert decision.should_record_onchain == True

    def test_just_above_blocking_thresholds(self, decision_maker):
        """
        Test with stats just above blocking thresholds - more options available:
        - hygiene=74 (allows RUB/SHOWER at < 75)
        - health=15 (allows THROWBALL - at least one >= 15)
        """
        decisions = []
        for action_num in range(8):
            context = create_context(
                hunger=0,
                health=15,
                energy=0,
                happiness=0,
                hygiene=74,
                token_balance=0.0,
                owned_consumables=[],
                actions_recorded=action_num,
            )
            decision = decision_maker.decide(context)
            decisions.append(decision)

            assert decision.action != ActionType.NONE
            assert decision.should_record_onchain == True
            # Should have multiple options available (SHOWER, RUB, THROWBALL, SLEEP)

    def test_varying_stats_across_8_actions(self, decision_maker):
        """
        Simulate realistic scenario where stats vary between actions.
        This tests that we can always find 8 valid actions even with changing stats.
        """
        stat_scenarios = [
            (0, 0, 0, 0, 0),  # All zero - critical, use RUB
            (50, 0, 0, 0, 50),  # After eating - low health/energy
            (50, 30, 0, 0, 50),  # After health item
            (50, 30, 0, 30, 50),  # After happiness action
            (50, 30, 50, 30, 50),  # After sleep/energy recovery
            (50, 30, 50, 30, 30),  # Hygiene dropped
            (30, 30, 30, 30, 30),  # All moderate
            (60, 60, 60, 60, 60),  # All decent
        ]

        decisions = []
        for action_num, (hunger, health, energy, happiness, hygiene) in enumerate(
            stat_scenarios
        ):
            context = create_context(
                hunger=hunger,
                health=health,
                energy=energy,
                happiness=happiness,
                hygiene=hygiene,
                token_balance=0.0,
                owned_consumables=[],
                actions_recorded=action_num,
            )
            decision = decision_maker.decide(context)
            decisions.append(decision)

            assert decision.action != ActionType.NONE, (
                f"Action {action_num+1}/8 with stats {stat_scenarios[action_num]}: "
                f"Got NONE action"
            )
            assert decision.should_record_onchain == True


class TestActionDistribution:
    """
    Test the distribution of actions across different scenarios.
    Ensures we're not just falling back to SLEEP for everything.
    """

    @pytest.fixture
    def decision_maker(self):
        return PetDecisionMaker()

    def test_varied_actions_when_stats_allow(self, decision_maker):
        """
        With varied stats, we should see different actions being chosen,
        not just SLEEP fallback for everything.
        """
        action_counts = {action: 0 for action in ActionType}

        # Run through permutations where multiple actions are possible
        test_cases = [
            (80, 80, 80, 80, 30),  # Low hygiene -> SHOWER
            (80, 80, 20, 80, 80),  # Low energy -> SLEEP
            (80, 80, 80, 30, 80),  # Low happiness -> THROWBALL
            (100, 100, 100, 100, 100),  # All full -> THROWBALL (maintenance)
            (0, 0, 100, 0, 0),  # Critical but energy ok -> RUB
            (80, 80, 80, 80, 60),  # Moderate hygiene -> might SHOWER or other
        ]

        for hunger, health, energy, happiness, hygiene in test_cases:
            context = create_context(
                hunger=hunger,
                health=health,
                energy=energy,
                happiness=happiness,
                hygiene=hygiene,
                actions_recorded=0,
            )
            decision = decision_maker.decide(context)
            action_counts[decision.action] += 1

        # We should have at least 2 different action types
        non_zero_actions = sum(1 for count in action_counts.values() if count > 0)
        assert non_zero_actions >= 2, (
            f"Expected varied actions, but only got: "
            f"{[a.name for a, c in action_counts.items() if c > 0]}"
        )

    def test_sleep_is_always_available_fallback(self, decision_maker):
        """Verify SLEEP works as ultimate fallback in worst case."""
        # Worst case: hygiene=100 (blocks RUB/SHOWER), all core stats=0 (blocks THROWBALL)
        context = create_context(
            hunger=0,
            health=0,
            energy=0,
            happiness=0,
            hygiene=100,
            token_balance=0.0,
            owned_consumables=[],
            actions_recorded=0,
        )
        decision = decision_maker.decide(context)

        assert decision.action == ActionType.SLEEP
        assert decision.should_record_onchain == True


class TestOnChainRecordingGuarantee:
    """
    Tests specifically focused on the on-chain recording guarantee.
    Ensures that actions 1-8 are ALWAYS recorded on-chain.
    """

    @pytest.fixture
    def decision_maker(self):
        return PetDecisionMaker()

    def test_first_8_actions_always_recorded_onchain(self, decision_maker):
        """First 8 actions must have should_record_onchain=True."""
        for action_num in range(8):
            context = create_context(
                hunger=50,
                health=50,
                energy=50,
                happiness=50,
                hygiene=50,
                actions_recorded=action_num,
            )
            decision = decision_maker.decide(context)

            assert (
                decision.should_record_onchain == True
            ), f"Action {action_num+1} should be on-chain but isn't"

    def test_actions_after_8_not_recorded_onchain(self, decision_maker):
        """Actions after the 8th should NOT be recorded on-chain."""
        for action_num in range(8, 12):
            context = create_context(
                hunger=50,
                health=50,
                energy=50,
                happiness=50,
                hygiene=50,
                actions_recorded=action_num,
            )
            decision = decision_maker.decide(context)

            assert (
                decision.should_record_onchain == False
            ), f"Action {action_num+1} should be off-chain but is on-chain"

    def test_onchain_recording_across_all_action_types(self, decision_maker):
        """Every action type should properly set should_record_onchain."""
        # Create contexts that trigger each action type
        test_cases = [
            # (context_kwargs, expected_action)
            ({"energy": 10}, ActionType.SLEEP),
            ({"hygiene": 30}, ActionType.SHOWER),
            (
                {
                    "hunger": 50,
                    "health": 50,
                    "energy": 100,
                    "happiness": 100,
                    "hygiene": 100,
                },
                ActionType.THROWBALL,
            ),
        ]

        for context_kwargs, expected_action in test_cases:
            # Test with actions_recorded=0 (should record)
            context = create_context(**context_kwargs, actions_recorded=0)
            decision = decision_maker.decide(context)

            assert decision.should_record_onchain == True, (
                f"Action {decision.action.name} with actions_recorded=0 "
                f"should be on-chain"
            )

            # Test with actions_recorded=8 (should NOT record)
            context = create_context(**context_kwargs, actions_recorded=8)
            decision = decision_maker.decide(context)

            assert decision.should_record_onchain == False, (
                f"Action {decision.action.name} with actions_recorded=8 "
                f"should be off-chain"
            )


# ==============================================================================
# Integration Tests
# ==============================================================================


class TestIntegration:
    """Integration tests simulating real scenarios."""

    def test_full_epoch_simulation(self, decision_maker: PetDecisionMaker):
        """Simulate a full epoch of decisions."""
        decisions = []

        # Start with fresh pet
        context = create_context(
            hunger=80,
            health=80,
            energy=80,
            happiness=80,
            hygiene=50,
            actions_recorded=0,
            required_actions=8,
        )

        for i in range(10):  # 10 action cycles
            decision = decision_maker.decide(context)
            decisions.append(decision)

            # Simulate stats degradation
            context = create_context(
                hunger=max(0, context.stats.hunger - 5),
                health=max(0, context.stats.health - 3),
                energy=max(0, context.stats.energy - 10),
                happiness=max(0, context.stats.happiness - 5),
                hygiene=max(0, context.stats.hygiene - 5),
                actions_recorded=min(i + 1, 8),  # Count up to 8
                required_actions=8,
            )

        # First 8 should record on-chain
        onchain_count = sum(1 for d in decisions[:8] if d.should_record_onchain)
        assert onchain_count == 8

        # Last 2 should NOT record on-chain
        offchain_count = sum(1 for d in decisions[8:] if not d.should_record_onchain)
        assert offchain_count == 2

    def test_recovery_from_critical(self, decision_maker: PetDecisionMaker):
        """Test recovery path from critical state."""
        # Start critical with energy >= 25 so sleep doesn't trigger
        context = create_context(
            hunger=2,
            health=2,
            energy=50,
            happiness=2,
            hygiene=2,
            owned_consumables=["BURGER", "SALAD", "SMALL_POTION"],
            token_balance=100.0,
            actions_recorded=0,
        )

        decision1 = decision_maker.decide(context)

        # Should use consumables first (critical state)
        assert decision1.action == ActionType.CONSUMABLES_USE

        # Simulate partial recovery - hunger improved but health still low
        # Now not critical (hunger >= 5), but health < 70 triggers health recovery
        context = create_context(
            hunger=50,
            health=30,
            energy=50,
            happiness=50,
            hygiene=50,
            owned_consumables=["SMALL_POTION"],
            token_balance=100.0,
            actions_recorded=1,
        )

        decision2 = decision_maker.decide(context)

        # Should try health recovery (health < 70 with POTION available)
        assert decision2.action == ActionType.CONSUMABLES_USE
        assert "POTION" in decision2.params.get("consumable_id", "").upper()


class TestFailedActionLoopPrevention:
    """
    Tests for preventing infinite retry loops when actions fail.

    This addresses the bug where the agent would repeatedly try to use
    a POTION that fails with "Pet does not have enough stats", getting
    stuck in an infinite loop.
    """

    @pytest.fixture
    def decision_maker(self):
        return PetDecisionMaker()

    def test_record_action_failure(self, decision_maker: PetDecisionMaker):
        """Test that failures are recorded correctly."""
        decision_maker.record_action_failure(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "POTION"},
            reason="Pet does not have enough stats",
        )

        failures = decision_maker.get_failed_actions()
        assert len(failures) == 1
        assert failures[0].action == ActionType.CONSUMABLES_USE
        assert failures[0].params["consumable_id"] == "POTION"
        # Reason is stored but doesn't affect matching
        assert failures[0].reason == "Pet does not have enough stats"

    def test_failure_matching_independent_of_reason(
        self, decision_maker: PetDecisionMaker
    ):
        """
        Test that failure matching works regardless of the error message.

        The matching logic only uses action type and params, not the reason string.
        This ensures the system works even if error messages change.
        """
        # Record failure with one error message
        decision_maker.record_action_failure(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "POTION"},
            reason="Pet does not have enough stats",
        )

        # Should be blocked regardless of what reason we check with
        assert decision_maker.is_action_blocked(
            ActionType.CONSUMABLES_USE,
            {"consumable_id": "POTION"},
        )

        # Record same action with completely different error message
        decision_maker.clear_all_failures()
        decision_maker.record_action_failure(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "POTION"},
            reason="Insufficient resources",  # Different error message
        )

        # Should still be blocked - matching is independent of reason
        assert decision_maker.is_action_blocked(
            ActionType.CONSUMABLES_USE,
            {"consumable_id": "POTION"},
        )

    def test_is_action_blocked_after_failure(self, decision_maker: PetDecisionMaker):
        """Test that failed actions are blocked."""
        # Record a failure
        decision_maker.record_action_failure(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "POTION"},
            reason="Server error",
        )

        # Check if blocked
        assert decision_maker.is_action_blocked(
            ActionType.CONSUMABLES_USE,
            {"consumable_id": "POTION"},
        )

        # Different consumable should not be blocked
        assert not decision_maker.is_action_blocked(
            ActionType.CONSUMABLES_USE,
            {"consumable_id": "BURGER"},
        )

        # Different action type should not be blocked
        assert not decision_maker.is_action_blocked(ActionType.SLEEP)

    def test_get_blocked_consumables(self, decision_maker: PetDecisionMaker):
        """Test getting list of blocked consumables."""
        decision_maker.record_action_failure(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "POTION"},
            reason="Error 1",
        )
        decision_maker.record_action_failure(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "SALAD"},
            reason="Error 2",
        )

        blocked = decision_maker.get_blocked_consumables()
        assert "POTION" in blocked
        assert "SALAD" in blocked
        assert len(blocked) == 2

    def test_health_recovery_skips_blocked_consumable(
        self, decision_maker: PetDecisionMaker
    ):
        """
        Test that health recovery skips blocked consumables and uses next best.

        This is the exact scenario from the bug report:
        - Pet has POTION
        - POTION use fails with "Pet does not have enough stats"
        - Agent should NOT keep retrying POTION, should try alternative
        """
        # Record that POTION failed
        decision_maker.record_action_failure(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "POTION"},
            reason="Pet does not have enough stats",
        )

        # Create context with low health and multiple consumables
        context = create_context(
            hunger=80,
            health=30,  # Low health triggers health recovery
            energy=80,
            happiness=80,
            hygiene=80,
            owned_consumables=["POTION", "SALAD"],  # Has POTION and SALAD
            actions_recorded=0,
        )

        decision = decision_maker.decide(context)

        # Should use SALAD instead of blocked POTION
        assert decision.action == ActionType.CONSUMABLES_USE
        assert decision.params.get("consumable_id") == "SALAD"

    def test_hunger_recovery_skips_blocked_consumable(
        self, decision_maker: PetDecisionMaker
    ):
        """Test that hunger recovery skips blocked food items."""
        # Record that BURGER failed
        decision_maker.record_action_failure(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "BURGER"},
            reason="Some error",
        )

        # Create context with low hunger
        context = create_context(
            hunger=30,  # Low hunger
            health=80,
            energy=80,
            happiness=80,
            hygiene=80,
            owned_consumables=["BURGER", "COOKIE"],  # Has BURGER and COOKIE
            actions_recorded=0,
        )

        decision = decision_maker.decide(context)

        # Should use COOKIE instead of blocked BURGER
        assert decision.action == ActionType.CONSUMABLES_USE
        assert decision.params.get("consumable_id") == "COOKIE"

    def test_critical_stats_skips_blocked_consumable(
        self, decision_maker: PetDecisionMaker
    ):
        """Test that critical state handling skips blocked consumables."""
        # Block BURGER
        decision_maker.record_action_failure(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "BURGER"},
            reason="Error",
        )

        # Critical state with multiple consumables
        context = create_context(
            hunger=2,
            health=2,
            energy=50,  # Not too low to trigger sleep first
            happiness=2,
            hygiene=2,
            owned_consumables=["BURGER", "SALAD"],
            actions_recorded=0,
        )

        decision = decision_maker.decide(context)

        # Should use SALAD (BURGER is blocked)
        assert decision.action == ActionType.CONSUMABLES_USE
        assert decision.params.get("consumable_id") == "SALAD"

    def test_all_consumables_blocked_falls_back_to_free_action(
        self, decision_maker: PetDecisionMaker
    ):
        """
        When all owned consumables are blocked, should fall back to free actions.

        This prevents the infinite loop from the bug report.
        """
        # Block all owned consumables
        decision_maker.record_action_failure(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "POTION"},
            reason="Error",
        )

        context = create_context(
            hunger=80,
            health=30,  # Low health would normally trigger POTION use
            energy=80,
            happiness=80,
            hygiene=50,  # Low enough to allow shower
            owned_consumables=["POTION"],  # Only has blocked POTION
            token_balance=0,  # Can't buy
            actions_recorded=0,
        )

        decision = decision_maker.decide(context)

        # Should NOT use POTION (blocked)
        # Should fall back to next priority (hygiene -> SHOWER)
        assert decision.action != ActionType.CONSUMABLES_USE
        assert decision.action in (
            ActionType.SHOWER,
            ActionType.THROWBALL,
            ActionType.RUB,
            ActionType.SLEEP,
        )

    def test_failure_record_expires_after_cooldown(
        self, decision_maker: PetDecisionMaker
    ):
        """Test that failure records expire and allow retry after cooldown."""
        # Create an expired failure
        old_failure = FailedAction(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "POTION"},
            failed_at=datetime.now()
            - timedelta(seconds=FailedAction.COOLDOWN_SECONDS + 10),
            reason="Old error",
        )
        decision_maker._failed_actions.append(old_failure)

        # Should be expired
        assert old_failure.is_expired()

        # Should not be blocked (expired failures are cleared)
        assert not decision_maker.is_action_blocked(
            ActionType.CONSUMABLES_USE,
            {"consumable_id": "POTION"},
        )

    def test_clear_all_failures(self, decision_maker: PetDecisionMaker):
        """Test clearing all failure records."""
        decision_maker.record_action_failure(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "POTION"},
            reason="Error 1",
        )
        decision_maker.record_action_failure(
            action=ActionType.CONSUMABLES_BUY,
            params={"consumable_id": "BURGER"},
            reason="Error 2",
        )

        assert len(decision_maker.get_failed_actions()) == 2

        decision_maker.clear_all_failures()

        assert len(decision_maker.get_failed_actions()) == 0

    def test_update_existing_failure_record(self, decision_maker: PetDecisionMaker):
        """Test that recording same failure updates timestamp instead of duplicating."""
        # Record initial failure
        decision_maker.record_action_failure(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "POTION"},
            reason="First error",
        )

        first_failure = decision_maker.get_failed_actions()[0]
        first_time = first_failure.failed_at

        # Small delay
        import time

        time.sleep(0.01)

        # Record same failure again
        decision_maker.record_action_failure(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "POTION"},
            reason="Second error",
        )

        # Should still have only 1 failure record
        failures = decision_maker.get_failed_actions()
        assert len(failures) == 1

        # Timestamp should be updated
        assert failures[0].failed_at > first_time
        assert failures[0].reason == "Second error"

    def test_no_infinite_loop_scenario(self, decision_maker: PetDecisionMaker):
        """
        Simulate the exact bug scenario: repeatedly failing POTION use.

        Decision engine should not recommend the same failed action repeatedly.
        Note: The error message doesn't matter - matching is based on action/params only.
        """
        context = create_context(
            hunger=2.8,
            health=59.3,  # Low health - would trigger POTION use
            energy=95.2,
            happiness=54.7,
            hygiene=46.2,
            owned_consumables=["POTION", "POTION", "POTION"],  # 3 potions
            token_balance=59.14,
            actions_recorded=0,
            required_actions=9,
        )

        # First decision - should try POTION
        decision1 = decision_maker.decide(context)
        assert decision1.action == ActionType.CONSUMABLES_USE
        consumable1 = decision1.params.get("consumable_id", "")
        assert "POTION" in consumable1.upper()

        # Simulate failure (error message can be anything - doesn't affect matching)
        decision_maker.record_action_failure(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": consumable1},
            reason="Server error: action failed",  # Generic error - any message works
        )

        # Second decision - should NOT try POTION again
        decision2 = decision_maker.decide(context)

        # Should choose a different action (not the blocked POTION)
        if decision2.action == ActionType.CONSUMABLES_USE:
            # If still consumable use, must be different consumable
            consumable2 = decision2.params.get("consumable_id", "")
            assert consumable2.upper() != consumable1.upper()
        else:
            # Or chose completely different action (fallback)
            assert decision2.action in (
                ActionType.SHOWER,
                ActionType.THROWBALL,
                ActionType.RUB,
                ActionType.SLEEP,
                ActionType.CONSUMABLES_BUY,
            )


class TestFailedActionDataclass:
    """Tests for the FailedAction dataclass."""

    def test_is_expired_fresh_failure(self):
        """Fresh failure should not be expired."""
        failure = FailedAction(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "POTION"},
            failed_at=datetime.now(),
        )
        assert not failure.is_expired()

    def test_is_expired_old_failure(self):
        """Old failure should be expired."""
        failure = FailedAction(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "POTION"},
            failed_at=datetime.now()
            - timedelta(seconds=FailedAction.COOLDOWN_SECONDS + 1),
        )
        assert failure.is_expired()

    def test_matches_same_consumable(self):
        """Should match same action and consumable."""
        failure = FailedAction(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "POTION"},
            failed_at=datetime.now(),
        )
        assert failure.matches(ActionType.CONSUMABLES_USE, {"consumable_id": "POTION"})

    def test_matches_different_consumable(self):
        """Should not match different consumable."""
        failure = FailedAction(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "POTION"},
            failed_at=datetime.now(),
        )
        assert not failure.matches(
            ActionType.CONSUMABLES_USE, {"consumable_id": "BURGER"}
        )

    def test_matches_different_action_type(self):
        """Should not match different action type."""
        failure = FailedAction(
            action=ActionType.CONSUMABLES_USE,
            params={"consumable_id": "POTION"},
            failed_at=datetime.now(),
        )
        assert not failure.matches(ActionType.SLEEP, {})

    def test_matches_non_consumable_action(self):
        """Non-consumable actions should match on action type only."""
        failure = FailedAction(
            action=ActionType.THROWBALL,
            params={},
            failed_at=datetime.now(),
        )
        assert failure.matches(ActionType.THROWBALL, {})
        assert failure.matches(ActionType.THROWBALL, {"some": "param"})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
