"""
Pet Decision Maker - Urgency-scoring decision logic for pet actions.

Replaces the old priority-based if/else logic with a weighted urgency scoring
system that picks the highest-scoring action each tick.

Decision flow:
  1. Calculate urgency scores per stat (weighted by decay rate)
  2. Critical state check (survival mode) — any stat <= 20
  3. Score every available action and pick the highest
  4. Return ActionDecision + AgentDecisionLog for UI display
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
)
import logging

try:
    from .constants import REQUIRED_ACTIONS_PER_EPOCH
except ImportError:
    REQUIRED_ACTIONS_PER_EPOCH = 9

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & core data classes
# ---------------------------------------------------------------------------

class ActionType(Enum):
    """All possible pet actions."""
    SLEEP = auto()
    SHOWER = auto()
    RUB = auto()
    THROWBALL = auto()
    CONSUMABLES_USE = auto()
    CONSUMABLES_BUY = auto()
    NONE = auto()


@dataclass
class PetStats:
    """Current pet statistics (all values 0-100)."""
    hunger: float = 0.0
    health: float = 0.0
    energy: float = 0.0
    happiness: float = 0.0
    hygiene: float = 0.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PetStats":
        def to_float(v: Any) -> float:
            if v is None:
                return 0.0
            try:
                return float(str(v))
            except (ValueError, TypeError):
                return 0.0

        return cls(
            hunger=to_float(data.get("hunger", 0)),
            health=to_float(data.get("health", 0)),
            energy=to_float(data.get("energy", 0)),
            happiness=to_float(data.get("happiness", 0)),
            hygiene=to_float(data.get("hygiene", 0)),
        )

    def to_dict(self) -> Dict[str, float]:
        return {
            "hunger": self.hunger,
            "health": self.health,
            "energy": self.energy,
            "happiness": self.happiness,
            "hygiene": self.hygiene,
        }

    def is_all_zero(self) -> bool:
        return all(
            v <= 0.0
            for v in [self.hunger, self.health, self.energy, self.happiness, self.hygiene]
        )

    def is_all_full(self) -> bool:
        return all(
            v >= 100.0
            for v in [self.hunger, self.health, self.energy, self.happiness, self.hygiene]
        )

    def is_critical(self, threshold: float = 5.0) -> bool:
        """Check if all core stats are below critical threshold."""
        return all(
            v < threshold
            for v in [self.hunger, self.health, self.hygiene, self.happiness]
        )


@dataclass
class PetContext:
    """Full context for making decisions."""
    stats: PetStats
    is_sleeping: bool = False
    is_dead: bool = False
    token_balance: float = 0.0
    owned_consumables: List[str] = field(default_factory=list)
    actions_recorded_this_epoch: int = 0
    required_actions_per_epoch: int = REQUIRED_ACTIONS_PER_EPOCH
    level: int = 1

    @property
    def needs_more_onchain_actions(self) -> bool:
        return self.actions_recorded_this_epoch < self.required_actions_per_epoch

    @property
    def remaining_required_actions(self) -> int:
        return max(0, self.required_actions_per_epoch - self.actions_recorded_this_epoch)


@dataclass
class ActionDecision:
    """Result of the decision-making process."""
    action: ActionType
    reason: str
    should_record_onchain: bool
    params: Dict[str, Any] = field(default_factory=dict)
    fallback_from: Optional[ActionType] = None
    stats_snapshot: Optional[Dict[str, float]] = None
    # Populated by the decision engine for UI display
    decision_log: Optional["AgentDecisionLog"] = None

    def __str__(self) -> str:
        onchain_str = "ON-CHAIN" if self.should_record_onchain else "OFF-CHAIN"
        fallback_str = (
            f" (fallback from {self.fallback_from.name})" if self.fallback_from else ""
        )
        return f"{onchain_str} {self.action.name}{fallback_str}: {self.reason}"


@dataclass
class AgentDecisionLog:
    """Structured decision summary for UI display in the AI action history feed."""
    action: str
    reasoning: str

    stats_before: Dict[str, float]
    urgency_scores: Dict[str, float]
    alternatives_considered: List[Dict[str, Any]]  # [{action, score}]
    mode: str  # 'survival' | 'normal' | 'farming'

    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "reasoning": self.reasoning,
            "statsBefore": self.stats_before,
            "urgencyScores": self.urgency_scores,
            "alternativesConsidered": self.alternatives_considered,
            "mode": self.mode,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Failure tracking
# ---------------------------------------------------------------------------

@dataclass
class FailedAction:
    """Tracks a failed action to prevent infinite retry loops."""
    action: ActionType
    params: Dict[str, Any]
    failed_at: datetime
    reason: str = ""

    COOLDOWN_SECONDS: int = 300

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now()
        return (now - self.failed_at).total_seconds() > self.COOLDOWN_SECONDS

    def matches(self, action: ActionType, params: Dict[str, Any]) -> bool:
        if self.action != action:
            return False
        if action in (ActionType.CONSUMABLES_USE, ActionType.CONSUMABLES_BUY):
            return self.params.get("consumable_id") == params.get("consumable_id")
        return True


# ---------------------------------------------------------------------------
# Consumable selector (unchanged logic)
# ---------------------------------------------------------------------------

class ConsumableSelector:
    """Selects the best consumable based on priority/effectiveness."""

    FOOD_PRIORITY = ["SUSHI", "STEAK", "PIZZA", "BURGER", "SALAD", "COOKIE"]
    HEALTH_PRIORITY = ["LARGE_POTION", "POTION", "SMALL_POTION", "SALAD"]
    ENERGY_PRIORITY = ["ENERGIZER"]

    ALL_FOOD = {"SUSHI", "STEAK", "PIZZA", "BURGER", "SALAD", "COOKIE"}
    ALL_HEALTH = {"LARGE_POTION", "POTION", "SMALL_POTION", "SALAD"}
    ALL_ENERGY = {"ENERGIZER"}

    @classmethod
    def _best_from_priority(cls, priority_list: List[str], owned: List[str]) -> Optional[str]:
        owned_upper = [c.upper() for c in owned]
        for item in priority_list:
            if item in owned_upper:
                idx = owned_upper.index(item)
                return owned[idx]
        return None

    @classmethod
    def get_best_food(cls, owned_consumables: List[str]) -> Optional[str]:
        return cls._best_from_priority(cls.FOOD_PRIORITY, owned_consumables)

    @classmethod
    def get_best_health_item(cls, owned_consumables: List[str]) -> Optional[str]:
        return cls._best_from_priority(cls.HEALTH_PRIORITY, owned_consumables)

    @classmethod
    def get_best_energy_item(cls, owned_consumables: List[str]) -> Optional[str]:
        return cls._best_from_priority(cls.ENERGY_PRIORITY, owned_consumables)

    @classmethod
    def get_any_consumable(cls, owned_consumables: List[str]) -> Optional[str]:
        food = cls.get_best_food(owned_consumables)
        if food:
            return food
        health = cls.get_best_health_item(owned_consumables)
        if health:
            return health
        energy = cls.get_best_energy_item(owned_consumables)
        if energy:
            return energy
        return owned_consumables[0] if owned_consumables else None

    @classmethod
    def has_food(cls, owned_consumables: List[str]) -> bool:
        owned_upper = {c.upper() for c in owned_consumables}
        return bool(owned_upper & cls.ALL_FOOD)

    @classmethod
    def has_health_item(cls, owned_consumables: List[str]) -> bool:
        owned_upper = {c.upper() for c in owned_consumables}
        return bool(owned_upper & cls.ALL_HEALTH)

    @classmethod
    def has_energy_item(cls, owned_consumables: List[str]) -> bool:
        owned_upper = {c.upper() for c in owned_consumables}
        return bool(owned_upper & cls.ALL_ENERGY)

    @classmethod
    def get_best_to_buy_for_hunger(cls) -> str:
        return "BURGER"

    @classmethod
    def get_best_to_buy_for_health(cls) -> str:
        return "SMALL_POTION"


# ---------------------------------------------------------------------------
# Action conditions (prerequisite validation)
# ---------------------------------------------------------------------------

class ActionConditions:
    """Prerequisite checks for each action."""
    HYGIENE_THRESHOLD = 75.0
    THROWBALL_MIN_STAT = 15.0
    MIN_TOKENS_FOR_BUY = 50.0

    @classmethod
    def can_rub(cls, stats: PetStats) -> Tuple[bool, str]:
        if stats.hygiene < cls.HYGIENE_THRESHOLD:
            return True, f"hygiene ({stats.hygiene:.1f}) < {cls.HYGIENE_THRESHOLD}"
        return False, f"hygiene ({stats.hygiene:.1f}) >= {cls.HYGIENE_THRESHOLD}"

    @classmethod
    def can_shower(cls, stats: PetStats) -> Tuple[bool, str]:
        if stats.hygiene < cls.HYGIENE_THRESHOLD:
            return True, f"hygiene ({stats.hygiene:.1f}) < {cls.HYGIENE_THRESHOLD}"
        return False, f"hygiene ({stats.hygiene:.1f}) >= {cls.HYGIENE_THRESHOLD}"

    @classmethod
    def can_sleep(cls, stats: PetStats) -> Tuple[bool, str]:
        return True, "sleep is always possible"

    @classmethod
    def can_throwball(cls, stats: PetStats) -> Tuple[bool, str]:
        below = (
            stats.health < cls.THROWBALL_MIN_STAT
            and stats.hunger < cls.THROWBALL_MIN_STAT
            and stats.energy < cls.THROWBALL_MIN_STAT
        )
        if not below:
            return True, f"at least one stat (health/hunger/energy) >= {cls.THROWBALL_MIN_STAT}"
        return False, f"all core stats below {cls.THROWBALL_MIN_STAT}"

    @classmethod
    def can_use_consumable(cls, context: PetContext) -> Tuple[bool, str]:
        if context.owned_consumables and len(context.owned_consumables) > 0:
            return True, f"owns {len(context.owned_consumables)} consumable(s)"
        return False, "no consumables owned"

    @classmethod
    def can_buy_consumable(cls, context: PetContext) -> Tuple[bool, str]:
        if context.token_balance >= cls.MIN_TOKENS_FOR_BUY:
            return True, f"balance ({context.token_balance:.2f}) >= {cls.MIN_TOKENS_FOR_BUY}"
        return False, f"balance ({context.token_balance:.2f}) < {cls.MIN_TOKENS_FOR_BUY}"

    @classmethod
    def get_all_possible_actions(cls, context: PetContext) -> List[Tuple[ActionType, str]]:
        possible = []
        can, reason = cls.can_sleep(context.stats)
        if can:
            possible.append((ActionType.SLEEP, reason))
        can, reason = cls.can_rub(context.stats)
        if can:
            possible.append((ActionType.RUB, reason))
        can, reason = cls.can_shower(context.stats)
        if can:
            possible.append((ActionType.SHOWER, reason))
        can, reason = cls.can_throwball(context.stats)
        if can:
            possible.append((ActionType.THROWBALL, reason))
        can, reason = cls.can_use_consumable(context)
        if can:
            possible.append((ActionType.CONSUMABLES_USE, reason))
        can, reason = cls.can_buy_consumable(context)
        if can:
            possible.append((ActionType.CONSUMABLES_BUY, reason))
        return possible


# ---------------------------------------------------------------------------
# Urgency scoring helpers
# ---------------------------------------------------------------------------

# Decay weights — faster-draining stats get higher weight
_DECAY_WEIGHTS: Dict[str, float] = {
    "hygiene": 1.5,
    "happiness": 1.2,
    "energy": 1.1,
    "hunger": 1.0,
    "health": 0.8,
}

# Thresholds below which each stat is "relevant" for urgency
_URGENCY_THRESHOLDS: Dict[str, float] = {
    "energy": 15.0,
    "hunger": 15.0,
    "health": 15.0,
    "hygiene": 75.0,
    "happiness": 90.0,
}

# Stat decay per minute (used for anticipatory buy logic)
_DECAY_PER_MINUTE: Dict[str, float] = {
    "hygiene": 0.03704,
    "happiness": 0.03032,
    "energy": 0.02776,
    "hunger": 0.02084,
    "health": 0.01388,
}

CRITICAL_THRESHOLD = 20.0
WAKE_ENERGY_THRESHOLD = 65.0


def compute_urgency_scores(stats: PetStats) -> Dict[str, float]:
    """Compute urgency score (0-100+) for each stat, weighted by decay rate."""
    scores: Dict[str, float] = {}
    stats_dict = stats.to_dict()
    for stat_name, threshold in _URGENCY_THRESHOLDS.items():
        value = stats_dict.get(stat_name, 100.0)
        raw = max(0.0, (1.0 - value / threshold) * 100.0)
        scores[stat_name] = round(raw * _DECAY_WEIGHTS.get(stat_name, 1.0), 2)
    return scores


def _stat_will_be_critical_in(stats: PetStats, stat_name: str, minutes: float) -> bool:
    """Predict if a stat will drop below CRITICAL_THRESHOLD within `minutes`."""
    current = getattr(stats, stat_name, 100.0)
    decay = _DECAY_PER_MINUTE.get(stat_name, 0.0) * minutes
    return (current - decay) <= CRITICAL_THRESHOLD


# ---------------------------------------------------------------------------
# Action scoring
# ---------------------------------------------------------------------------

_IMPOSSIBLE = -999.0


def _score_throwball(stats: PetStats, context: PetContext, blocked: bool) -> float:
    if blocked:
        return _IMPOSSIBLE
    can, _ = ActionConditions.can_throwball(stats)
    if not can:
        return _IMPOSSIBLE

    score = 40.0  # base — primary token earner
    if stats.energy < 70:
        score += 20.0  # token-eligible
    if stats.happiness < 50:
        score += 15.0  # needs happiness
    if stats.energy < 30:
        score -= 30.0  # too risky
    if stats.hunger < 30:
        score -= 30.0  # too risky
    if stats.hygiene < 20:
        score -= 20.0  # will push to critical
    return score


def _score_shower(stats: PetStats) -> float:
    can, _ = ActionConditions.can_shower(stats)
    if not can:
        return _IMPOSSIBLE

    score = 10.0  # always good for tokens/xp when available
    if stats.hygiene <= 25:
        score += 60.0  # critically dirty — must shower before it blocks play
    elif stats.hygiene < 40:
        score += 50.0  # urgently dirty
    elif stats.hygiene < 60:
        score += 35.0  # moderately dirty
    elif stats.hygiene < 75:
        score += 20.0  # available
    return score


def _score_rub(stats: PetStats, blocked: bool) -> float:
    if blocked:
        return _IMPOSSIBLE
    can, _ = ActionConditions.can_rub(stats)
    if not can:
        return _IMPOSSIBLE

    score = 15.0
    if stats.happiness < 50:
        score += 10.0
    return score


def _score_sleep(stats: PetStats) -> float:
    if stats.energy < 20:
        return 60.0  # critical
    if stats.energy < 35:
        return 30.0  # low
    if stats.energy > 50:
        return _IMPOSSIBLE  # wasteful
    return 0.0


def _score_consume(stats: PetStats, context: PetContext) -> Tuple[float, Optional[str]]:
    """Score CONSUME action and return (score, consumable_id or None)."""
    if not context.owned_consumables:
        return _IMPOSSIBLE, None

    # Check for critical needs
    best = None
    score = -999.0

    # Critical hunger
    if stats.hunger <= CRITICAL_THRESHOLD:
        food = ConsumableSelector.get_best_food(context.owned_consumables)
        if food:
            return 70.0, food

    # Critical health
    if stats.health <= CRITICAL_THRESHOLD:
        health_item = ConsumableSelector.get_best_health_item(context.owned_consumables)
        if health_item:
            return 70.0, health_item

    # Critical energy
    if stats.energy <= CRITICAL_THRESHOLD:
        energy_item = ConsumableSelector.get_best_energy_item(context.owned_consumables)
        if energy_item:
            return 70.0, energy_item

    # Non-critical but low stat
    if stats.hunger < 40:
        food = ConsumableSelector.get_best_food(context.owned_consumables)
        if food and (30.0 > score):
            score, best = 30.0, food
    if stats.health < 40:
        health_item = ConsumableSelector.get_best_health_item(context.owned_consumables)
        if health_item and (30.0 > score):
            score, best = 30.0, health_item
    if stats.energy < 40:
        energy_item = ConsumableSelector.get_best_energy_item(context.owned_consumables)
        if energy_item and (30.0 > score):
            score, best = 30.0, energy_item

    if best:
        return score, best
    return _IMPOSSIBLE, None


def _score_buy(stats: PetStats, context: PetContext) -> Tuple[float, Optional[str]]:
    """Score BUY action and return (score, consumable_id_to_buy or None)."""
    can_buy, _ = ActionConditions.can_buy_consumable(context)
    if not can_buy:
        return _IMPOSSIBLE, None

    # Buy if anticipating critical state in ~30 min
    for stat_name, buy_fn in [
        ("hunger", ConsumableSelector.get_best_to_buy_for_hunger),
        ("health", ConsumableSelector.get_best_to_buy_for_health),
    ]:
        if _stat_will_be_critical_in(stats, stat_name, 30.0):
            return 15.0, buy_fn()

    # Buy if flush with tokens and no consumables
    if not context.owned_consumables and context.token_balance >= ActionConditions.MIN_TOKENS_FOR_BUY * 2:
        return 25.0, ConsumableSelector.get_best_to_buy_for_hunger()

    return _IMPOSSIBLE, None


# ---------------------------------------------------------------------------
# Main decision maker
# ---------------------------------------------------------------------------

class PetDecisionMaker:
    """
    Urgency-scoring decision engine.

    Decision flow:
      0. Dead pet → NONE
      0.5. Sleeping management (wake-and-resleep / stay asleep)
      1. Critical survival check (any stat <= 20)
      2. Score all available actions → pick the highest
      3. Attach AgentDecisionLog for UI
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        self._last_decision: Optional[ActionDecision] = None
        self._decision_history: List[ActionDecision] = []
        self._failed_actions: List[FailedAction] = []

    # ---- public API ---------------------------------------------------------

    def decide(self, context: PetContext) -> ActionDecision:
        stats = context.stats
        # Always record on-chain every 7-minute tick — not just until the
        # epoch minimum is reached.  This guarantees continuous on-chain
        # activity regardless of pet stats or epoch progress.
        should_record = True

        self._log_context(context)
        urgency = compute_urgency_scores(stats)

        # --- Dead pet ---
        if context.is_dead:
            return self._finalize(
                ActionDecision(
                    action=ActionType.NONE,
                    reason="Pet is dead - no actions possible",
                    should_record_onchain=False,
                    stats_snapshot=stats.to_dict(),
                ),
                urgency, [], "survival",
            )

        # --- Sleeping management ---
        # Only keep sleeping if other stats are healthy enough.
        # When health, hunger, or happiness are critical the pet MUST wake
        # and use consumables (survival mode) instead of continuing to sleep.
        _other_stats_ok = (
            stats.health > CRITICAL_THRESHOLD
            and stats.hunger > CRITICAL_THRESHOLD
            and stats.happiness > CRITICAL_THRESHOLD
        )

        if context.is_sleeping and stats.energy <= 0 and should_record and _other_stats_ok:
            return self._finalize(
                ActionDecision(
                    action=ActionType.SLEEP,
                    reason="Sleeping with 0 energy - wake and re-sleep for on-chain record",
                    should_record_onchain=True,
                    params={"wake_first": True},
                    stats_snapshot=stats.to_dict(),
                ),
                urgency, [], "survival",
            )

        if context.is_sleeping and stats.energy < WAKE_ENERGY_THRESHOLD and _other_stats_ok:
            if not stats.is_critical(5.0):
                return self._finalize(
                    ActionDecision(
                        action=ActionType.SLEEP,
                        reason=f"Still resting - energy ({stats.energy:.1f}) < {WAKE_ENERGY_THRESHOLD} - wake+resleep for on-chain record",
                        should_record_onchain=True,
                        params={"stay_asleep": True},
                        stats_snapshot=stats.to_dict(),
                    ),
                    urgency, [], "normal",
                )

        # --- Step 1: Critical survival check (any stat <= 20) ---
        survival_decision = self._check_survival(context, stats, should_record, urgency)
        if survival_decision is not None:
            return survival_decision

        # --- Step 2: Score all actions and pick the best ---
        return self._score_and_pick(context, stats, should_record, urgency)

    # ---- survival mode ------------------------------------------------------

    def _check_survival(
        self,
        context: PetContext,
        stats: PetStats,
        should_record: bool,
        urgency: Dict[str, float],
    ) -> Optional[ActionDecision]:
        """If any stat is <= CRITICAL_THRESHOLD, enter survival mode.

        Priority order: health > hunger > energy.
        Health and hunger don't recover passively — they MUST be addressed
        with consumables.  Energy recovers by sleeping, so it's last.
        """
        can_buy, _ = ActionConditions.can_buy_consumable(context)

        # --- Health critical (top priority — pet can die) ---
        if stats.health <= CRITICAL_THRESHOLD:
            potion = ConsumableSelector.get_best_health_item(context.owned_consumables)
            if potion:
                return self._finalize(
                    ActionDecision(
                        action=ActionType.CONSUMABLES_USE,
                        reason=f"SURVIVAL: health critically low ({stats.health:.1f}) - using {potion}",
                        should_record_onchain=should_record,
                        params={"consumable_id": potion, "action": "survival_health"},
                        stats_snapshot=stats.to_dict(),
                    ),
                    urgency, [], "survival",
                )
            # No potion owned — try to buy.  Even if the decision engine's
            # balance threshold says "can't buy", the execution layer
            # (use_consumable auto-buy / _recover_low_health) may still
            # succeed with cheaper items.  Always attempt when health is
            # critical to avoid letting the pet die.
            potion_to_buy = ConsumableSelector.get_best_to_buy_for_health()
            return self._finalize(
                ActionDecision(
                    action=ActionType.CONSUMABLES_USE,
                    reason=f"SURVIVAL: health critically low ({stats.health:.1f}) - auto-buying {potion_to_buy}",
                    should_record_onchain=should_record,
                    params={"consumable_id": potion_to_buy, "action": "auto_buy_and_use_critical_consumable"},
                    stats_snapshot=stats.to_dict(),
                ),
                urgency, [], "survival",
            )

        # --- Hunger critical ---
        if stats.hunger <= CRITICAL_THRESHOLD:
            food = ConsumableSelector.get_best_food(context.owned_consumables)
            if food:
                return self._finalize(
                    ActionDecision(
                        action=ActionType.CONSUMABLES_USE,
                        reason=f"SURVIVAL: hunger critically low ({stats.hunger:.1f}) - feeding {food}",
                        should_record_onchain=should_record,
                        params={"consumable_id": food, "action": "survival_hunger"},
                        stats_snapshot=stats.to_dict(),
                    ),
                    urgency, [], "survival",
                )
            # Same as health: always attempt buy when hunger is critical.
            food_to_buy = ConsumableSelector.get_best_to_buy_for_hunger()
            return self._finalize(
                ActionDecision(
                    action=ActionType.CONSUMABLES_USE,
                    reason=f"SURVIVAL: hunger critically low ({stats.hunger:.1f}) - auto-buying {food_to_buy}",
                    should_record_onchain=should_record,
                    params={"consumable_id": food_to_buy, "action": "auto_buy_and_use_critical_consumable"},
                    stats_snapshot=stats.to_dict(),
                ),
                urgency, [], "survival",
            )

        # --- Energy critical (lowest priority — recovers by sleeping) ---
        if stats.energy <= CRITICAL_THRESHOLD:
            energizer = ConsumableSelector.get_best_energy_item(context.owned_consumables)
            if energizer:
                return self._finalize(
                    ActionDecision(
                        action=ActionType.CONSUMABLES_USE,
                        reason=f"SURVIVAL: energy critically low ({stats.energy:.1f}) - using {energizer}",
                        should_record_onchain=should_record,
                        params={"consumable_id": energizer, "action": "survival_energy"},
                        stats_snapshot=stats.to_dict(),
                    ),
                    urgency, [], "survival",
                )
            # No energizer → SLEEP (energy recovers passively)
            return self._finalize(
                ActionDecision(
                    action=ActionType.SLEEP,
                    reason=f"SURVIVAL: energy critically low ({stats.energy:.1f}) - sleeping",
                    should_record_onchain=should_record,
                    stats_snapshot=stats.to_dict(),
                ),
                urgency, [], "survival",
            )

        # No critical stats
        return None

    # ---- scoring mode -------------------------------------------------------

    def _score_and_pick(
        self,
        context: PetContext,
        stats: PetStats,
        should_record: bool,
        urgency: Dict[str, float],
    ) -> ActionDecision:
        """Score all available actions and pick the highest."""

        throwball_blocked = self.is_action_blocked(ActionType.THROWBALL)
        rub_blocked = self.is_action_blocked(ActionType.RUB)

        scored: List[Tuple[str, float, ActionType, Dict[str, Any]]] = []

        # THROW_BALL
        tb_score = _score_throwball(stats, context, throwball_blocked)
        scored.append(("THROWBALL", tb_score, ActionType.THROWBALL, {}))

        # SHOWER
        shower_score = _score_shower(stats)
        scored.append(("SHOWER", shower_score, ActionType.SHOWER, {}))

        # RUB
        rub_score = _score_rub(stats, rub_blocked)
        scored.append(("RUB", rub_score, ActionType.RUB, {}))

        # SLEEP
        sleep_score = _score_sleep(stats)
        scored.append(("SLEEP", sleep_score, ActionType.SLEEP, {}))

        # CONSUME
        consume_score, consume_id = _score_consume(stats, context)
        if consume_id:
            scored.append(("CONSUME", consume_score, ActionType.CONSUMABLES_USE, {"consumable_id": consume_id}))

        # BUY
        buy_score, buy_id = _score_buy(stats, context)
        if buy_id:
            scored.append(("BUY", buy_score, ActionType.CONSUMABLES_BUY, {"consumable_id": buy_id, "amount": 1}))

        # Sort descending by score
        scored.sort(key=lambda x: x[1], reverse=True)

        # Build alternatives list for UI
        alternatives = [
            {"action": name, "score": round(s, 1)}
            for name, s, _, _ in scored
            if s > _IMPOSSIBLE
        ]

        # Pick the best valid action
        for name, score, action_type, params in scored:
            if score <= _IMPOSSIBLE:
                continue

            # Determine reasoning
            reason = self._build_reason(name, stats, score)

            decision = ActionDecision(
                action=action_type,
                reason=reason,
                should_record_onchain=should_record,
                params=params,
                stats_snapshot=stats.to_dict(),
            )

            # Determine mode
            mode = "farming" if name == "THROWBALL" and stats.energy < 70 else "normal"

            return self._finalize(decision, urgency, alternatives, mode)

        # Absolute fallback: SLEEP (always valid)
        self.logger.warning("All action scores <= -999; falling back to SLEEP")
        return self._finalize(
            ActionDecision(
                action=ActionType.SLEEP,
                reason="No viable action available - sleeping as fallback",
                should_record_onchain=should_record,
                stats_snapshot=stats.to_dict(),
            ),
            urgency, alternatives, "survival",
        )

    def _build_reason(self, action_name: str, stats: PetStats, score: float) -> str:
        """Build a concise human-readable reason string."""
        if action_name == "THROWBALL":
            parts = [f"score={score:.0f}"]
            if stats.energy < 70:
                parts.append("token-eligible")
            if stats.happiness < 50:
                parts.append("needs happiness")
            return f"Throwing ball to earn tokens ({', '.join(parts)})"
        elif action_name == "SHOWER":
            return (
                f"Hygiene at {stats.hygiene:.0f}% - showering for big hygiene "
                f"restore + tokens/XP (score={score:.0f})"
            )
        elif action_name == "RUB":
            return f"Rubbing pet - hygiene at {stats.hygiene:.0f}% (score={score:.0f})"
        elif action_name == "SLEEP":
            return f"Energy at {stats.energy:.0f}% - sleeping to restore (score={score:.0f})"
        elif action_name == "CONSUME":
            return f"Using consumable to restore a low stat (score={score:.0f})"
        elif action_name == "BUY":
            return f"Buying consumable to prepare for future needs (score={score:.0f})"
        return f"{action_name} (score={score:.0f})"

    # ---- finalization & logging ---------------------------------------------

    def _finalize(
        self,
        decision: ActionDecision,
        urgency: Dict[str, float],
        alternatives: List[Dict[str, Any]],
        mode: str,
    ) -> ActionDecision:
        """Attach AgentDecisionLog and record."""
        decision.decision_log = AgentDecisionLog(
            action=decision.action.name,
            reasoning=decision.reason,
            stats_before=decision.stats_snapshot or {},
            urgency_scores=urgency,
            alternatives_considered=alternatives,
            mode=mode,
        )
        self._record_decision(decision)
        return decision

    def _log_context(self, context: PetContext) -> None:
        stats = context.stats
        self.logger.info(
            "Decision context: "
            "hunger=%.1f%%, health=%.1f%%, energy=%.1f%%, "
            "happiness=%.1f%%, hygiene=%.1f%% | "
            "sleeping=%s, tokens=%.2f, consumables=%d, "
            "onchain_actions=%d/%d",
            stats.hunger, stats.health, stats.energy,
            stats.happiness, stats.hygiene,
            context.is_sleeping, context.token_balance,
            len(context.owned_consumables),
            context.actions_recorded_this_epoch,
            context.required_actions_per_epoch,
        )

    def _record_decision(self, decision: ActionDecision) -> None:
        self._last_decision = decision
        self._decision_history.append(decision)
        if len(self._decision_history) > 50:
            self._decision_history = self._decision_history[-50:]
        self.logger.info("Decision: %s", decision)

    # ---- failure tracking ---------------------------------------------------

    def get_decision_history(self) -> List[ActionDecision]:
        return list(self._decision_history)

    def get_last_decision(self) -> Optional[ActionDecision]:
        return self._last_decision

    def record_action_failure(
        self,
        action: ActionType,
        params: Dict[str, Any],
        reason: str = "",
    ) -> None:
        self._clear_stale_failures()

        for failed in self._failed_actions:
            if failed.matches(action, params):
                failed.failed_at = datetime.now()
                failed.reason = reason
                self.logger.info(
                    "Updated failure record for %s (params=%s): %s",
                    action.name, params, reason,
                )
                return

        failure = FailedAction(
            action=action,
            params=dict(params),
            failed_at=datetime.now(),
            reason=reason,
        )
        self._failed_actions.append(failure)
        if action in (ActionType.CONSUMABLES_USE, ActionType.CONSUMABLES_BUY):
            self.logger.warning(
                "Recorded consumable failure (not blocked): %s (params=%s). Reason: %s",
                action.name, params, reason,
            )
        else:
            self.logger.warning(
                "Recorded action failure: %s (params=%s) - will skip for %d seconds. Reason: %s",
                action.name, params, FailedAction.COOLDOWN_SECONDS, reason,
            )

    def is_action_blocked(
        self,
        action: ActionType,
        params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if action in (ActionType.CONSUMABLES_USE, ActionType.CONSUMABLES_BUY):
            return False
        self._clear_stale_failures()
        params = params or {}
        for failed in self._failed_actions:
            if failed.matches(action, params):
                return True
        return False

    def get_blocked_consumables(self) -> List[str]:
        return []

    def _clear_stale_failures(self) -> None:
        now = datetime.now()
        before_count = len(self._failed_actions)
        self._failed_actions = [f for f in self._failed_actions if not f.is_expired(now)]
        cleared = before_count - len(self._failed_actions)
        if cleared > 0:
            self.logger.debug("Cleared %d expired failure records", cleared)

    def clear_all_failures(self) -> None:
        self._failed_actions.clear()
        self.logger.debug("Cleared all failure records")

    def get_failed_actions(self) -> List[FailedAction]:
        self._clear_stale_failures()
        return list(self._failed_actions)


# ---------------------------------------------------------------------------
# Action executor protocol (unchanged)
# ---------------------------------------------------------------------------

class ActionExecutor(Protocol):
    async def execute_sleep(
        self, record_on_chain: bool, wake_first: bool = False
    ) -> bool:
        ...

    async def execute_shower(self, record_on_chain: bool) -> bool:
        ...

    async def execute_rub(self, record_on_chain: bool) -> bool:
        ...

    async def execute_throwball(self, record_on_chain: bool) -> bool:
        ...

    async def execute_use_consumable(
        self, consumable_id: str, record_on_chain: bool
    ) -> bool:
        ...

    async def execute_buy_consumable(
        self, consumable_id: str, amount: int, record_on_chain: bool
    ) -> bool:
        ...


async def execute_decision(
    decision: ActionDecision,
    executor: ActionExecutor,
    logger: Optional[logging.Logger] = None,
) -> bool:
    log = logger or logging.getLogger(__name__)

    if decision.action == ActionType.NONE:
        log.info("No action to execute")
        return False

    if decision.action == ActionType.SLEEP:
        wake_first = decision.params.get("wake_first", False)
        return await executor.execute_sleep(decision.should_record_onchain, wake_first=wake_first)

    if decision.action == ActionType.SHOWER:
        return await executor.execute_shower(decision.should_record_onchain)

    if decision.action == ActionType.RUB:
        return await executor.execute_rub(decision.should_record_onchain)

    if decision.action == ActionType.THROWBALL:
        return await executor.execute_throwball(decision.should_record_onchain)

    if decision.action == ActionType.CONSUMABLES_USE:
        consumable_id = decision.params.get("consumable_id", "")
        return await executor.execute_use_consumable(consumable_id, decision.should_record_onchain)

    if decision.action == ActionType.CONSUMABLES_BUY:
        consumable_id = decision.params.get("consumable_id", "")
        amount = decision.params.get("amount", 1)
        return await executor.execute_buy_consumable(consumable_id, amount, decision.should_record_onchain)

    log.warning("Unknown action type: %s", decision.action)
    return False


# ---------------------------------------------------------------------------
# Convenience functions (backward compat)
# ---------------------------------------------------------------------------

def feed_best_owned_food(owned_consumables: List[str]) -> Optional[str]:
    return ConsumableSelector.get_best_food(owned_consumables)

def get_best_health_item(owned_consumables: List[str]) -> Optional[str]:
    return ConsumableSelector.get_best_health_item(owned_consumables)

def get_best_consumable(owned_consumables: List[str]) -> Optional[str]:
    return ConsumableSelector.get_any_consumable(owned_consumables)
