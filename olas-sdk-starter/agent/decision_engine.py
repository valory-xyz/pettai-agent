"""
Pet Decision Maker - Clean, testable decision logic for pet actions.

This module provides a prioritized, deterministic decision-making system
for choosing pet actions based on current stats and constraints.
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
    # Fallback for when constants module is not available
    REQUIRED_ACTIONS_PER_EPOCH = 9

logger = logging.getLogger(__name__)


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
        """Create PetStats from a dictionary."""

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
        """Check if all stats are at 0."""
        return all(
            v <= 0.0
            for v in [
                self.hunger,
                self.health,
                self.energy,
                self.happiness,
                self.hygiene,
            ]
        )

    def is_all_full(self) -> bool:
        """Check if all stats are at 100."""
        return all(
            v >= 100.0
            for v in [
                self.hunger,
                self.health,
                self.energy,
                self.happiness,
                self.hygiene,
            ]
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

    @property
    def needs_more_onchain_actions(self) -> bool:
        """Check if we still need to record more on-chain actions."""
        return self.actions_recorded_this_epoch < self.required_actions_per_epoch

    @property
    def remaining_required_actions(self) -> int:
        """Number of on-chain actions still needed."""
        return max(
            0, self.required_actions_per_epoch - self.actions_recorded_this_epoch
        )


@dataclass
class ActionDecision:
    """Result of the decision-making process."""

    action: ActionType
    reason: str
    should_record_onchain: bool
    params: Dict[str, Any] = field(default_factory=dict)
    fallback_from: Optional[ActionType] = None
    stats_snapshot: Optional[Dict[str, float]] = None

    def __str__(self) -> str:
        onchain_str = "🔗 ON-CHAIN" if self.should_record_onchain else "📝 OFF-CHAIN"
        fallback_str = (
            f" (fallback from {self.fallback_from.name})" if self.fallback_from else ""
        )
        return f"{onchain_str} {self.action.name}{fallback_str}: {self.reason}"


@dataclass
class FailedAction:
    """
    Tracks a failed action to prevent infinite retry loops.

    When an action fails (e.g., server rejects CONSUMABLES_USE for any reason),
    we record it here to avoid immediately retrying the same action.

    Matching is based on action type and params only - the reason string
    is only used for logging/debugging and does not affect matching logic.
    """

    action: ActionType
    params: Dict[str, Any]
    failed_at: datetime
    reason: str = ""  # Used only for logging, not for matching

    # How long to block this action from being retried (default 5 minutes)
    COOLDOWN_SECONDS: int = 300

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        """Check if this failure record has expired and can be retried."""
        now = now or datetime.now()
        return (now - self.failed_at).total_seconds() > self.COOLDOWN_SECONDS

    def matches(self, action: ActionType, params: Dict[str, Any]) -> bool:
        """
        Check if this failure matches a given action and params.

        For CONSUMABLES_USE, we match on consumable_id.
        For CONSUMABLES_BUY, we match on consumable_id.
        For other actions, we just match the action type.
        """
        if self.action != action:
            return False

        if action in (ActionType.CONSUMABLES_USE, ActionType.CONSUMABLES_BUY):
            # Match on specific consumable
            return self.params.get("consumable_id") == params.get("consumable_id")

        # For other actions, just matching action type is enough
        return True


class ConsumableSelector:
    """
    Selects the best consumable to use based on priority/effectiveness.

    Food priority (hunger recovery):
        SUSHI > STEAK > PIZZA > BURGER > SALAD > COOKIE

    Health priority (health recovery):
        LARGE_POTION > POTION > SMALL_POTION > SALAD

    Note: SALAD provides both food and health benefits.
    """

    # Food items ordered by effectiveness (best first)
    FOOD_PRIORITY = [
        "SUSHI",  # Best food
        "STEAK",
        "PIZZA",
        "BURGER",
        "SALAD",  # Also gives health
        "COOKIE",  # Least effective
    ]

    # Health items ordered by effectiveness (best first)
    HEALTH_PRIORITY = [
        "LARGE_POTION",
        "POTION",
        "SMALL_POTION",
        "SALAD",  # Also gives food
    ]

    # All consumable types
    ALL_FOOD = {"SUSHI", "STEAK", "PIZZA", "BURGER", "SALAD", "COOKIE"}
    ALL_HEALTH = {"LARGE_POTION", "POTION", "SMALL_POTION", "SALAD"}

    @classmethod
    def get_best_food(cls, owned_consumables: List[str]) -> Optional[str]:
        """
        Get the best food item from owned consumables.

        Returns:
            Best food consumable ID or None if no food owned.
        """
        owned_upper = [c.upper() for c in owned_consumables]

        for food in cls.FOOD_PRIORITY:
            if food in owned_upper:
                # Return original case from owned list
                idx = owned_upper.index(food)
                return owned_consumables[idx]

        return None

    @classmethod
    def get_best_health_item(cls, owned_consumables: List[str]) -> Optional[str]:
        """
        Get the best health item from owned consumables.

        Returns:
            Best health consumable ID or None if no health items owned.
        """
        owned_upper = [c.upper() for c in owned_consumables]

        for health in cls.HEALTH_PRIORITY:
            if health in owned_upper:
                idx = owned_upper.index(health)
                return owned_consumables[idx]

        return None

    @classmethod
    def get_any_consumable(cls, owned_consumables: List[str]) -> Optional[str]:
        """
        Get any consumable (prefer food over health items for critical state).

        Returns:
            Best consumable ID or None if nothing owned.
        """
        # Try food first (more universally useful)
        food = cls.get_best_food(owned_consumables)
        if food:
            return food

        # Then health items
        health = cls.get_best_health_item(owned_consumables)
        if health:
            return health

        # Return first available if any
        return owned_consumables[0] if owned_consumables else None

    @classmethod
    def has_food(cls, owned_consumables: List[str]) -> bool:
        """Check if any food items are owned."""
        owned_upper = {c.upper() for c in owned_consumables}
        return bool(owned_upper & cls.ALL_FOOD)

    @classmethod
    def has_health_item(cls, owned_consumables: List[str]) -> bool:
        """Check if any health items are owned."""
        owned_upper = {c.upper() for c in owned_consumables}
        return bool(owned_upper & cls.ALL_HEALTH)

    @classmethod
    def get_best_to_buy_for_hunger(cls) -> str:
        """Get the best food item to buy when hungry."""
        return "BURGER"  # Good balance of cost and effectiveness

    @classmethod
    def get_best_to_buy_for_health(cls) -> str:
        """Get the best health item to buy when low health."""
        return "SMALL_POTION"  # Most cost-effective


class ActionConditions:
    """
    Defines conditions for when each action can be performed.

    Conditions (action can only be performed if condition is met):
    - RUB: hygiene < 75
    - SHOWER: hygiene < 75
    - SLEEP: always possible
    - THROWBALL: health >= 15 OR hunger >= 15 OR energy >= 15
    - CONSUMABLES_USE: we have any consumable
    - CONSUMABLES_BUY: we have enough tokens (>= threshold)
    """

    HYGIENE_THRESHOLD = 75.0
    THROWBALL_MIN_STAT = 15.0
    MIN_TOKENS_FOR_BUY = 50.0  # Minimum tokens needed to buy a consumable

    @classmethod
    def can_rub(cls, stats: PetStats) -> Tuple[bool, str]:
        """Check if RUB action is possible."""
        if stats.hygiene < cls.HYGIENE_THRESHOLD:
            return True, f"hygiene ({stats.hygiene:.1f}) < {cls.HYGIENE_THRESHOLD}"
        return False, f"hygiene ({stats.hygiene:.1f}) >= {cls.HYGIENE_THRESHOLD}"

    @classmethod
    def can_shower(cls, stats: PetStats) -> Tuple[bool, str]:
        """Check if SHOWER action is possible."""
        if stats.hygiene < cls.HYGIENE_THRESHOLD:
            return True, f"hygiene ({stats.hygiene:.1f}) < {cls.HYGIENE_THRESHOLD}"
        return False, f"hygiene ({stats.hygiene:.1f}) >= {cls.HYGIENE_THRESHOLD}"

    @classmethod
    def can_sleep(cls, stats: PetStats) -> Tuple[bool, str]:
        """Check if SLEEP action is possible. Always returns True."""
        return True, "sleep is always possible"

    @classmethod
    def can_throwball(cls, stats: PetStats) -> Tuple[bool, str]:
        """
        Check if THROWBALL action is possible.
        Cannot throwball if ALL of health, hunger, and energy are below minimum.
        """
        below_threshold = (
            stats.health < cls.THROWBALL_MIN_STAT
            and stats.hunger < cls.THROWBALL_MIN_STAT
            and stats.energy < cls.THROWBALL_MIN_STAT
        )
        if not below_threshold:
            return (
                True,
                f"at least one stat (health/hunger/energy) >= {cls.THROWBALL_MIN_STAT}",
            )
        return False, f"all core stats below {cls.THROWBALL_MIN_STAT}"

    @classmethod
    def can_use_consumable(cls, context: PetContext) -> Tuple[bool, str]:
        """Check if CONSUMABLES_USE action is possible."""
        if context.owned_consumables and len(context.owned_consumables) > 0:
            return True, f"owns {len(context.owned_consumables)} consumable(s)"
        return False, "no consumables owned"

    @classmethod
    def can_buy_consumable(cls, context: PetContext) -> Tuple[bool, str]:
        """Check if CONSUMABLES_BUY action is possible."""
        if context.token_balance >= cls.MIN_TOKENS_FOR_BUY:
            return (
                True,
                f"balance ({context.token_balance:.2f}) >= {cls.MIN_TOKENS_FOR_BUY}",
            )
        return (
            False,
            f"balance ({context.token_balance:.2f}) < {cls.MIN_TOKENS_FOR_BUY}",
        )

    @classmethod
    def get_all_possible_actions(
        cls, context: PetContext
    ) -> List[Tuple[ActionType, str]]:
        """Get all actions that are currently possible with their reasons."""
        possible = []

        can, reason = cls.can_sleep(context.stats)
        if can:
            possible.append((ActionType.SLEEP, reason))

        can, reason = cls.can_shower(context.stats)
        if can:
            possible.append((ActionType.SHOWER, reason))

        can, reason = cls.can_rub(context.stats)
        if can:
            possible.append((ActionType.RUB, reason))

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


class PetDecisionMaker:
    """
    Makes decisions about which action to perform based on pet state.

    Decision Priority (highest to lowest):
    1. CRITICAL: If all stats are near 0, use/buy consumables or sleep
    2. LOW_ENERGY: If energy < 25 and not critical, sleep
    3. LOW_HEALTH: If health < 70, use health consumable
    4. LOW_HUNGER: If hunger < 70, use food consumable
    5. LOW_HYGIENE: If hygiene < 70, shower
    6. LOW_HAPPINESS: If happiness < 70, throwball or rub
    7. MAINTENANCE: Otherwise, perform any valid action (prefer throwball for tokens)

    Special Rules:
    - If sleeping with 0 energy and need on-chain record, wake and re-sleep
    - Always ensure we get an on-chain record if needed
    - Fallback to RUB/SLEEP if primary action is not possible
    """

    # Thresholds
    CRITICAL_THRESHOLD = 5.0
    LOW_ENERGY_THRESHOLD = 25.0
    LOW_STAT_THRESHOLD = 70.0
    WAKE_ENERGY_THRESHOLD = 65.0

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        self._last_decision: Optional[ActionDecision] = None
        self._decision_history: List[ActionDecision] = []
        self._failed_actions: List[FailedAction] = []

    def decide(self, context: PetContext) -> ActionDecision:
        """
        Main decision method. Returns the best action to perform.

        Args:
            context: Full pet context including stats, inventory, etc.

        Returns:
            ActionDecision with the chosen action and reasoning
        """
        stats = context.stats
        should_record = context.needs_more_onchain_actions

        self._log_context(context)

        # Check if pet is dead - no actions possible
        if context.is_dead:
            decision = ActionDecision(
                action=ActionType.NONE,
                reason="Pet is dead - no actions possible",
                should_record_onchain=False,
                stats_snapshot=stats.to_dict(),
            )
            self._record_decision(decision)
            return decision

        # Special case: sleeping with 0 energy and need on-chain record
        if context.is_sleeping and stats.energy <= 0 and should_record:
            decision = ActionDecision(
                action=ActionType.SLEEP,
                reason="Sleeping with 0 energy - need to wake and re-sleep for on-chain record",
                should_record_onchain=True,
                params={"wake_first": True},
                stats_snapshot=stats.to_dict(),
            )
            self._record_decision(decision)
            return decision

        # If sleeping and energy is recovering, stay asleep (unless critical)
        if context.is_sleeping and stats.energy < self.WAKE_ENERGY_THRESHOLD:
            if not stats.is_critical(self.CRITICAL_THRESHOLD):
                decision = ActionDecision(
                    action=ActionType.SLEEP,
                    reason=f"Still resting - energy ({stats.energy:.1f}) < {self.WAKE_ENERGY_THRESHOLD}",
                    should_record_onchain=should_record,
                    params={"stay_asleep": True},
                    stats_snapshot=stats.to_dict(),
                )
                self._record_decision(decision)
                return decision

        # Priority 1: CRITICAL - all stats near 0
        if stats.is_critical(self.CRITICAL_THRESHOLD):
            decision = self._handle_critical_stats(context, should_record)
            self._record_decision(decision)
            return decision

        # Priority 2: LOW_ENERGY - sleep if very low
        if stats.energy < self.LOW_ENERGY_THRESHOLD:
            can_sleep, reason = ActionConditions.can_sleep(stats)
            if can_sleep:
                decision = ActionDecision(
                    action=ActionType.SLEEP,
                    reason=f"Low energy ({stats.energy:.1f}) - initiating sleep",
                    should_record_onchain=should_record,
                    stats_snapshot=stats.to_dict(),
                )
                self._record_decision(decision)
                return decision

        # Priority 3: LOW_HEALTH - use health consumable
        if stats.health < self.LOW_STAT_THRESHOLD:
            decision = self._try_health_recovery(context, should_record)
            if decision.action != ActionType.NONE:
                self._record_decision(decision)
                return decision

        # Priority 4: LOW_HUNGER - use food consumable
        if stats.hunger < self.LOW_STAT_THRESHOLD:
            decision = self._try_hunger_recovery(context, should_record)
            if decision.action != ActionType.NONE:
                self._record_decision(decision)
                return decision

        # Priority 5: LOW_HYGIENE - shower
        if stats.hygiene < self.LOW_STAT_THRESHOLD:
            can_shower, reason = ActionConditions.can_shower(stats)
            if can_shower:
                decision = ActionDecision(
                    action=ActionType.SHOWER,
                    reason=f"Low hygiene ({stats.hygiene:.1f}) - showering",
                    should_record_onchain=should_record,
                    stats_snapshot=stats.to_dict(),
                )
                self._record_decision(decision)
                return decision

        # Priority 6: LOW_HAPPINESS - throwball or rub
        if stats.happiness < self.LOW_STAT_THRESHOLD:
            decision = self._try_happiness_recovery(context, should_record)
            if decision.action != ActionType.NONE:
                self._record_decision(decision)
                return decision

        # Priority 7: MAINTENANCE - all stats are okay, do maintenance actions
        decision = self._do_maintenance_action(context, should_record)
        self._record_decision(decision)
        return decision

    def _handle_critical_stats(
        self, context: PetContext, should_record: bool
    ) -> ActionDecision:
        """Handle critical state where all stats are very low."""
        stats = context.stats

        self.logger.warning(
            "⚠️ CRITICAL: All stats below %.1f - prioritizing recovery",
            self.CRITICAL_THRESHOLD,
        )

        # Filter out blocked consumables before selecting
        blocked = self.get_blocked_consumables()
        available_consumables = [
            c
            for c in context.owned_consumables
            if c.upper() not in [b.upper() for b in blocked]
        ]

        # Try consumables first - use best available (non-blocked)
        if available_consumables:
            best_consumable = ConsumableSelector.get_any_consumable(
                available_consumables
            )
            if best_consumable:
                return ActionDecision(
                    action=ActionType.CONSUMABLES_USE,
                    reason=f"CRITICAL stats - using best consumable: {best_consumable}",
                    should_record_onchain=should_record,
                    params={
                        "consumable_id": best_consumable,
                        "action": "use_critical_consumable",
                    },
                    stats_snapshot=stats.to_dict(),
                )

        # Log if we skipped consumables due to blocking
        if blocked and context.owned_consumables:
            self.logger.info(
                "⏭️ CRITICAL: Skipping blocked consumables: %s (blocked: %s)",
                context.owned_consumables,
                blocked,
            )

        # Try buying consumables - prefer food in critical state (if not blocked)
        can_buy, reason = ActionConditions.can_buy_consumable(context)
        if can_buy:
            food_to_buy = ConsumableSelector.get_best_to_buy_for_hunger()
            if not self.is_action_blocked(
                ActionType.CONSUMABLES_BUY, {"consumable_id": food_to_buy}
            ):
                return ActionDecision(
                    action=ActionType.CONSUMABLES_BUY,
                    reason=f"CRITICAL stats - buying {food_to_buy} ({reason})",
                    should_record_onchain=should_record,
                    params={"consumable_id": food_to_buy, "amount": 1},
                    stats_snapshot=stats.to_dict(),
                )

        # Fallback to free actions: RUB first (improves happiness)
        can_rub, reason = ActionConditions.can_rub(stats)
        if can_rub:
            return ActionDecision(
                action=ActionType.RUB,
                reason=f"CRITICAL stats, no consumables - rubbing pet ({reason})",
                should_record_onchain=should_record,
                fallback_from=ActionType.CONSUMABLES_USE,
                stats_snapshot=stats.to_dict(),
            )

        # Fallback to SHOWER
        can_shower, reason = ActionConditions.can_shower(stats)
        if can_shower:
            return ActionDecision(
                action=ActionType.SHOWER,
                reason=f"CRITICAL stats, no consumables - showering ({reason})",
                should_record_onchain=should_record,
                fallback_from=ActionType.RUB,
                stats_snapshot=stats.to_dict(),
            )

        # Ultimate fallback: SLEEP (always possible)
        return ActionDecision(
            action=ActionType.SLEEP,
            reason="CRITICAL stats - sleeping as last resort",
            should_record_onchain=should_record,
            fallback_from=ActionType.SHOWER,
            stats_snapshot=stats.to_dict(),
        )

    def _try_health_recovery(
        self, context: PetContext, should_record: bool
    ) -> ActionDecision:
        """Attempt to recover health using consumables."""
        stats = context.stats

        # Filter out blocked consumables before selecting
        blocked = self.get_blocked_consumables()
        available_consumables = [
            c
            for c in context.owned_consumables
            if c.upper() not in [b.upper() for b in blocked]
        ]

        # Get best health item using ConsumableSelector from available (non-blocked)
        best_health = ConsumableSelector.get_best_health_item(available_consumables)

        if best_health:
            return ActionDecision(
                action=ActionType.CONSUMABLES_USE,
                reason=f"Low health ({stats.health:.1f}) - using best health item: {best_health}",
                should_record_onchain=should_record,
                params={"consumable_id": best_health, "action": "use_best_health_item"},
                stats_snapshot=stats.to_dict(),
            )

        # Log if we skipped consumables due to blocking
        if blocked and context.owned_consumables:
            owned_health = ConsumableSelector.get_best_health_item(
                context.owned_consumables
            )
            if owned_health:
                self.logger.info(
                    "⏭️ Skipping blocked health item %s (blocked: %s)",
                    owned_health,
                    blocked,
                )

        # Try buying a health consumable (if not blocked)
        can_buy, reason = ActionConditions.can_buy_consumable(context)
        if can_buy:
            health_to_buy = ConsumableSelector.get_best_to_buy_for_health()
            # Check if the item to buy is blocked
            if not self.is_action_blocked(
                ActionType.CONSUMABLES_BUY, {"consumable_id": health_to_buy}
            ):
                return ActionDecision(
                    action=ActionType.CONSUMABLES_BUY,
                    reason=f"Low health ({stats.health:.1f}) - buying {health_to_buy}",
                    should_record_onchain=should_record,
                    params={"consumable_id": health_to_buy, "amount": 1},
                    stats_snapshot=stats.to_dict(),
                )

        # Can't recover health - return NONE to try next priority
        return ActionDecision(
            action=ActionType.NONE,
            reason=f"Low health ({stats.health:.1f}) but no way to recover",
            should_record_onchain=False,
            stats_snapshot=stats.to_dict(),
        )

    def _try_hunger_recovery(
        self, context: PetContext, should_record: bool
    ) -> ActionDecision:
        """Attempt to recover hunger using consumables."""
        stats = context.stats

        # Filter out blocked consumables before selecting
        blocked = self.get_blocked_consumables()
        available_consumables = [
            c
            for c in context.owned_consumables
            if c.upper() not in [b.upper() for b in blocked]
        ]

        # Get best food using ConsumableSelector from available (non-blocked)
        best_food = ConsumableSelector.get_best_food(available_consumables)

        if best_food:
            return ActionDecision(
                action=ActionType.CONSUMABLES_USE,
                reason=f"Low hunger ({stats.hunger:.1f}) - feeding best food: {best_food}",
                should_record_onchain=should_record,
                params={"consumable_id": best_food, "action": "feed_best_owned_food"},
                stats_snapshot=stats.to_dict(),
            )

        # Log if we skipped consumables due to blocking
        if blocked and context.owned_consumables:
            owned_food = ConsumableSelector.get_best_food(context.owned_consumables)
            if owned_food:
                self.logger.info(
                    "⏭️ Skipping blocked food item %s (blocked: %s)",
                    owned_food,
                    blocked,
                )

        # Try buying food (if not blocked)
        can_buy, reason = ActionConditions.can_buy_consumable(context)
        if can_buy:
            food_to_buy = ConsumableSelector.get_best_to_buy_for_hunger()
            # Check if the item to buy is blocked
            if not self.is_action_blocked(
                ActionType.CONSUMABLES_BUY, {"consumable_id": food_to_buy}
            ):
                return ActionDecision(
                    action=ActionType.CONSUMABLES_BUY,
                    reason=f"Low hunger ({stats.hunger:.1f}) - buying {food_to_buy}",
                    should_record_onchain=should_record,
                    params={"consumable_id": food_to_buy, "amount": 1},
                    stats_snapshot=stats.to_dict(),
                )

        # Can't recover hunger - return NONE to try next priority
        return ActionDecision(
            action=ActionType.NONE,
            reason=f"Low hunger ({stats.hunger:.1f}) but no way to recover",
            should_record_onchain=False,
            stats_snapshot=stats.to_dict(),
        )

    def _try_happiness_recovery(
        self, context: PetContext, should_record: bool
    ) -> ActionDecision:
        """Attempt to recover happiness."""
        stats = context.stats

        # Prefer throwball (earns tokens) unless it's been recently blocked
        if not self.is_action_blocked(ActionType.THROWBALL):
            can_throwball, reason = ActionConditions.can_throwball(stats)
            if can_throwball:
                return ActionDecision(
                    action=ActionType.THROWBALL,
                    reason=f"Low happiness ({stats.happiness:.1f}) - throwing ball",
                    should_record_onchain=should_record,
                    stats_snapshot=stats.to_dict(),
                )
        else:
            self.logger.debug("🔁 Skipping THROWBALL - action blocked by recent failure")

        # Fallback to rub
        can_rub, reason = ActionConditions.can_rub(stats)
        if can_rub:
            return ActionDecision(
                action=ActionType.RUB,
                reason=f"Low happiness ({stats.happiness:.1f}) - rubbing pet",
                should_record_onchain=should_record,
                fallback_from=ActionType.THROWBALL,
                stats_snapshot=stats.to_dict(),
            )

        # Can't improve happiness directly
        return ActionDecision(
            action=ActionType.NONE,
            reason=f"Low happiness ({stats.happiness:.1f}) but no action available",
            should_record_onchain=False,
            stats_snapshot=stats.to_dict(),
        )

    def _do_maintenance_action(
        self, context: PetContext, should_record: bool
    ) -> ActionDecision:
        """Perform maintenance action when all stats are acceptable."""
        stats = context.stats

        # Prefer throwball (earns tokens and we need actions) unless blocked
        if not self.is_action_blocked(ActionType.THROWBALL):
            can_throwball, reason = ActionConditions.can_throwball(stats)
            if can_throwball:
                return ActionDecision(
                    action=ActionType.THROWBALL,
                    reason=f"Maintenance: throwing ball to earn tokens ({reason})",
                    should_record_onchain=should_record,
                    stats_snapshot=stats.to_dict(),
                )
        else:
            self.logger.debug("🔁 Skipping maintenance THROWBALL - blocked by recent failure")

        # Try shower
        can_shower, reason = ActionConditions.can_shower(stats)
        if can_shower:
            return ActionDecision(
                action=ActionType.SHOWER,
                reason=f"Maintenance: showering ({reason})",
                should_record_onchain=should_record,
                stats_snapshot=stats.to_dict(),
            )

        # Try rub
        can_rub, reason = ActionConditions.can_rub(stats)
        if can_rub:
            return ActionDecision(
                action=ActionType.RUB,
                reason=f"Maintenance: rubbing ({reason})",
                should_record_onchain=should_record,
                stats_snapshot=stats.to_dict(),
            )

        # Ultimate fallback: sleep (always possible)
        return ActionDecision(
            action=ActionType.SLEEP,
            reason="Maintenance: all stats full, sleeping to maintain",
            should_record_onchain=should_record,
            fallback_from=ActionType.THROWBALL,
            stats_snapshot=stats.to_dict(),
        )

    def _log_context(self, context: PetContext) -> None:
        """Log the current context for debugging."""
        stats = context.stats
        self.logger.info(
            "📊 Decision context: "
            "hunger=%.1f%%, health=%.1f%%, energy=%.1f%%, "
            "happiness=%.1f%%, hygiene=%.1f%% | "
            "sleeping=%s, tokens=%.2f, consumables=%d, "
            "onchain_actions=%d/%d",
            stats.hunger,
            stats.health,
            stats.energy,
            stats.happiness,
            stats.hygiene,
            context.is_sleeping,
            context.token_balance,
            len(context.owned_consumables),
            context.actions_recorded_this_epoch,
            context.required_actions_per_epoch,
        )

    def _record_decision(self, decision: ActionDecision) -> None:
        """Record decision in history and log it."""
        self._last_decision = decision
        self._decision_history.append(decision)

        # Keep only last 50 decisions
        if len(self._decision_history) > 50:
            self._decision_history = self._decision_history[-50:]

        self.logger.info("✅ Decision: %s", decision)

    def get_decision_history(self) -> List[ActionDecision]:
        """Get the history of decisions made."""
        return list(self._decision_history)

    def get_last_decision(self) -> Optional[ActionDecision]:
        """Get the most recent decision."""
        return self._last_decision

    def record_action_failure(
        self,
        action: ActionType,
        params: Dict[str, Any],
        reason: str = "",
    ) -> None:
        """
        Record that an action failed and should not be immediately retried.

        This prevents infinite loops where the same action keeps failing
        but the decision engine keeps recommending it.

        Note: The failure matching is based on action type and params only,
        not the reason string. The reason is only used for logging/debugging.

        Args:
            action: The action type that failed
            params: Parameters of the failed action (e.g., consumable_id)
            reason: Optional reason for the failure (any string, used only for logging)
        """
        # First, clean up any stale failures
        self._clear_stale_failures()

        # Check if we already have this failure recorded
        for failed in self._failed_actions:
            if failed.matches(action, params):
                # Update the timestamp and reason
                failed.failed_at = datetime.now()
                failed.reason = reason
                self.logger.info(
                    "🔄 Updated failure record for %s (params=%s): %s",
                    action.name,
                    params,
                    reason,
                )
                return

        # Record the new failure
        failure = FailedAction(
            action=action,
            params=dict(params),  # Copy to avoid mutation
            failed_at=datetime.now(),
            reason=reason,
        )
        self._failed_actions.append(failure)
        self.logger.warning(
            "⛔ Recorded action failure: %s (params=%s) - will skip for %d seconds. Reason: %s",
            action.name,
            params,
            FailedAction.COOLDOWN_SECONDS,
            reason,
        )

    def is_action_blocked(
        self,
        action: ActionType,
        params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Check if an action is currently blocked due to recent failure.

        Args:
            action: The action type to check
            params: Parameters to match (for consumables, includes consumable_id)

        Returns:
            True if the action should be skipped, False if it can be tried
        """
        self._clear_stale_failures()
        params = params or {}

        for failed in self._failed_actions:
            if failed.matches(action, params):
                return True
        return False

    def get_blocked_consumables(self) -> List[str]:
        """
        Get list of consumable IDs that are currently blocked.

        Returns:
            List of consumable IDs that should not be used
        """
        self._clear_stale_failures()
        blocked = []
        for failed in self._failed_actions:
            if failed.action in (
                ActionType.CONSUMABLES_USE,
                ActionType.CONSUMABLES_BUY,
            ):
                consumable_id = failed.params.get("consumable_id")
                if consumable_id:
                    blocked.append(consumable_id)
        return blocked

    def _clear_stale_failures(self) -> None:
        """Remove expired failure records."""
        now = datetime.now()
        before_count = len(self._failed_actions)
        self._failed_actions = [
            f for f in self._failed_actions if not f.is_expired(now)
        ]
        cleared = before_count - len(self._failed_actions)
        if cleared > 0:
            self.logger.debug("Cleared %d expired failure records", cleared)

    def clear_all_failures(self) -> None:
        """Clear all failure records. Useful for testing or reset scenarios."""
        self._failed_actions.clear()
        self.logger.debug("Cleared all failure records")

    def get_failed_actions(self) -> List[FailedAction]:
        """Get list of currently active failure records."""
        self._clear_stale_failures()
        return list(self._failed_actions)


class ActionExecutor(Protocol):
    """Protocol for executing pet actions."""

    async def execute_sleep(
        self, record_on_chain: bool, wake_first: bool = False
    ) -> bool: ...

    async def execute_shower(self, record_on_chain: bool) -> bool: ...

    async def execute_rub(self, record_on_chain: bool) -> bool: ...

    async def execute_throwball(self, record_on_chain: bool) -> bool: ...

    async def execute_use_consumable(
        self, consumable_id: str, record_on_chain: bool
    ) -> bool: ...

    async def execute_buy_consumable(
        self, consumable_id: str, amount: int, record_on_chain: bool
    ) -> bool: ...


async def execute_decision(
    decision: ActionDecision,
    executor: ActionExecutor,
    logger: Optional[logging.Logger] = None,
) -> bool:
    """
    Execute a decision using the provided executor.

    Args:
        decision: The ActionDecision to execute
        executor: Implementation of ActionExecutor protocol
        logger: Optional logger

    Returns:
        True if action was executed successfully
    """
    log = logger or logging.getLogger(__name__)

    if decision.action == ActionType.NONE:
        log.info("No action to execute")
        return False

    if decision.action == ActionType.SLEEP:
        wake_first = decision.params.get("wake_first", False)
        return await executor.execute_sleep(
            decision.should_record_onchain,
            wake_first=wake_first,
        )

    if decision.action == ActionType.SHOWER:
        return await executor.execute_shower(decision.should_record_onchain)

    if decision.action == ActionType.RUB:
        return await executor.execute_rub(decision.should_record_onchain)

    if decision.action == ActionType.THROWBALL:
        return await executor.execute_throwball(decision.should_record_onchain)

    if decision.action == ActionType.CONSUMABLES_USE:
        consumable_id = decision.params.get("consumable_id", "")
        return await executor.execute_use_consumable(
            consumable_id,
            decision.should_record_onchain,
        )

    if decision.action == ActionType.CONSUMABLES_BUY:
        consumable_id = decision.params.get("consumable_id", "")
        amount = decision.params.get("amount", 1)
        return await executor.execute_buy_consumable(
            consumable_id,
            amount,
            decision.should_record_onchain,
        )

    log.warning("Unknown action type: %s", decision.action)
    return False


# ==============================================================================
# Convenience Functions
# ==============================================================================


def feed_best_owned_food(owned_consumables: List[str]) -> Optional[str]:
    """
    Get the best food to feed from owned consumables.

    This is a convenience wrapper around ConsumableSelector.get_best_food().

    Args:
        owned_consumables: List of consumable IDs owned by the pet.

    Returns:
        The best food consumable ID to use, or None if no food owned.

    Example:
        >>> food = feed_best_owned_food(["COOKIE", "BURGER", "SMALL_POTION"])
        >>> print(food)  # "BURGER" (higher priority than COOKIE)
    """
    return ConsumableSelector.get_best_food(owned_consumables)


def get_best_health_item(owned_consumables: List[str]) -> Optional[str]:
    """
    Get the best health item from owned consumables.

    This is a convenience wrapper around ConsumableSelector.get_best_health_item().

    Args:
        owned_consumables: List of consumable IDs owned by the pet.

    Returns:
        The best health consumable ID to use, or None if no health items owned.

    Example:
        >>> health = get_best_health_item(["SMALL_POTION", "LARGE_POTION"])
        >>> print(health)  # "LARGE_POTION" (higher priority)
    """
    return ConsumableSelector.get_best_health_item(owned_consumables)


def get_best_consumable(owned_consumables: List[str]) -> Optional[str]:
    """
    Get the best consumable to use (prefers food over health items).

    Args:
        owned_consumables: List of consumable IDs owned by the pet.

    Returns:
        The best consumable ID to use, or None if nothing owned.
    """
    return ConsumableSelector.get_any_consumable(owned_consumables)
