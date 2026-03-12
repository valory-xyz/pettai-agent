"""
Decision engine tests — unit tests + time-based survival simulation.

Tests cover:
  1. Individual action scoring at various stat levels
  2. Survival mode triggers (any stat <= 20)
  3. Sleeping management edge cases
  4. Always-selects-an-action guarantee (no NONE for living pets)
  5. Full time simulation: stats decay over 48h, actions apply effects,
     verify the pet never dies and an action is chosen every tick.
"""

import random
import sys
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "olas-sdk-starter")
)

from agent.decision_engine import (
    ActionConditions,
    ActionType,
    AgentDecisionLog,
    ConsumableSelector,
    PetContext,
    PetDecisionMaker,
    PetStats,
    compute_urgency_scores,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(
    hunger=50.0,
    health=50.0,
    energy=50.0,
    happiness=50.0,
    hygiene=50.0,
    sleeping=False,
    dead=False,
    tokens=100.0,
    consumables=None,
    epoch_actions=0,
):
    """Shorthand to build a PetContext."""
    return PetContext(
        stats=PetStats(
            hunger=hunger,
            health=health,
            energy=energy,
            happiness=happiness,
            hygiene=hygiene,
        ),
        is_sleeping=sleeping,
        is_dead=dead,
        token_balance=tokens,
        owned_consumables=consumables or [],
        actions_recorded_this_epoch=epoch_actions,
    )


def _decide(maker, **kwargs):
    """Decide and return (ActionType, decision)."""
    ctx = _ctx(**kwargs)
    d = maker.decide(ctx)
    return d.action, d


# ---------------------------------------------------------------------------
# 1. Basic action selection at various stat levels
# ---------------------------------------------------------------------------

class TestActionSelection:
    """Verify the engine picks the right action for known stat snapshots."""

    def setup_method(self):
        self.maker = PetDecisionMaker()

    def test_dead_pet_returns_none(self):
        action, _ = _decide(self.maker, dead=True)
        assert action == ActionType.NONE

    def test_all_stats_healthy_picks_throwball(self):
        action, _ = _decide(
            self.maker, hunger=80, health=90, energy=60, happiness=80, hygiene=80
        )
        assert action == ActionType.THROWBALL

    def test_low_hygiene_picks_shower(self):
        """Hygiene <= 25 should trigger SHOWER over THROWBALL."""
        action, _ = _decide(
            self.maker, hunger=80, health=90, energy=60, happiness=80, hygiene=20
        )
        assert action == ActionType.SHOWER

    def test_moderate_hygiene_picks_throwball(self):
        """Hygiene ~50 should still prefer THROWBALL (higher token earnings)."""
        action, _ = _decide(
            self.maker, hunger=80, health=90, energy=60, happiness=80, hygiene=50
        )
        assert action == ActionType.THROWBALL

    def test_critical_energy_sleeps(self):
        action, _ = _decide(
            self.maker, hunger=60, health=70, energy=15, happiness=50, hygiene=50
        )
        assert action == ActionType.SLEEP

    def test_critical_energy_uses_energizer_if_available(self):
        action, d = _decide(
            self.maker,
            hunger=60, health=70, energy=18, happiness=50, hygiene=50,
            consumables=["ENERGIZER"],
        )
        assert action == ActionType.CONSUMABLES_USE
        assert d.params.get("consumable_id") == "ENERGIZER"

    def test_critical_hunger_feeds(self):
        action, d = _decide(
            self.maker,
            hunger=15, health=70, energy=60, happiness=50, hygiene=50,
            consumables=["BURGER"],
        )
        assert action == ActionType.CONSUMABLES_USE
        assert d.params.get("consumable_id") == "BURGER"

    def test_critical_hunger_auto_buys_when_no_food(self):
        action, d = _decide(
            self.maker,
            hunger=15, health=70, energy=60, happiness=50, hygiene=50,
            tokens=200.0,
        )
        assert action == ActionType.CONSUMABLES_USE
        assert "auto_buy" in d.params.get("action", "")

    def test_critical_health_uses_potion(self):
        action, d = _decide(
            self.maker,
            hunger=60, health=18, energy=60, happiness=50, hygiene=50,
            consumables=["SMALL_POTION"],
        )
        assert action == ActionType.CONSUMABLES_USE
        assert d.params.get("consumable_id") == "SMALL_POTION"

    def test_hygiene_above_75_cannot_shower_or_rub(self):
        """When hygiene >= 75, SHOWER/RUB are unavailable; should THROWBALL."""
        action, _ = _decide(
            self.maker, hunger=80, health=90, energy=55, happiness=80, hygiene=80
        )
        assert action == ActionType.THROWBALL

    def test_sleeping_stay_asleep_when_energy_low(self):
        """Sleeping with low (but non-zero) energy should stay asleep."""
        action, d = _decide(
            self.maker,
            hunger=60, health=70, energy=30, happiness=50, hygiene=50,
            sleeping=True,
        )
        assert action == ActionType.SLEEP
        assert d.params.get("stay_asleep") is True

    def test_sleeping_zero_energy_wake_resleep(self):
        """Sleeping with 0 energy and needing on-chain should wake+resleep."""
        action, d = _decide(
            self.maker,
            hunger=60, health=70, energy=0, happiness=50, hygiene=50,
            sleeping=True, epoch_actions=0,
        )
        assert action == ActionType.SLEEP
        assert d.params.get("wake_first") is True

    def test_rub_when_throwball_blocked(self):
        """If THROWBALL is blocked by recent failure, should pick next best."""
        self.maker.record_action_failure(ActionType.THROWBALL, {}, "test")
        action, _ = _decide(
            self.maker, hunger=80, health=90, energy=60, happiness=80, hygiene=50
        )
        # Should fall to SHOWER or RUB (hygiene=50 makes both available)
        assert action in (ActionType.SHOWER, ActionType.RUB)


# ---------------------------------------------------------------------------
# 2. Decision log is always attached
# ---------------------------------------------------------------------------

class TestDecisionLog:
    def setup_method(self):
        self.maker = PetDecisionMaker()

    def test_decision_log_present(self):
        _, d = _decide(self.maker)
        assert d.decision_log is not None
        assert isinstance(d.decision_log, AgentDecisionLog)

    def test_decision_log_has_required_fields(self):
        _, d = _decide(self.maker)
        log = d.decision_log.to_dict()
        assert "action" in log
        assert "reasoning" in log
        assert "statsBefore" in log
        assert "urgencyScores" in log
        assert "alternativesConsidered" in log
        assert "mode" in log
        assert "timestamp" in log

    def test_alternatives_sorted_descending(self):
        _, d = _decide(self.maker, hygiene=30, energy=60)
        alts = d.decision_log.alternatives_considered
        scores = [a["score"] for a in alts]
        assert scores == sorted(scores, reverse=True)

    def test_mode_survival_on_critical(self):
        _, d = _decide(self.maker, energy=10)
        assert d.decision_log.mode == "survival"

    def test_mode_farming_when_token_eligible(self):
        _, d = _decide(
            self.maker, hunger=80, health=90, energy=55, happiness=80, hygiene=80
        )
        assert d.decision_log.mode == "farming"


# ---------------------------------------------------------------------------
# 3. Urgency score computation
# ---------------------------------------------------------------------------

class TestUrgencyScores:
    def test_full_stats_zero_urgency(self):
        stats = PetStats(hunger=100, health=100, energy=100, happiness=100, hygiene=100)
        scores = compute_urgency_scores(stats)
        assert all(v <= 0 for v in scores.values())

    def test_zero_stats_max_urgency(self):
        stats = PetStats(hunger=0, health=0, energy=0, happiness=0, hygiene=0)
        scores = compute_urgency_scores(stats)
        assert all(v > 0 for v in scores.values())

    def test_hygiene_highest_weight(self):
        """Hygiene at same % level should produce higher urgency than hunger."""
        stats = PetStats(hunger=30, health=100, energy=100, happiness=100, hygiene=30)
        scores = compute_urgency_scores(stats)
        # hygiene threshold=75 with weight 1.5 vs hunger threshold=15 weight 1.0
        assert scores["hygiene"] > scores["hunger"]


# ---------------------------------------------------------------------------
# 4. Never returns NONE for a living pet (exhaustive stat combos)
# ---------------------------------------------------------------------------

class TestNeverNone:
    """Verify the engine ALWAYS picks an action for a living, non-sleeping pet."""

    STAT_LEVELS = [0, 5, 10, 20, 30, 50, 70, 85, 100]

    def setup_method(self):
        self.maker = PetDecisionMaker()

    def test_never_none_grid(self):
        """Test a grid of stat combinations — no NONE allowed."""
        levels = self.STAT_LEVELS
        count = 0
        for hunger in levels:
            for energy in levels:
                for hygiene in levels:
                    # Fix health=50, happiness=50 to keep combinatorics sane
                    ctx = _ctx(
                        hunger=hunger, health=50, energy=energy,
                        happiness=50, hygiene=hygiene, tokens=0,
                    )
                    d = self.maker.decide(ctx)
                    assert d.action != ActionType.NONE, (
                        f"Got NONE for hunger={hunger} energy={energy} "
                        f"hygiene={hygiene}"
                    )
                    count += 1
        assert count == len(levels) ** 3

    def test_never_none_all_zero_stats(self):
        ctx = _ctx(hunger=0, health=0, energy=0, happiness=0, hygiene=0, tokens=0)
        d = self.maker.decide(ctx)
        assert d.action != ActionType.NONE

    def test_never_none_all_full_stats(self):
        ctx = _ctx(
            hunger=100, health=100, energy=100, happiness=100, hygiene=100, tokens=0
        )
        d = self.maker.decide(ctx)
        assert d.action != ActionType.NONE

    def test_never_none_random_500(self):
        """500 random stat combinations should all produce a valid action."""
        rng = random.Random(42)
        for _ in range(500):
            ctx = _ctx(
                hunger=rng.uniform(0, 100),
                health=rng.uniform(0, 100),
                energy=rng.uniform(0, 100),
                happiness=rng.uniform(0, 100),
                hygiene=rng.uniform(0, 100),
                tokens=rng.uniform(0, 500),
                consumables=(
                    rng.sample(["BURGER", "SMALL_POTION", "ENERGIZER"], k=rng.randint(0, 3))
                ),
            )
            d = self.maker.decide(ctx)
            assert d.action != ActionType.NONE


# ---------------------------------------------------------------------------
# 5. Time simulation — does the pet survive 48 hours?
# ---------------------------------------------------------------------------

# Stat decay per minute (from spec)
DECAY_PER_MINUTE = {
    "hygiene": 0.03704,
    "happiness": 0.03032,
    "energy": 0.02776,
    "hunger": 0.02084,
    "health": 0.01388,
}

TICK_MINUTES = 7  # agent runs every 7 minutes


def _apply_decay(stats: PetStats, minutes: float) -> PetStats:
    """Apply passive stat decay for `minutes`."""
    return PetStats(
        hunger=max(0, stats.hunger - DECAY_PER_MINUTE["hunger"] * minutes),
        health=max(0, stats.health - DECAY_PER_MINUTE["health"] * minutes),
        energy=max(0, stats.energy - DECAY_PER_MINUTE["energy"] * minutes),
        happiness=max(0, stats.happiness - DECAY_PER_MINUTE["happiness"] * minutes),
        hygiene=max(0, stats.hygiene - DECAY_PER_MINUTE["hygiene"] * minutes),
    )


def _apply_sleep_recovery(stats: PetStats, minutes: float) -> PetStats:
    """While sleeping, energy restores at ~1.5/min (recovers in ~45 min from 30)."""
    return PetStats(
        hunger=max(0, stats.hunger - DECAY_PER_MINUTE["hunger"] * minutes),
        health=max(0, stats.health - DECAY_PER_MINUTE["health"] * minutes),
        energy=min(100, stats.energy + 1.5 * minutes),  # energy restores fast while sleeping
        happiness=max(0, stats.happiness - DECAY_PER_MINUTE["happiness"] * minutes),
        hygiene=max(0, stats.hygiene - DECAY_PER_MINUTE["hygiene"] * minutes),
    )


def _apply_action_effects(action: ActionType, stats: PetStats, params: Dict) -> PetStats:
    """Simulate approximate action effects on stats (using midpoint of ranges)."""
    h, hp, e, hap, hyg = stats.hunger, stats.health, stats.energy, stats.happiness, stats.hygiene

    if action == ActionType.SHOWER:
        hyg = min(100, hyg + 75)   # +55~95, midpoint ~75
        hap = min(100, hap + 5)    # +1~10

    elif action == ActionType.RUB:
        hyg = min(100, hyg + 4.5)  # +2~7
        hap = min(100, hap + 3)    # +1~5

    elif action == ActionType.THROWBALL:
        h = max(0, h - 4)          # hunger -1~7
        e = max(0, e - 4)          # energy -1~7
        hyg = max(0, hyg - 10)     # hygiene -5~15
        hap = min(100, hap + 10)   # happiness +5~15

    elif action == ActionType.CONSUMABLES_USE:
        cid = params.get("consumable_id", "").upper()
        if cid in ("BURGER", "PIZZA", "COOKIE", "SUSHI", "STEAK", "SALAD"):
            h = min(100, h + 30)       # rough food restore
        elif cid in ("SMALL_POTION", "POTION", "LARGE_POTION"):
            hp = min(100, hp + 25)     # rough potion restore
        elif cid == "ENERGIZER":
            e = min(100, e + 40)       # rough energizer restore

    elif action == ActionType.SLEEP:
        pass  # sleep recovery handled between ticks

    elif action == ActionType.CONSUMABLES_BUY:
        pass  # no stat change, just adds to inventory

    return PetStats(hunger=h, health=hp, energy=e, happiness=hap, hygiene=hyg)


@dataclass
class SimResult:
    """Summary of a simulation run."""
    ticks: int
    survived: bool
    min_stats: Dict[str, float]
    action_counts: Dict[str, int]
    tokens_earned_approx: float
    death_tick: Optional[int]
    stats_timeline: List[Dict[str, float]]


def simulate(
    hours: float = 48,
    initial_stats: Optional[PetStats] = None,
    initial_consumables: Optional[List[str]] = None,
    initial_tokens: float = 200.0,
    seed: int = 42,
) -> SimResult:
    """
    Simulate the pet over `hours` with the decision engine choosing actions.

    Returns a SimResult with survival status, min stats, action counts, etc.
    """
    rng = random.Random(seed)
    maker = PetDecisionMaker()

    stats = initial_stats or PetStats(
        hunger=80, health=90, energy=70, happiness=75, hygiene=60
    )
    consumables = list(initial_consumables or ["BURGER", "BURGER", "SMALL_POTION"])
    tokens = initial_tokens
    is_sleeping = False

    total_ticks = int((hours * 60) / TICK_MINUTES)
    action_counts: Dict[str, int] = {}
    min_stats = stats.to_dict()
    tokens_earned = 0.0
    death_tick = None
    timeline: List[Dict[str, float]] = []

    for tick in range(total_ticks):
        # --- Decay between ticks ---
        if is_sleeping:
            stats = _apply_sleep_recovery(stats, TICK_MINUTES)
        else:
            stats = _apply_decay(stats, TICK_MINUTES)

        # Track minimums
        for k, v in stats.to_dict().items():
            if v < min_stats[k]:
                min_stats[k] = v

        # Record timeline every 10 ticks (~70 min)
        if tick % 10 == 0:
            timeline.append({"tick": tick, **stats.to_dict()})

        # --- Check death (all stats 0) ---
        if stats.is_all_zero():
            death_tick = tick
            return SimResult(
                ticks=tick,
                survived=False,
                min_stats=min_stats,
                action_counts=action_counts,
                tokens_earned_approx=tokens_earned,
                death_tick=death_tick,
                stats_timeline=timeline,
            )

        # --- Build context and decide ---
        ctx = PetContext(
            stats=stats,
            is_sleeping=is_sleeping,
            token_balance=tokens,
            owned_consumables=list(consumables),
            actions_recorded_this_epoch=tick % 9,
        )
        decision = maker.decide(ctx)
        action = decision.action

        # Track
        name = action.name
        action_counts[name] = action_counts.get(name, 0) + 1

        # --- Handle sleep toggle ---
        if action == ActionType.SLEEP:
            if decision.params.get("stay_asleep"):
                pass  # remain sleeping
            elif decision.params.get("wake_first"):
                is_sleeping = False
                # immediately re-sleep
                is_sleeping = True
            else:
                is_sleeping = not is_sleeping
        else:
            # Any non-sleep action requires being awake
            if is_sleeping:
                is_sleeping = False

        # --- Apply action effects ---
        stats = _apply_action_effects(action, stats, decision.params)

        # --- Simulate consumable usage ---
        if action == ActionType.CONSUMABLES_USE:
            cid = decision.params.get("consumable_id", "").upper()
            if cid in [c.upper() for c in consumables]:
                # Remove one instance
                for i, c in enumerate(consumables):
                    if c.upper() == cid:
                        consumables.pop(i)
                        break
            elif "auto_buy" in decision.params.get("action", ""):
                # Auto-buy costs tokens
                tokens = max(0, tokens - 50)

        elif action == ActionType.CONSUMABLES_BUY:
            cid = decision.params.get("consumable_id", "BURGER")
            consumables.append(cid)
            tokens = max(0, tokens - 50)

        # --- Simulate token earnings ---
        if action == ActionType.THROWBALL and rng.random() < 0.6:
            earned = rng.uniform(1, 2) * 1.0  # rtim=1, level_mult=1
            tokens += earned
            tokens_earned += earned
        elif action == ActionType.SHOWER:
            earned = rng.uniform(0.5, 2.0)
            tokens += earned
            tokens_earned += earned

        # --- Replenish consumable inventory periodically (simulate buying) ---
        if tick % 60 == 0 and tick > 0 and tokens > 150:
            consumables.extend(["BURGER", "SMALL_POTION"])
            tokens -= 100

    timeline.append({"tick": total_ticks, **stats.to_dict()})

    return SimResult(
        ticks=total_ticks,
        survived=True,
        min_stats=min_stats,
        action_counts=action_counts,
        tokens_earned_approx=tokens_earned,
        death_tick=None,
        stats_timeline=timeline,
    )


class TestSimulation:
    """Full time simulations to verify the pet survives."""

    def test_48h_default_start(self):
        """Pet with decent starting stats and some consumables survives 48h."""
        result = simulate(hours=48)
        assert result.survived, (
            f"Pet died at tick {result.death_tick}! "
            f"Min stats: {result.min_stats}, Actions: {result.action_counts}"
        )
        # Should have used a variety of actions
        assert len(result.action_counts) >= 3, f"Only used: {result.action_counts}"
        assert "THROWBALL" in result.action_counts
        assert result.tokens_earned_approx > 0

    def test_48h_no_consumables_no_tokens(self):
        """Pet with NO consumables and NO tokens must survive on free actions."""
        result = simulate(
            hours=48,
            initial_consumables=[],
            initial_tokens=0,
        )
        assert result.survived, (
            f"Pet died at tick {result.death_tick}! "
            f"Min stats: {result.min_stats}, Actions: {result.action_counts}"
        )

    def test_48h_starting_low_stats(self):
        """Pet starting with all stats at 25 should recover and survive."""
        result = simulate(
            hours=48,
            initial_stats=PetStats(
                hunger=25, health=25, energy=25, happiness=25, hygiene=25
            ),
            initial_consumables=["BURGER", "BURGER", "SMALL_POTION", "ENERGIZER"],
            initial_tokens=300,
        )
        assert result.survived, (
            f"Pet died at tick {result.death_tick}! "
            f"Min stats: {result.min_stats}, Actions: {result.action_counts}"
        )

    def test_48h_high_stats_no_help(self):
        """Pet starting at 100% everywhere with no consumables or tokens."""
        result = simulate(
            hours=48,
            initial_stats=PetStats(
                hunger=100, health=100, energy=100, happiness=100, hygiene=100
            ),
            initial_consumables=[],
            initial_tokens=0,
        )
        assert result.survived, (
            f"Pet died at tick {result.death_tick}! "
            f"Min stats: {result.min_stats}, Actions: {result.action_counts}"
        )

    def test_24h_critical_start(self):
        """Pet starting at the brink (all stats = 5) with some supplies."""
        result = simulate(
            hours=24,
            initial_stats=PetStats(
                hunger=5, health=5, energy=5, happiness=5, hygiene=5
            ),
            initial_consumables=[
                "BURGER", "BURGER", "BURGER",
                "SMALL_POTION", "SMALL_POTION",
                "ENERGIZER", "ENERGIZER",
            ],
            initial_tokens=500,
        )
        assert result.survived, (
            f"Pet died at tick {result.death_tick}! "
            f"Min stats: {result.min_stats}, Actions: {result.action_counts}"
        )

    def test_action_distribution_is_sane(self):
        """In a normal 48h run, THROWBALL should be the most frequent action."""
        result = simulate(hours=48)
        assert result.survived
        counts = result.action_counts
        # THROWBALL should dominate normal play
        throwball_count = counts.get("THROWBALL", 0)
        shower_count = counts.get("SHOWER", 0)
        sleep_count = counts.get("SLEEP", 0)
        total = sum(counts.values())

        # Throwball should be > 30% of actions in normal play
        assert throwball_count / total > 0.3, (
            f"THROWBALL only {throwball_count}/{total} "
            f"({throwball_count/total:.0%}). Counts: {counts}"
        )
        # Shower should happen but not dominate
        assert shower_count > 0, f"No SHOWER actions in 48h! Counts: {counts}"
        # Sleep should happen occasionally
        assert sleep_count > 0, f"No SLEEP actions in 48h! Counts: {counts}"

    def test_multiple_seeds_all_survive(self):
        """Run with 10 different random seeds — all should survive 48h."""
        for seed in range(10):
            result = simulate(hours=48, seed=seed)
            assert result.survived, (
                f"Seed {seed}: died at tick {result.death_tick}! "
                f"Min stats: {result.min_stats}"
            )

    def test_simulation_prints_timeline(self, capsys):
        """Run and print a readable timeline (useful for manual inspection)."""
        result = simulate(hours=48)
        print(f"\n{'='*80}")
        print(f"48h Simulation: survived={result.survived}, ticks={result.ticks}")
        print(f"Action counts: {result.action_counts}")
        print(f"Tokens earned: {result.tokens_earned_approx:.1f}")
        print(f"Min stats: { {k: f'{v:.1f}' for k, v in result.min_stats.items()} }")
        print(f"\nTimeline (every ~70 min):")
        print(f"{'Tick':>5} {'Hour':>5} {'Hunger':>7} {'Health':>7} {'Energy':>7} {'Happy':>7} {'Hygiene':>7}")
        for snap in result.stats_timeline:
            tick = snap.pop("tick", 0)
            hour = tick * TICK_MINUTES / 60
            print(
                f"{tick:5d} {hour:5.1f}h "
                f"{snap['hunger']:7.1f} {snap['health']:7.1f} "
                f"{snap['energy']:7.1f} {snap['happiness']:7.1f} "
                f"{snap['hygiene']:7.1f}"
            )
        print(f"{'='*80}")
        assert result.survived


# ---------------------------------------------------------------------------
# 6. Consumable selector
# ---------------------------------------------------------------------------

class TestConsumableSelector:
    def test_best_food_priority(self):
        assert ConsumableSelector.get_best_food(["COOKIE", "BURGER"]) == "BURGER"
        assert ConsumableSelector.get_best_food(["SUSHI", "STEAK"]) == "SUSHI"

    def test_best_health_priority(self):
        assert ConsumableSelector.get_best_health_item(
            ["SMALL_POTION", "LARGE_POTION"]
        ) == "LARGE_POTION"

    def test_best_energy(self):
        assert ConsumableSelector.get_best_energy_item(["ENERGIZER"]) == "ENERGIZER"
        assert ConsumableSelector.get_best_energy_item(["BURGER"]) is None

    def test_no_consumables(self):
        assert ConsumableSelector.get_best_food([]) is None
        assert ConsumableSelector.get_any_consumable([]) is None


# ---------------------------------------------------------------------------
# 7. Action conditions
# ---------------------------------------------------------------------------

class TestActionConditions:
    def test_can_rub_below_threshold(self):
        ok, _ = ActionConditions.can_rub(PetStats(hygiene=50))
        assert ok

    def test_cannot_rub_above_threshold(self):
        ok, _ = ActionConditions.can_rub(PetStats(hygiene=80))
        assert not ok

    def test_can_throwball_when_one_stat_ok(self):
        ok, _ = ActionConditions.can_throwball(PetStats(health=5, hunger=5, energy=20))
        assert ok

    def test_cannot_throwball_all_below(self):
        ok, _ = ActionConditions.can_throwball(PetStats(health=5, hunger=5, energy=5))
        assert not ok

    def test_can_buy_with_tokens(self):
        ctx = _ctx(tokens=100)
        ok, _ = ActionConditions.can_buy_consumable(ctx)
        assert ok

    def test_cannot_buy_low_tokens(self):
        ctx = _ctx(tokens=10)
        ok, _ = ActionConditions.can_buy_consumable(ctx)
        assert not ok


# ---------------------------------------------------------------------------
# 8. Failure tracking
# ---------------------------------------------------------------------------

class TestFailureTracking:
    def setup_method(self):
        self.maker = PetDecisionMaker()

    def test_blocked_action_skipped(self):
        self.maker.record_action_failure(ActionType.THROWBALL, {})
        assert self.maker.is_action_blocked(ActionType.THROWBALL)

    def test_consumables_never_blocked(self):
        self.maker.record_action_failure(
            ActionType.CONSUMABLES_USE, {"consumable_id": "BURGER"}
        )
        assert not self.maker.is_action_blocked(ActionType.CONSUMABLES_USE)

    def test_clear_all(self):
        self.maker.record_action_failure(ActionType.THROWBALL, {})
        self.maker.clear_all_failures()
        assert not self.maker.is_action_blocked(ActionType.THROWBALL)


# ---------------------------------------------------------------------------
# 9. Always performs an action every 7 minutes — no stat combination skips
# ---------------------------------------------------------------------------

class TestAlwaysActsEvery7Min:
    """
    Guarantee: the decision engine ALWAYS returns a real, executable action
    (not NONE) for any living pet, regardless of stat values, sleeping state,
    blocked actions, consumable inventory, or token balance.

    This ensures the 7-minute action loop always has work to do.
    """

    def setup_method(self):
        self.maker = PetDecisionMaker()

    # -- All stats at extremes --

    def test_all_stats_100_still_acts(self):
        """Perfect pet with all stats maxed must still pick an action."""
        action, d = _decide(
            self.maker,
            hunger=100, health=100, energy=100, happiness=100, hygiene=100,
            tokens=0, consumables=[],
        )
        assert action != ActionType.NONE
        assert d.reason  # must have a reason

    def test_all_stats_0_still_acts(self):
        """Dying pet with all stats at 0 must still pick an action."""
        action, _ = _decide(
            self.maker,
            hunger=0, health=0, energy=0, happiness=0, hygiene=0,
            tokens=0, consumables=[],
        )
        assert action != ActionType.NONE

    def test_all_stats_at_critical_boundary(self):
        """All stats exactly at critical threshold (20) must still act."""
        action, _ = _decide(
            self.maker,
            hunger=20, health=20, energy=20, happiness=20, hygiene=20,
            tokens=0, consumables=[],
        )
        assert action != ActionType.NONE

    # -- Sleeping pet always gets a decision --

    def test_sleeping_low_energy_still_decides(self):
        """Sleeping pet with low energy should return SLEEP (stay/wake), not NONE."""
        action, _ = _decide(
            self.maker,
            hunger=50, health=50, energy=30, happiness=50, hygiene=50,
            sleeping=True,
        )
        assert action != ActionType.NONE

    def test_sleeping_high_energy_still_decides(self):
        """Sleeping pet with high energy (>= 65) should wake and pick an action."""
        action, _ = _decide(
            self.maker,
            hunger=50, health=50, energy=80, happiness=50, hygiene=50,
            sleeping=True,
        )
        assert action != ActionType.NONE

    def test_sleeping_zero_energy_no_onchain_needed(self):
        """Sleeping pet at 0 energy, all epoch actions done, still decides."""
        action, _ = _decide(
            self.maker,
            hunger=50, health=50, energy=0, happiness=50, hygiene=50,
            sleeping=True, epoch_actions=9,
        )
        assert action != ActionType.NONE

    def test_sleeping_zero_energy_needs_onchain(self):
        """Sleeping pet at 0 energy needing on-chain → wake+resleep."""
        action, d = _decide(
            self.maker,
            hunger=50, health=50, energy=0, happiness=50, hygiene=50,
            sleeping=True, epoch_actions=0,
        )
        assert action == ActionType.SLEEP
        assert d.params.get("wake_first") is True
        assert d.should_record_onchain is True

    def test_sleeping_critical_stats_wakes_for_survival(self):
        """Sleeping pet with critical non-energy stats should wake for survival."""
        action, d = _decide(
            self.maker,
            hunger=5, health=5, energy=10, happiness=5, hygiene=5,
            sleeping=True, consumables=["BURGER"],
        )
        # Should either stay asleep (energy critical) or wake for survival
        assert action != ActionType.NONE

    # -- All free actions blocked --

    def test_all_actions_blocked_still_acts(self):
        """Even with THROWBALL and RUB blocked, engine picks SHOWER or SLEEP."""
        self.maker.record_action_failure(ActionType.THROWBALL, {})
        self.maker.record_action_failure(ActionType.RUB, {})
        action, _ = _decide(
            self.maker,
            hunger=80, health=80, energy=60, happiness=80, hygiene=50,
            tokens=0, consumables=[],
        )
        assert action != ActionType.NONE
        assert action in (ActionType.SHOWER, ActionType.SLEEP)

    def test_all_blocked_high_hygiene_high_energy(self):
        """THROWBALL+RUB blocked, hygiene>=75 (no shower), energy>50 (no sleep score).
        Fallback SLEEP must kick in."""
        self.maker.record_action_failure(ActionType.THROWBALL, {})
        self.maker.record_action_failure(ActionType.RUB, {})
        action, _ = _decide(
            self.maker,
            hunger=80, health=80, energy=80, happiness=80, hygiene=80,
            tokens=0, consumables=[],
        )
        assert action != ActionType.NONE
        # Fallback SLEEP is the only option
        assert action == ActionType.SLEEP

    # -- No tokens, no consumables, various stats --

    def test_broke_pet_low_hunger_still_acts(self):
        """Low hunger, no food, no tokens → still picks a free action."""
        action, _ = _decide(
            self.maker,
            hunger=15, health=50, energy=50, happiness=50, hygiene=50,
            tokens=0, consumables=[],
        )
        assert action != ActionType.NONE

    def test_broke_pet_all_critical_still_acts(self):
        """All stats critical, no resources → picks SLEEP (free action)."""
        action, _ = _decide(
            self.maker,
            hunger=10, health=10, energy=10, happiness=10, hygiene=10,
            tokens=0, consumables=[],
        )
        assert action != ActionType.NONE

    # -- Exhaustive: every sleeping + stat combination still returns an action --

    def test_sleeping_never_none_grid(self):
        """Grid of energy levels while sleeping — must always return an action."""
        maker = PetDecisionMaker()
        for energy in [0, 5, 10, 20, 30, 50, 64, 65, 80, 100]:
            for hunger in [0, 20, 50, 100]:
                ctx = _ctx(
                    hunger=hunger, health=50, energy=energy,
                    happiness=50, hygiene=50, sleeping=True,
                )
                d = maker.decide(ctx)
                assert d.action != ActionType.NONE, (
                    f"Got NONE for sleeping pet: energy={energy}, hunger={hunger}"
                )

    # -- on-chain recording guarantee --

    def test_onchain_always_possible(self):
        """When epoch actions < 9, decision always sets should_record_onchain=True."""
        for energy in [0, 20, 50, 80, 100]:
            ctx = _ctx(
                hunger=50, health=50, energy=energy,
                happiness=50, hygiene=50, epoch_actions=0,
            )
            d = self.maker.decide(ctx)
            assert d.action != ActionType.NONE
            assert d.should_record_onchain is True, (
                f"energy={energy}: should_record_onchain should be True "
                f"when epoch_actions=0"
            )

    def test_stay_asleep_always_records_onchain(self):
        """stay_asleep=True always does wake+resleep with on-chain record."""
        ctx = _ctx(
            hunger=50, health=50, energy=30, happiness=50, hygiene=50,
            sleeping=True, epoch_actions=9,  # even when epoch is "done"
        )
        d = self.maker.decide(ctx)
        assert d.action == ActionType.SLEEP
        assert d.params.get("stay_asleep") is True
        assert d.should_record_onchain is True  # always on-chain

    # -- Large random sweep including sleeping --

    def test_random_1000_including_sleeping_never_none(self):
        """1000 random contexts (some sleeping) must all produce a real action."""
        rng = random.Random(123)
        maker = PetDecisionMaker()
        for _ in range(1000):
            ctx = _ctx(
                hunger=rng.uniform(0, 100),
                health=rng.uniform(0, 100),
                energy=rng.uniform(0, 100),
                happiness=rng.uniform(0, 100),
                hygiene=rng.uniform(0, 100),
                sleeping=rng.random() < 0.3,  # 30% chance sleeping
                tokens=rng.uniform(0, 500),
                consumables=rng.sample(
                    ["BURGER", "SMALL_POTION", "ENERGIZER", "SUSHI", "LARGE_POTION"],
                    k=rng.randint(0, 3),
                ),
                epoch_actions=rng.randint(0, 12),
            )
            d = maker.decide(ctx)
            assert d.action != ActionType.NONE, (
                f"Got NONE for living pet: "
                f"sleeping={ctx.is_sleeping}, "
                f"energy={ctx.stats.energy:.1f}, "
                f"hunger={ctx.stats.hunger:.1f}"
            )


# ---------------------------------------------------------------------------
# 10. Time-based simulation: on-chain action recorded every 7-minute tick
# ---------------------------------------------------------------------------

@dataclass
class OnchainSimResult:
    """Result of an on-chain guarantee simulation."""
    ticks: int
    survived: bool
    death_tick: Optional[int]
    # Every tick's record: (tick, action_name, should_record_onchain, is_sleeping)
    tick_log: List[Dict[str, Any]]
    # Ticks where should_record_onchain was False (should be empty)
    offchain_ticks: List[int]
    # Ticks where action was NONE (should be empty for living pet)
    none_ticks: List[int]
    action_counts: Dict[str, int]
    min_stats: Dict[str, float]


def simulate_onchain(
    hours: float = 48,
    initial_stats: Optional[PetStats] = None,
    initial_consumables: Optional[List[str]] = None,
    initial_tokens: float = 200.0,
    seed: int = 42,
) -> OnchainSimResult:
    """
    Simulate the pet and assert on-chain recording every 7-minute tick.

    Unlike the basic simulate(), this tracks:
      - Whether every tick produces a non-NONE action
      - Whether every tick has should_record_onchain=True
      - Proper stay_asleep handling (wake+resleep counts as action)
    """
    rng = random.Random(seed)
    maker = PetDecisionMaker()

    stats = initial_stats or PetStats(
        hunger=80, health=90, energy=70, happiness=75, hygiene=60
    )
    consumables = list(initial_consumables or ["BURGER", "BURGER", "SMALL_POTION"])
    tokens = initial_tokens
    is_sleeping = False

    total_ticks = int((hours * 60) / TICK_MINUTES)
    action_counts: Dict[str, int] = {}
    min_stats = stats.to_dict()
    death_tick = None
    tick_log: List[Dict[str, Any]] = []
    offchain_ticks: List[int] = []
    none_ticks: List[int] = []

    for tick in range(total_ticks):
        # --- Decay between ticks ---
        if is_sleeping:
            stats = _apply_sleep_recovery(stats, TICK_MINUTES)
        else:
            stats = _apply_decay(stats, TICK_MINUTES)

        # Track minimums
        for k, v in stats.to_dict().items():
            if v < min_stats[k]:
                min_stats[k] = v

        # --- Check death (all stats 0) ---
        if stats.is_all_zero():
            death_tick = tick
            return OnchainSimResult(
                ticks=tick,
                survived=False,
                death_tick=death_tick,
                tick_log=tick_log,
                offchain_ticks=offchain_ticks,
                none_ticks=none_ticks,
                action_counts=action_counts,
                min_stats=min_stats,
            )

        # --- Build context and decide ---
        ctx = PetContext(
            stats=stats,
            is_sleeping=is_sleeping,
            token_balance=tokens,
            owned_consumables=list(consumables),
            actions_recorded_this_epoch=tick % 9,
        )
        decision = maker.decide(ctx)
        action = decision.action

        # --- Record tick data ---
        tick_record = {
            "tick": tick,
            "action": action.name,
            "should_record_onchain": decision.should_record_onchain,
            "is_sleeping": is_sleeping,
            "stay_asleep": decision.params.get("stay_asleep", False),
            "wake_first": decision.params.get("wake_first", False),
            "stats": stats.to_dict(),
        }
        tick_log.append(tick_record)

        if action == ActionType.NONE:
            none_ticks.append(tick)
        if not decision.should_record_onchain:
            offchain_ticks.append(tick)

        # Track action counts
        name = action.name
        action_counts[name] = action_counts.get(name, 0) + 1

        # --- Handle sleep toggle (mirrors _execute_decision logic) ---
        if action == ActionType.SLEEP:
            if decision.params.get("stay_asleep"):
                # In production: wake+resleep for on-chain record, end sleeping
                is_sleeping = False
                is_sleeping = True  # re-sleep
            elif decision.params.get("wake_first"):
                is_sleeping = False
                is_sleeping = True
            else:
                is_sleeping = not is_sleeping
        else:
            if is_sleeping:
                is_sleeping = False

        # --- Apply action effects ---
        stats = _apply_action_effects(action, stats, decision.params)

        # --- Simulate consumable usage ---
        if action == ActionType.CONSUMABLES_USE:
            cid = decision.params.get("consumable_id", "").upper()
            if cid in [c.upper() for c in consumables]:
                for i, c in enumerate(consumables):
                    if c.upper() == cid:
                        consumables.pop(i)
                        break
            elif "auto_buy" in decision.params.get("action", ""):
                tokens = max(0, tokens - 50)
        elif action == ActionType.CONSUMABLES_BUY:
            cid = decision.params.get("consumable_id", "BURGER")
            consumables.append(cid)
            tokens = max(0, tokens - 50)

        # --- Simulate token earnings ---
        if action == ActionType.THROWBALL and rng.random() < 0.6:
            earned = rng.uniform(1, 2)
            tokens += earned
        elif action == ActionType.SHOWER:
            earned = rng.uniform(0.5, 2.0)
            tokens += earned

        # --- Replenish consumable inventory periodically ---
        if tick % 60 == 0 and tick > 0 and tokens > 150:
            consumables.extend(["BURGER", "SMALL_POTION"])
            tokens -= 100

    return OnchainSimResult(
        ticks=total_ticks,
        survived=True,
        death_tick=None,
        tick_log=tick_log,
        offchain_ticks=offchain_ticks,
        none_ticks=none_ticks,
        action_counts=action_counts,
        min_stats=min_stats,
    )


class TestAlwaysOnchainEvery7Min:
    """
    Time-based simulations that verify EVERY 7-minute tick produces a real
    on-chain action across many different starting stat combinations.
    """

    def _assert_all_onchain(self, result: OnchainSimResult, label: str):
        """Common assertions for every simulation."""
        assert result.survived, (
            f"[{label}] Pet died at tick {result.death_tick}! "
            f"Min stats: {result.min_stats}"
        )
        assert result.none_ticks == [], (
            f"[{label}] Got NONE action at ticks: {result.none_ticks[:10]}... "
            f"(total {len(result.none_ticks)}/{result.ticks})"
        )
        assert result.offchain_ticks == [], (
            f"[{label}] Off-chain (not recorded) at ticks: "
            f"{result.offchain_ticks[:10]}... "
            f"(total {len(result.offchain_ticks)}/{result.ticks})"
        )

    # -- Standard 48h runs with different starting stats --

    def test_48h_default_start_all_onchain(self):
        result = simulate_onchain(hours=48)
        self._assert_all_onchain(result, "default")

    def test_48h_all_stats_100(self):
        result = simulate_onchain(
            hours=48,
            initial_stats=PetStats(
                hunger=100, health=100, energy=100, happiness=100, hygiene=100
            ),
            initial_consumables=[],
            initial_tokens=0,
        )
        self._assert_all_onchain(result, "all-100")

    def test_48h_all_stats_50(self):
        result = simulate_onchain(
            hours=48,
            initial_stats=PetStats(
                hunger=50, health=50, energy=50, happiness=50, hygiene=50
            ),
            initial_consumables=[],
            initial_tokens=0,
        )
        self._assert_all_onchain(result, "all-50")

    def test_48h_all_stats_25(self):
        result = simulate_onchain(
            hours=48,
            initial_stats=PetStats(
                hunger=25, health=25, energy=25, happiness=25, hygiene=25
            ),
            initial_consumables=["BURGER", "BURGER", "SMALL_POTION", "ENERGIZER"],
            initial_tokens=300,
        )
        self._assert_all_onchain(result, "all-25")

    def test_24h_all_stats_5(self):
        """Near-death start — every tick must still be on-chain."""
        result = simulate_onchain(
            hours=24,
            initial_stats=PetStats(
                hunger=5, health=5, energy=5, happiness=5, hygiene=5
            ),
            initial_consumables=[
                "BURGER", "BURGER", "BURGER",
                "SMALL_POTION", "SMALL_POTION",
                "ENERGIZER", "ENERGIZER",
            ],
            initial_tokens=500,
        )
        self._assert_all_onchain(result, "all-5")

    def test_48h_no_consumables_no_tokens(self):
        """Broke pet — only free actions, every tick must still be on-chain."""
        result = simulate_onchain(
            hours=48,
            initial_consumables=[],
            initial_tokens=0,
        )
        self._assert_all_onchain(result, "broke")

    # -- Asymmetric starting stats --

    def test_48h_high_energy_low_everything(self):
        result = simulate_onchain(
            hours=48,
            initial_stats=PetStats(
                hunger=10, health=10, energy=100, happiness=10, hygiene=10
            ),
            initial_consumables=["BURGER", "SMALL_POTION"],
            initial_tokens=200,
        )
        self._assert_all_onchain(result, "high-energy-low-rest")

    def test_48h_low_energy_high_everything(self):
        result = simulate_onchain(
            hours=48,
            initial_stats=PetStats(
                hunger=90, health=90, energy=5, happiness=90, hygiene=90
            ),
            initial_consumables=["ENERGIZER"],
            initial_tokens=100,
        )
        self._assert_all_onchain(result, "low-energy-high-rest")

    def test_48h_only_hygiene_critical(self):
        result = simulate_onchain(
            hours=48,
            initial_stats=PetStats(
                hunger=80, health=80, energy=80, happiness=80, hygiene=5
            ),
            initial_consumables=[],
            initial_tokens=0,
        )
        self._assert_all_onchain(result, "hygiene-critical")

    def test_48h_only_hunger_critical(self):
        result = simulate_onchain(
            hours=48,
            initial_stats=PetStats(
                hunger=5, health=80, energy=80, happiness=80, hygiene=80
            ),
            initial_consumables=["BURGER", "BURGER"],
            initial_tokens=200,
        )
        self._assert_all_onchain(result, "hunger-critical")

    # -- Multiple seeds to catch edge cases --

    def test_10_seeds_all_onchain(self):
        """10 random seeds x default start — every tick on-chain."""
        for seed in range(10):
            result = simulate_onchain(hours=48, seed=seed)
            self._assert_all_onchain(result, f"seed-{seed}")

    def test_10_seeds_broke_all_onchain(self):
        """10 random seeds x broke start — every tick on-chain."""
        for seed in range(10):
            result = simulate_onchain(
                hours=48,
                initial_consumables=[],
                initial_tokens=0,
                seed=seed,
            )
            self._assert_all_onchain(result, f"broke-seed-{seed}")

    # -- Random starting stats sweep --

    def test_20_random_starting_stats_all_onchain(self):
        """20 random starting stat combos, each simulated 48h."""
        rng = random.Random(999)
        for i in range(20):
            stats = PetStats(
                hunger=rng.uniform(0, 100),
                health=rng.uniform(0, 100),
                energy=rng.uniform(0, 100),
                happiness=rng.uniform(0, 100),
                hygiene=rng.uniform(0, 100),
            )
            tokens = rng.uniform(0, 500)
            consumables = rng.sample(
                ["BURGER", "SMALL_POTION", "ENERGIZER", "SUSHI", "LARGE_POTION"],
                k=rng.randint(0, 5),
            )
            result = simulate_onchain(
                hours=48,
                initial_stats=stats,
                initial_consumables=consumables,
                initial_tokens=tokens,
                seed=i,
            )
            self._assert_all_onchain(
                result,
                f"random-{i} (h={stats.hunger:.0f} hp={stats.health:.0f} "
                f"e={stats.energy:.0f} hap={stats.happiness:.0f} "
                f"hyg={stats.hygiene:.0f})",
            )
