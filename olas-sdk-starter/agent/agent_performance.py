"""
Helper utilities to keep the agent_performance.json in sync with Pearl v1 expectations.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional


class AgentPerformanceStore:
    """Utility to persist Pearl agent performance metrics."""

    FILENAME = "agent_performance.json"

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        probable_root_path_names_env = [
            "CONNECTION_CONFIGS_CONFIG_STORE_PATH",
            "CONNECTION_CONFIGS_STORE_PATH",
            "STORE_PATH",
        ]
        for env_name in probable_root_path_names_env:
            value = os.environ.get(env_name)
            if value and value.strip():
                self._root_path = Path(value).expanduser()
                break
        if not self._root_path:
            self._logger.warning(
                "No store path configured, agent performance metrics will be on the ./persistent_data directory"
            )
            self._root_path = Path("./persistent_data")
        self._store_path = self._root_path / self.FILENAME
        self._file_path = self._store_path
        self._ensure_initialized()

    @property
    def is_enabled(self) -> bool:
        """Return True when the store path is configured."""
        return self._file_path is not None

    def _ensure_initialized(self) -> None:
        if not self.is_enabled:
            return
        try:
            self._store_path.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
            if not self._file_path.exists():  # type: ignore[union-attr]
                self._write_payload(self._default_payload())
        except Exception as exc:
            self._logger.warning(
                "Failed to initialize agent performance store: %s", exc
            )

    def _default_payload(self) -> Dict[str, Any]:
        return {"timestamp": None, "metrics": [], "agent_behavior": None}

    def _read_payload(self) -> Dict[str, Any]:
        if not self.is_enabled:
            return self._default_payload()
        try:
            with self._file_path.open("r", encoding="utf-8") as handle:  # type: ignore[union-attr]
                data = json.load(handle)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return self._default_payload()

    def _write_payload(self, payload: Dict[str, Any]) -> None:
        if not self.is_enabled:
            return
        try:
            self._store_path.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
            with self._file_path.open("w", encoding="utf-8") as handle:  # type: ignore[union-attr]
                json.dump(payload, handle, indent=2)
        except Exception as exc:
            self._logger.warning("Failed to write agent performance file: %s", exc)

    def update_pet_metrics(
        self,
        pet_name: Optional[str],
        is_dead: Optional[bool],
        agent_ui_behavior: Optional[str],
    ) -> None:
        """Update the metrics payload with the latest pet info."""
        if not self.is_enabled:
            return
        if not pet_name:
            return

        payload = self._read_payload()
        payload["timestamp"] = int(time.time())

        if agent_ui_behavior is not None and agent_ui_behavior != "":
            payload["agent_behavior"] = agent_ui_behavior
        else:
            payload["agent_behavior"] = None

        metrics = [
            {
                "name": "Pet Name",
                "is_primary": False,
                "description": "Latest pet name received from Pett servers.",
                "value": str(pet_name),
            }
        ]
        if is_dead is not None:
            metrics.append(
                {
                    "name": "Pet Status",
                    "is_primary": False,
                    "description": "Reports whether the pet is alive or dead.",
                    "value": "dead" if is_dead else "alive",
                }
            )

        payload["metrics"] = metrics
        self._write_payload(payload)
