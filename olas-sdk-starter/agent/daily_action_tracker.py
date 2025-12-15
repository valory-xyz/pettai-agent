import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DailyActionTracker:
    """Persist and expose per-epoch action progress for the Pett agent."""

    def __init__(
        self,
        storage_path: Path,
        required_actions: int = 8,
        *,
        reset_on_start: bool = False,
    ) -> None:
        self.storage_path = storage_path
        self.required_actions = max(0, required_actions)
        self._reset_on_start = bool(reset_on_start)
        self._state: Dict[str, Any] = {
            "epoch": self._current_epoch(),
            "actions": [],
        }
        self._load_state()

    def _current_epoch(self) -> str:
        """Return the current UTC day identifier used for action epochs."""
        now = datetime.now(timezone.utc)
        return now.strftime("%Y-%m-%d")

    def _ensure_current_epoch(self) -> None:
        """Reset tracked actions when a new day/epoch begins."""
        epoch = self._current_epoch()
        if self._state.get("epoch") == epoch:
            return
        self._state = {"epoch": epoch, "actions": []}
        self._save_state()

    def _load_state(self) -> None:
        """Load persisted action history if it matches the current epoch."""
        try:
            if not self.storage_path.exists():
                self.storage_path.parent.mkdir(parents=True, exist_ok=True)
                self._save_state()
                return
            data = json.loads(self.storage_path.read_text())
            if not isinstance(data, dict):
                raise ValueError("tracker state must be a dict")
            self._state = data
            self._ensure_current_epoch()
            if self._reset_on_start:
                # Ignore persisted action counts; start fresh on boot
                self._state["actions"] = []
                self._save_state()
        except Exception as exc:
            logger.warning("Failed to load daily action tracker state: %s", exc)
            self._state = {"epoch": self._current_epoch(), "actions": []}

    def _save_state(self) -> None:
        """Persist the in-memory tracker state to disk."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            serialized = json.dumps(self._state, indent=2, sort_keys=True)
            self.storage_path.write_text(serialized)
        except Exception as exc:
            logger.warning("Failed to persist daily action tracker state: %s", exc)

    def record_action(
        self, action_name: str, *, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Register a successful action execution for the current epoch."""
        if not action_name:
            return
        self._ensure_current_epoch()
        entry: Dict[str, Any] = {
            "name": action_name.upper(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if metadata:
            entry["metadata"] = metadata
        actions: List[Dict[str, Any]] = self._state.setdefault("actions", [])
        actions.append(entry)
        self._save_state()

    def actions_completed(self) -> int:
        """Return the number of tracked actions for the current epoch."""
        self._ensure_current_epoch()
        return len(self._state.get("actions", []))

    def actions_remaining(self) -> int:
        """Return how many actions are needed to reach the daily requirement."""
        completed = self.actions_completed()
        return max(self.required_actions - completed, 0)

    def has_met_required_actions(self) -> bool:
        """Return True once the minimum required actions have been satisfied."""
        return self.actions_completed() >= self.required_actions

    def reset_for_new_epoch(self, epoch_identifier: Optional[str] = None) -> None:
        """Reset action counter for a new staking epoch.

        Args:
            epoch_identifier: Optional identifier for the new epoch (uses UTC date if not provided).
        """
        new_epoch = epoch_identifier or self._current_epoch()
        prev_epoch = self._state.get("epoch")
        prev_count = len(self._state.get("actions", []))
        logger.info(
            "🔄 Resetting verified on-chain tx counter for new epoch: %s → %s (had %d verified txs)",
            prev_epoch,
            new_epoch,
            prev_count,
        )
        self._state = {"epoch": new_epoch, "actions": []}
        self._save_state()

    def snapshot(self) -> Dict[str, Any]:
        """Return a shallow copy of the current state for telemetry."""
        self._ensure_current_epoch()
        return {
            "epoch": self._state.get("epoch"),
            "required_actions": self.required_actions,
            "completed": self.actions_completed(),
            "remaining": self.actions_remaining(),
            "actions": list(self._state.get("actions", [])),
        }
