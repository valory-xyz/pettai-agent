"""
Olas SDK Interface Layer
Handles all Olas SDK requirements and provides a clean interface for the Pett Agent.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING, Deque, List, Tuple

from aiohttp import web
from collections import deque
import aiohttp
from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

if TYPE_CHECKING:
    from .pett_agent import PettAgent

from .action_recorder import (
    ActionRecorder,
    RecorderConfig,
    DEFAULT_ACTION_REPO_ADDRESS,
)
from .staking_checkpoint import (
    CheckpointConfig,
    StakingCheckpointClient,
    DEFAULT_SAFE_ADDRESS,
    DEFAULT_STATE_FILE,
)
from .agent_performance import AgentPerformanceStore
import subprocess
import mimetypes

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
DEFAULT_FUNDS_CHAIN = "base"
_DEFAULT_NATIVE_TOPUP_FALLBACK_WEI = 80000000000000  # 0.00008 ETH


def _resolve_default_native_topup() -> int:
    """Resolve the default native top-up (wei) from environment overrides."""
    env_candidates = (
        "DEFAULT_NATIVE_TOPUP_WEI",
        "FUND_NATIVE_TOPUP_WEI",
        "CONNECTION_CONFIGS_CONFIG_DEFAULT_NATIVE_TOPUP_WEI",
        "CONNECTION_CONFIGS_CONFIG_FUND_NATIVE_TOPUP_WEI",
    )
    for env_name in env_candidates:
        raw = os.environ.get(env_name)
        if not raw or not str(raw).strip():
            continue
        try:
            value = int(str(raw).strip(), 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return _DEFAULT_NATIVE_TOPUP_FALLBACK_WEI


DEFAULT_NATIVE_TOPUP_WEI = _resolve_default_native_topup()
DEFAULT_NATIVE_THRESHOLD_WEI = DEFAULT_NATIVE_TOPUP_WEI // 2
DEFAULT_STAKING_CONTRACT_ADDRESS = "0x31183503be52391844594b4B587F0e764eB3956E"
ERC20_BALANCE_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
]


class OlasInterface:
    """Interface layer to handle all Olas SDK requirements."""

    def __init__(
        self,
        ethereum_private_key: Optional[str] = None,
        withdrawal_mode: bool = False,
        logger: Optional[logging.Logger] = None,
    ):
        """Initialize Olas interface."""
        self.ethereum_private_key: Optional[str] = ethereum_private_key
        self.withdrawal_mode: bool = withdrawal_mode
        self.logger: logging.Logger = logger or logging.getLogger("olas_interface")
        self.agent: Optional["PettAgent"] = None
        self._health_refresh_lock: asyncio.Lock = asyncio.Lock()
        self._last_refresh_result: Optional[Dict[str, Any]] = None

        # Health check state
        self.last_transition_time: datetime = datetime.now()
        self.is_transitioning: bool = False
        self.health_status: str = "starting"

        # Web server for health checks and UI
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None

        # Environment variables (Olas SDK requirement)
        self.env_vars: Dict[str, str] = self._load_environment_variables()
        self.privy_token_preview: Optional[str] = self._token_preview(
            self.env_vars.get("PRIVY_TOKEN")
        )
        if "PRIVY_TOKEN" in self.env_vars and self.privy_token_preview:
            self.env_vars["PRIVY_TOKEN"] = self.privy_token_preview

        # WebSocket and Pet connection status
        self.websocket_url: str = self.env_vars.get("WEBSOCKET_URL", "wss://ws.pett.ai")
        self.websocket_connected: bool = False
        self.websocket_authenticated: bool = False
        self.pet_connected: bool = False
        self.pet_status: str = "Unknown"
        self.last_websocket_activity: Optional[datetime] = None

        # Registration/auth tracking for UI feedback
        self.registration_required: bool = False
        self.registration_error: Optional[str] = None
        self.last_auth_error: Optional[str] = None

        # Pet data storage
        self.pet_data: Optional[Dict[str, Any]] = None
        self.pet_name: str = "Unknown"
        self.pet_id: str = "Unknown"
        self.pet_balance: str = "0.0000"
        self.pet_hotel_tier: int = 0
        self.pet_dead: bool = False
        self.pet_sleeping: bool = False
        self.agent_ui_behavior: Optional[str] = (
            "Make sure to login to your favourite pet through the Agent Profile to enable autonomous pet sitting actions!"
        )
        self.economy_mode_active: bool = False
        self.economy_mode_message: Optional[str] = None
        self.economy_mode_threshold: float = 350.0

        # Pet stats storage
        self.pet_hunger: float = 0.0
        self.pet_health: float = 0.0
        self.pet_energy: float = 0.0
        self.pet_happiness: float = 0.0
        self.pet_hygiene: float = 0.0
        self.pet_xp: float = 0.0
        self.pet_xp_min: float = 0.0
        self.pet_xp_max: float = 100.0
        self.pet_level: int = 1
        self.pet_updated_at: Optional[datetime] = None

        # Telemetry buffers (in-memory)
        self.sent_messages_history: Deque[Dict[str, Any]] = deque(maxlen=100)
        self.openai_prompts_history: Deque[Dict[str, Any]] = deque(maxlen=50)

        # React static build directory
        self.react_build_dir: Optional[Path] = None
        self.react_enabled: bool = False

        # Funds / performance configuration
        self.agent_eoa_address: Optional[str] = self._derive_agent_address()
        self.agent_safe_address: Optional[str] = self._resolve_safe_address()
        self.fund_requirements: Dict[str, Any] = self._load_fund_requirements()
        self.fund_rpc_urls: Dict[str, str] = self._load_fund_rpc_urls()
        self._funds_web3_clients: Dict[str, Web3] = {}
        self.agent_performance_store = AgentPerformanceStore(logger=self.logger)

        # Optional on-chain components
        self.action_recorder: Optional[ActionRecorder] = None
        self.staking_checkpoint_client: Optional[StakingCheckpointClient] = None
        self.staking_metrics: Optional[Dict[str, Any]] = None
        self._staking_metrics_updated_at: Optional[datetime] = None
        self._initialise_action_recorder()
        self._initialise_staking_checkpoint()

        self.logger.info("🔧 Olas SDK Interface initialized")

    def _get_current_stats_snapshot(self) -> Dict[str, Any]:
        """Build a snapshot dict of the currently stored pet stats."""
        return {
            "hunger": self.pet_hunger,
            "health": self.pet_health,
            "energy": self.pet_energy,
            "happiness": self.pet_happiness,
            "hygiene": self.pet_hygiene,
            "xp": self.pet_xp,
            "xpMin": self.pet_xp_min,
            "xpMax": self.pet_xp_max,
            "level": self.pet_level,
        }

    def _load_environment_variables(self) -> Dict[str, str]:
        """Load Olas SDK standard environment variables."""
        env_vars = {}

        # Standard Olas environment variables
        olas_env_vars = [
            "OPENAI_API_KEY",
            "TELEGRAM_BOT_TOKEN",
            "PRIVY_TOKEN",
            "WEBSOCKET_URL",
            "SAFE_CONTRACT_ADDRESSES",
        ]

        for var in olas_env_vars:
            prefixed_var = f"CONNECTION_CONFIGS_CONFIG_{var}"
            prefixed_value = os.environ.get(prefixed_var)
            if prefixed_value:
                env_vars[var] = prefixed_value

        self.logger.info(f"📋 Loaded {len(env_vars)} environment variables")
        return env_vars

    @staticmethod
    def _token_preview(token: Optional[str]) -> Optional[str]:
        """Create a short preview of sensitive tokens for UI display."""
        if not token:
            return None
        token = token.strip()
        if len(token) <= 12:
            return token
        return f"{token[:6]}...{token[-4:]}"

    def _derive_agent_address(self) -> Optional[str]:
        """Return the checksum EOA derived from the configured private key."""
        private_key = (self.ethereum_private_key or "").strip()
        if not private_key:
            return None
        try:
            account = Account.from_key(private_key)
            return Web3.to_checksum_address(account.address)
        except Exception as exc:
            self.logger.debug("Failed to derive agent address: %s", exc)
            return None

    def _resolve_safe_address(self) -> Optional[str]:
        """Resolve the primary Safe address if configured."""
        candidates = (
            "STAKING_SAFE_ADDRESS",
            "SERVICE_SAFE_ADDRESS",
            "SAFE_CONTRACT_ADDRESS",
            "SAFE_ADDRESS",
            "CONNECTION_CONFIGS_CONFIG_SAFE_CONTRACT_ADDRESS",
        )
        for env_name in candidates:
            value = os.environ.get(env_name)
            if not value or not value.strip():
                continue
            try:
                return Web3.to_checksum_address(value.strip())
            except Exception as exc:
                self.logger.warning(
                    "Invalid safe address in %s ignored: %s", env_name, exc
                )
        mapping_candidates = (
            "CONNECTION_CONFIGS_CONFIG_SAFE_CONTRACT_ADDRESSES",
            "SAFE_CONTRACT_ADDRESSES",
        )
        for env_name in mapping_candidates:
            raw = os.environ.get(env_name)
            if not raw or not raw.strip():
                continue
            try:
                mapping = json.loads(raw)
            except Exception as exc:
                self.logger.warning(
                    "Failed to parse %s for Safe resolution: %s", env_name, exc
                )
                continue
            resolved = self._select_safe_from_mapping(mapping)
            if resolved:
                return resolved
        return None

    def _select_safe_from_mapping(self, mapping: Any) -> Optional[str]:
        """Select a Safe address from a JSON mapping."""
        if not isinstance(mapping, dict) or not mapping:
            return None
        preferred_keys = [
            DEFAULT_FUNDS_CHAIN,
            DEFAULT_FUNDS_CHAIN.replace("_", ""),
            "8453",
            "base-mainnet",
        ]
        for key in preferred_keys:
            value = mapping.get(key)
            if value:
                try:
                    return Web3.to_checksum_address(str(value).strip())
                except Exception:
                    continue
        if len(mapping) == 1:
            value = next(iter(mapping.values()))
            try:
                return Web3.to_checksum_address(str(value).strip())
            except Exception:
                return None
        for value in mapping.values():
            try:
                return Web3.to_checksum_address(str(value).strip())
            except Exception:
                continue
        return None

    def _load_fund_requirements(self) -> Dict[str, Any]:
        """Load fund requirements from env or fall back to defaults."""
        env_value = os.environ.get("FUND_REQUIREMENTS") or os.environ.get(
            "CONNECTION_CONFIGS_CONFIG_FUND_REQUIREMENTS"
        )
        if env_value and env_value.strip():
            try:
                data = json.loads(env_value)
                if isinstance(data, dict):
                    return data
                self.logger.warning("FUND_REQUIREMENTS must be a JSON object")
            except Exception as exc:
                self.logger.error("Failed to parse FUND_REQUIREMENTS: %s", exc)
        return self._build_default_fund_requirements()

    def _build_default_fund_requirements(self) -> Dict[str, Any]:
        """Build a conservative default that covers native gas for the EOA."""
        if not self.agent_eoa_address:
            return {}
        return {
            DEFAULT_FUNDS_CHAIN: {
                self.agent_eoa_address: {
                    ZERO_ADDRESS: {
                        "threshold": str(DEFAULT_NATIVE_THRESHOLD_WEI),
                        "topup": str(DEFAULT_NATIVE_TOPUP_WEI),
                    }
                }
            }
        }

    def _load_fund_rpc_urls(self) -> Dict[str, str]:
        """Load RPC URL overrides per chain for funds calculations."""
        env_value = (
            os.environ.get("FUND_RPC_URLS")
            or os.environ.get("RPC_URLS")
            or os.environ.get("CONNECTION_CONFIGS_CONFIG_RPC_URLS")
        )
        if not env_value or not env_value.strip():
            return {}
        try:
            mapping = json.loads(env_value)
        except Exception as exc:
            self.logger.error("Failed to parse RPC_URLS: %s", exc)
            return {}
        if not isinstance(mapping, dict):
            return {}
        parsed: Dict[str, str] = {}
        for chain, url in mapping.items():
            if not url or not str(url).strip():
                continue
            parsed[str(chain).lower()] = str(url).strip()
        return parsed

    def _get_funds_rpc_url(self, chain: str) -> Optional[str]:
        """Resolve the RPC URL used to fetch balances for a chain."""
        chain_key = chain.lower()
        if chain_key in self.fund_rpc_urls:
            return self.fund_rpc_urls[chain_key]
        env_basename = f"{chain_key.upper().replace('-', '_')}_RPC_URL"
        for candidate in (env_basename, f"CONNECTION_CONFIGS_CONFIG_{env_basename}"):
            value = os.environ.get(candidate)
            if value and value.strip():
                return value.strip()
        if chain_key == DEFAULT_FUNDS_CHAIN:
            return self._resolve_rpc_url()
        return None

    def _get_funds_web3(self, chain: str) -> Web3:
        """Return (and cache) a Web3 client for the requested chain."""
        chain_key = chain.lower()
        if chain_key in self._funds_web3_clients:
            return self._funds_web3_clients[chain_key]
        rpc_url = self._get_funds_rpc_url(chain_key)
        if not rpc_url:
            raise RuntimeError(f"No RPC URL configured for chain '{chain}'")
        try:
            w3 = Web3(Web3.HTTPProvider(rpc_url))
            if not w3.is_connected():
                raise RuntimeError("RPC connection failed")
            try:
                w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            except ValueError:
                pass
        except Exception as exc:
            raise RuntimeError(f"Failed to initialise Web3 for {chain}: {exc}") from exc
        self._funds_web3_clients[chain_key] = w3
        return w3

    def _fetch_asset_balance(
        self, chain: str, address: str, asset: str
    ) -> Tuple[int, int]:
        """Return the balance and decimals for an address/asset pair."""
        w3 = self._get_funds_web3(chain)
        if asset == ZERO_ADDRESS:
            balance = w3.eth.get_balance(address)
            return balance, 18
        contract = w3.eth.contract(address=asset, abi=ERC20_BALANCE_ABI)
        balance = contract.functions.balanceOf(address).call()
        try:
            decimals_raw = contract.functions.decimals().call()
            decimals = int(decimals_raw)
        except Exception:
            decimals = 18
        return balance, decimals

    def _parse_requirement_values(self, state: Dict[str, Any]) -> Tuple[int, int]:
        """Parse threshold/topup integers from the requirement state."""

        def _parse_int(value: Any) -> Optional[int]:
            if value is None:
                return None
            try:
                return int(str(value), 0)
            except Exception:
                return None

        topup = _parse_int(state.get("topup"))
        threshold = _parse_int(state.get("threshold"))
        if topup is None and threshold is None:
            return 0, 0
        if topup is None:
            topup = max(int(threshold or 0) * 2, 0)
        if threshold is None:
            threshold = max(topup // 2, 0)
        return threshold, topup

    def _coerce_address(self, value: Any) -> Optional[str]:
        """Return checksum version of an address when possible."""
        if value is None:
            return None
        try:
            return Web3.to_checksum_address(str(value).strip())
        except Exception:
            self.logger.debug("Invalid address in funds requirements: %s", value)
            return None

    def _allowed_fund_addresses(self) -> Dict[str, str]:
        """Return the canonical addresses we are allowed to request funds for."""
        allowed: Dict[str, str] = {}
        if self.agent_eoa_address:
            allowed[self.agent_eoa_address.lower()] = self.agent_eoa_address
        if self.agent_safe_address:
            allowed[self.agent_safe_address.lower()] = self.agent_safe_address
        return allowed

    def _update_agent_performance_metrics(self) -> None:
        """Persist the latest pet snapshot to the Pearl performance file."""
        store = getattr(self, "agent_performance_store", None)
        if store is None or not store.is_enabled:
            self.logger.warning(
                "Agent performance store is not enabled, skipping update"
            )
            return
        pet_name = (
            self.pet_name if self.pet_name and self.pet_name != "Unknown" else None
        )
        if not pet_name:
            return
        try:
            store.update_pet_metrics(
                pet_name=pet_name,
                is_dead=self.pet_dead,
                agent_ui_behavior=self.agent_ui_behavior,
            )
        except Exception as exc:
            self.logger.debug("Failed to update agent performance metrics: %s", exc)

    def persist_agent_performance_metrics(self) -> None:
        """Public wrapper to ensure the performance file is refreshed on demand."""
        self._update_agent_performance_metrics()

    def _compute_funds_status(self) -> Dict[str, Any]:
        """Compute the funds deficit payload expected by Pearl v1."""
        requirements = self.fund_requirements or {}
        allowed = self._allowed_fund_addresses()
        if not requirements or not allowed:
            return {}

        payload: Dict[str, Dict[str, Dict[str, Dict[str, str]]]] = {}
        for chain_name, addresses in requirements.items():
            if not isinstance(addresses, dict):
                continue
            chain_key = str(chain_name).lower()
            chain_payload: Dict[str, Dict[str, Dict[str, str]]] = {}
            chain_needs_funds = False
            for raw_address, assets in addresses.items():
                if not isinstance(assets, dict):
                    continue
                checksum_address = self._coerce_address(raw_address)
                if not checksum_address:
                    continue
                if checksum_address.lower() not in allowed:
                    continue
                asset_payload: Dict[str, Dict[str, str]] = {}
                for asset_address, state in assets.items():
                    if not isinstance(state, dict):
                        continue
                    checksum_asset = (
                        ZERO_ADDRESS
                        if str(asset_address).lower() in {"0x0", ZERO_ADDRESS.lower()}
                        else self._coerce_address(asset_address)
                    )
                    if not checksum_asset:
                        continue
                    threshold, topup = self._parse_requirement_values(state)
                    if topup <= 0:
                        continue
                    try:
                        balance, decimals = self._fetch_asset_balance(
                            chain_key, checksum_address, checksum_asset
                        )
                    except Exception as exc:
                        self.logger.error(
                            "Failed to fetch %s balance for %s on %s: %s",
                            checksum_asset,
                            checksum_address,
                            chain_key,
                            exc,
                        )
                        continue
                    deficit = 0
                    if balance < threshold:
                        deficit = max(topup - balance, 0)
                    asset_payload[checksum_asset] = {
                        "balance": str(balance),
                        "deficit": str(deficit),
                        "decimals": str(decimals),
                    }
                    if deficit > 0:
                        chain_needs_funds = True
                if asset_payload:
                    chain_payload[checksum_address] = asset_payload
            if chain_needs_funds and chain_payload:
                payload[chain_key] = chain_payload
        return payload

    def register_agent(self, agent: "PettAgent") -> None:
        """Store a reference to the running PettAgent instance."""
        self.agent = agent
        self.logger.debug("Registered PettAgent with Olas interface")

    def get_env_var(self, name: str, default: Optional[str] = None) -> Optional[str]:
        """Get environment variable with Olas SDK prefix handling."""
        # Try direct name first
        value = self.env_vars.get(name, default)
        if value:
            return value

        # Try with CONNECTION_CONFIGS_CONFIG_ prefix
        prefixed_name = f"CONNECTION_CONFIGS_CONFIG_{name}"
        return os.environ.get(prefixed_name, default)

    def update_health_status(self, status: str, is_transitioning: bool = False) -> None:
        """Update health check status."""
        self.health_status = status
        self.is_transitioning = is_transitioning
        if not is_transitioning:
            self.last_transition_time = datetime.now()

        self.logger.debug(
            f"Health status updated: {status} (transitioning: {is_transitioning})"
        )

    def update_websocket_status(
        self,
        connected: bool,
        authenticated: bool = False,
        activity_time: Optional[datetime] = None,
    ) -> None:
        """Update WebSocket connection status."""
        self.websocket_connected = connected
        self.websocket_authenticated = authenticated
        if activity_time is not None:
            self.last_websocket_activity = activity_time
        elif connected:
            self.last_websocket_activity = datetime.now()

        self.logger.debug(
            f"WebSocket status updated: connected={connected}, authenticated={authenticated}"
        )

    def update_pet_status(self, connected: bool, status: str = "Unknown") -> None:
        """Update pet connection status."""
        self.pet_connected = connected
        self.pet_status = status
        self.logger.debug(f"Pet status updated: connected={connected}, status={status}")

    def update_economy_mode_status(
        self, active: bool, message: Optional[str] = None
    ) -> None:
        """Expose the agent's economy mode state for UI warnings."""
        self.economy_mode_active = bool(active)
        self.economy_mode_message = message if active else None
        if active:
            self.logger.warning(
                "⚠️ Economy mode active: %s", message or "insufficient funds"
            )

    def update_registration_state(
        self, required: bool, error: Optional[str] = None
    ) -> None:
        """Track whether the UI needs to prompt for pet registration."""
        self.registration_required = required
        self.registration_error = error
        if required:
            # Clear stale connection flags so UI knows authentication is pending
            self.websocket_authenticated = False
            self.pet_connected = False
        self.logger.debug(
            "Registration state updated: required=%s error=%s",
            required,
            error,
        )

    def update_auth_error(self, error: Optional[str]) -> None:
        """Store the latest authentication error message for UI consumption."""
        self.last_auth_error = error

    def update_pet_data(self, pet_data: Optional[Dict[str, Any]]) -> None:
        """Update pet data with detailed information."""
        # Check for death status transition before updating
        was_dead = self.pet_dead
        self.pet_data = pet_data
        self.pet_updated_at = datetime.now()
        if pet_data and pet_data.get("name"):
            self.pet_name = pet_data.get("name", "Unknown")
            self.pet_id = pet_data.get("id", "Unknown")

            # Format balance from wei to ETH (using the same logic as websocket client)
            raw_balance = pet_data.get("PetTokens", {}).get(
                "tokens", pet_data.get("balance", "0")
            )
            balance_float: Optional[float] = None
            try:
                if isinstance(raw_balance, str):
                    raw_balance = int(raw_balance)
                eth_value = raw_balance / (10**18)
                self.pet_balance = f"{eth_value:.4f}"
                balance_float = eth_value
            except (ValueError, TypeError, ZeroDivisionError):
                self.pet_balance = "0.0000"
                balance_float = None

            self.pet_hotel_tier = pet_data.get("currentHotelTier", 0)
            new_dead_status = pet_data.get("dead", False)
            self.pet_dead = new_dead_status
            self.pet_sleeping = pet_data.get("sleeping", False)

            # Check if pet just died (transition from alive to dead)
            if new_dead_status and not was_dead:
                self.logger.warning(
                    f"💀 Pet {self.pet_name} (ID: {self.pet_id}) has died! "
                    "Actions cannot be performed until the pet is revived."
                )
            elif new_dead_status:
                # Pet is still dead (was already dead)
                self.logger.debug(
                    f"💀 Pet {self.pet_name} (ID: {self.pet_id}) is still dead."
                )

            if (
                balance_float is not None
                and balance_float > self.economy_mode_threshold
                and self.economy_mode_active
            ):
                self.update_economy_mode_status(False, None)

            # Extract and normalize PetStats
            stats = pet_data.get("PetStats", {}) if isinstance(pet_data, dict) else {}
            if isinstance(stats, dict):

                def to_float(v):
                    try:
                        if v is None:
                            return 0.0
                        if isinstance(v, (int, float)):
                            return float(v)
                        return float(str(v))
                    except Exception:
                        return 0.0

                self.pet_hunger = to_float(stats.get("hunger"))
                self.pet_health = to_float(stats.get("health"))
                self.pet_energy = to_float(stats.get("energy"))
                self.pet_happiness = to_float(stats.get("happiness"))
                self.pet_hygiene = to_float(stats.get("hygiene"))
                self.pet_xp = to_float(stats.get("xp"))
                # Optional XP range for progress bar
                self.pet_xp_min = to_float(stats.get("xpMin"))
                self.pet_xp_max = to_float(stats.get("xpMax")) or self.pet_xp_max
                try:
                    self.pet_level = int(
                        stats.get("level", self.pet_level) or self.pet_level
                    )
                except Exception:
                    pass

            self.logger.debug(f"Pet data updated: {self.pet_name} (ID: {self.pet_id})")
            self._update_agent_performance_metrics()
        else:
            # Reset to defaults if no data
            self.pet_name = "Unknown"
            self.pet_id = "Unknown"
            self.pet_balance = "0.0000"
            self.pet_hotel_tier = 0
            self.pet_dead = False
            self.pet_sleeping = False
            self.pet_hunger = 0.0
            self.pet_health = 0.0
            self.pet_energy = 0.0
            self.pet_happiness = 0.0
            self.pet_hygiene = 0.0
            self.pet_xp = 0.0
            self.pet_xp_min = 0.0
            self.pet_xp_max = 100.0
            self.pet_level = 1

    def record_client_send(
        self, message: Dict[str, Any], success: bool, error: Optional[str] = None
    ) -> None:
        try:
            entry: Dict[str, Any] = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "type": message.get("type"),
                "success": bool(success),
            }
            data = message.get("data")
            if data is not None:
                if isinstance(data, dict):
                    entry["data_keys"] = list(data.keys())[:10]
                    # Store actual data for preview (truncated)
                    entry["data_values"] = str(data)[:300] + "..."
                else:
                    entry["data_preview"] = str(data)[:200] + "..."
            if error:
                entry["error"] = str(error)[:200]
            # Do not attach stats here; we will update with post-action stats after pet_update
            self.sent_messages_history.append(entry)
        except Exception as e:
            self.logger.debug(f"Failed to record client send: {e}")

    def update_last_action_stats(self) -> None:
        """Update the most recent action entry with the latest stored pet stats (post-action)."""
        try:
            if not self.sent_messages_history:
                return
            # Action types we display in health recent actions
            actionable_types = {
                "RUB",
                "SHOWER",
                "SLEEP",
                "THROWBALL",
                "CONSUMABLES_USE",
                "CONSUMABLES_BUY",
                "HOTEL_CHECK_IN",
                "HOTEL_CHECK_OUT",
                "HOTEL_BUY",
                "ACCESSORY_USE",
                "ACCESSORY_BUY",
            }

            # Find the most recent actionable entry from the end
            for idx in range(len(self.sent_messages_history) - 1, -1, -1):
                entry = self.sent_messages_history[idx]
                if str(entry.get("type")) in actionable_types:
                    entry["pet_stats"] = self._get_current_stats_snapshot()
                    break
        except Exception as e:
            self.logger.debug(f"Failed to update last action stats: {e}")

    def record_openai_prompt(
        self, kind: str, prompt: str, context: Optional[Dict[str, Any]] = None
    ) -> None:
        try:
            entry: Dict[str, Any] = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "kind": kind,
                "prompt": (prompt or "")[:2000],
            }
            if context:
                entry["context_keys"] = list(context.keys())[:10]
            self.openai_prompts_history.append(entry)
        except Exception as e:
            self.logger.debug(f"Failed to record OpenAI prompt: {e}")

    def get_seconds_since_last_transition(self) -> float:
        """Get seconds since last transition for health check."""
        return (datetime.now() - self.last_transition_time).total_seconds()

    @property
    def is_healthy(self) -> bool:
        """Compute overall health boolean for quick checks.

        Criteria (conservative):
        - Not in an error or stopped state
        - WebSocket connected
        - If transitioning, allow a short grace period (< 60s)
        """
        if self.health_status in {"error", "stopped"}:
            return False
        if not self.websocket_connected:
            return False
        if self.is_transitioning and self.get_seconds_since_last_transition() > 60:
            return False
        return True

    @property
    def health_reason(self) -> str:
        """Return a human-readable reason for the current health status."""
        if self.health_status in {"error", "stopped"}:
            return f"Agent status is '{self.health_status}'"
        if not self.websocket_connected:
            return (
                "WebSocket not connected (agent not authenticated or still connecting)"
            )
        if self.is_transitioning and self.get_seconds_since_last_transition() > 60:
            return f"Agent stuck transitioning for {self.get_seconds_since_last_transition():.0f}s"
        return "Agent is healthy"

    def _resolve_rpc_url(self) -> Optional[str]:
        """Attempt to resolve the RPC URL for action recording."""

        def _lookup_env(name: str, include_prefixed: bool) -> Optional[str]:
            candidates = [name]
            if include_prefixed:
                candidates.append(f"CONNECTION_CONFIGS_CONFIG_{name}")
            for candidate in candidates:
                value = os.environ.get(candidate)
                if value and value.strip():
                    return value.strip()
            return None

        candidate_env_vars = [
            ("ACTION_REPO_RPC_URL", True),
            ("BASE_LEDGER_RPC", True),
            ("CONNECTION_LEDGER_CONFIG_LEDGER_APIS_GNOSIS_ADDRESS", False),
            ("CONNECTION_LEDGER_CONFIG_LEDGER_APIS_ETHEREUM_ADDRESS", False),
            ("CONNECTION_LEDGER_CONFIG_LEDGER_APIS_BASE_ADDRESS", False),
            ("ETH_RPC_URL", True),
            ("RPC_URL", True),
        ]

        for env_name in candidate_env_vars:
            value = _lookup_env(env_name[0], include_prefixed=env_name[1])
            if value:
                return value
        return None

    def _initialise_action_recorder(self) -> None:
        """Initialise the optional action recorder using the agent's credentials."""
        private_key = (self.ethereum_private_key or "").strip()
        if not private_key:
            self.logger.info(
                "Skipping action recorder initialisation: ethereum private key not available"
            )
            return

        rpc_url = self._resolve_rpc_url()
        if not rpc_url:
            self.logger.info(
                "Skipping action recorder initialisation: RPC endpoint not configured"
            )
            return

        contract_address_env = os.environ.get("ACTION_REPO_CONTRACT_ADDRESS")
        contract_address = (contract_address_env or DEFAULT_ACTION_REPO_ADDRESS).strip()
        # The ActionRecorder resolves the Safe from CONNECTION_CONFIGS_CONFIG_SAFE_CONTRACT_ADDRESSES

        try:
            config = RecorderConfig(
                private_key=private_key,
                rpc_url=rpc_url,
                contract_address=contract_address,
            )
            self.action_recorder = ActionRecorder(config=config, logger=self.logger)
        except Exception as exc:
            self.logger.error(f"Failed to initialise action recorder: {exc}")
            self.action_recorder = None

    def get_action_recorder(self) -> Optional[ActionRecorder]:
        """Return the configured action recorder, if available."""
        return self.action_recorder

    def get_staking_checkpoint_client(self) -> Optional[StakingCheckpointClient]:
        """Return the staking checkpoint client, if available."""
        return self.staking_checkpoint_client

    def update_staking_metrics(self, metrics: Optional[Dict[str, Any]]) -> None:
        """Persist latest staking KPI snapshot for health/status endpoints."""
        if metrics is None:
            self.staking_metrics = None
            self._staking_metrics_updated_at = None
            return
        self.staking_metrics = metrics
        self._staking_metrics_updated_at = datetime.now()

    def _initialise_staking_checkpoint(self) -> None:
        """Initialise the staking checkpoint helper when configuration is provided."""
        feature_flag = os.environ.get("ENABLE_STAKING_CHECKPOINTS", "1").strip().lower()
        if feature_flag in {"0", "false", "no"}:
            self.logger.info("Staking checkpoint helper disabled via environment flag")
            return

        private_key = (self.ethereum_private_key or "").strip()
        if not private_key:
            self.logger.info(
                "Skipping staking checkpoint initialisation: ethereum private key not available"
            )
            return

        rpc_url = self._resolve_rpc_url()
        discovered_staking = self._discover_staking_config()
        used_discovered_config = False
        if not rpc_url and discovered_staking:
            rpc_candidate = discovered_staking.get("rpc_url")
            if rpc_candidate:
                rpc_url = rpc_candidate
                used_discovered_config = True
        if not rpc_url:
            self.logger.info(
                "Skipping staking checkpoint initialisation: RPC endpoint not configured"
            )
            return

        staking_address: Optional[str] = None
        staking_env_candidates = (
            "STAKING_TOKEN_CONTRACT_ADDRESS",
            "CONNECTION_CONFIGS_CONFIG_STAKING_TOKEN_CONTRACT_ADDRESS",
            "STAKING_CONTRACT_ADDRESS",
            "STAKING_PROXY_ADDRESS",
            "SERVICE_STAKING_CONTRACT_ADDRESS",
            "CONNECTION_CONFIGS_CONFIG_STAKING_CONTRACT_ADDRESS",
            "STAKING_PROGRAM_ID",
            "CONNECTION_CONFIGS_CONFIG_STAKING_PROGRAM_ID",
        )
        for env_name in staking_env_candidates:
            value = os.environ.get(env_name)
            if value and value.strip():
                staking_address = value.strip()
                self.logger.info(
                    f"[OLAS SDK] Staking contract address found in environment: {staking_address}. We will use this one."
                )
                break

        if not staking_address and discovered_staking:
            staking_candidate = discovered_staking.get(
                "staking_contract_address"
            ) or discovered_staking.get("staking_program_id")
            if staking_candidate:
                staking_address = staking_candidate
                used_discovered_config = True

        if not staking_address:
            staking_address = DEFAULT_STAKING_CONTRACT_ADDRESS
            self.logger.info(
                "Staking contract address not configured; defaulting to %s",
                staking_address,
            )

        safe_address: Optional[str] = None
        safe_env_candidates = (
            "STAKING_SAFE_ADDRESS",
            "SERVICE_SAFE_ADDRESS",
            "SAFE_CONTRACT_ADDRESS",
            "CONNECTION_CONFIGS_CONFIG_SAFE_CONTRACT_ADDRESS",
        )
        for env_name in safe_env_candidates:
            value = os.environ.get(env_name)
            if value and value.strip():
                safe_address = value.strip()
                break
        if not safe_address and discovered_staking:
            safe_candidate = discovered_staking.get("safe_address")
            if safe_candidate:
                safe_address = safe_candidate
                used_discovered_config = True
        safe_address = safe_address or DEFAULT_SAFE_ADDRESS

        liveness_env = os.environ.get(
            "STAKING_LIVENESS_PERIOD_SECONDS"
        ) or os.environ.get("STAKING_LIVENESS_PERIOD")
        liveness_period: Optional[int] = None
        if liveness_env:
            try:
                liveness_period = int(liveness_env.strip())
            except ValueError:
                self.logger.warning(
                    "Invalid staking liveness period provided (%s); ignoring and falling back to contract value",
                    liveness_env,
                )

        state_file_env = os.environ.get("STAKING_CHECKPOINT_STATE_FILE")
        state_file_path = (
            Path(state_file_env).expanduser()
            if state_file_env and state_file_env.strip()
            else DEFAULT_STATE_FILE
        )

        staking_token_address = staking_address
        staking_token_env_candidates = (
            "STAKING_TOKEN_ADDRESS",
            "STAKING_TOKEN_PROXY_ADDRESS",
            "SERVICE_STAKING_CONTRACT_ADDRESS",
            "CONNECTION_CONFIGS_CONFIG_STAKING_TOKEN_ADDRESS",
        )
        for env_name in staking_token_env_candidates:
            value = os.environ.get(env_name)
            if value and value.strip():
                staking_token_address = value.strip()
                break
        if (
            not staking_token_address or staking_token_address == staking_address
        ) and discovered_staking:
            token_candidate = discovered_staking.get("staking_token_address")
            if token_candidate:
                staking_token_address = token_candidate
                used_discovered_config = True

        if not staking_token_address:
            staking_token_address = staking_address

        try:
            config = CheckpointConfig(
                private_key=private_key,
                rpc_url=rpc_url,
                staking_contract_address=staking_address,
                safe_address=safe_address,
                liveness_period=liveness_period,
                state_file=state_file_path,
                staking_token_address=staking_token_address,
            )
            self.staking_checkpoint_client = StakingCheckpointClient(
                config=config, logger=self.logger
            )
        except Exception as exc:
            self.logger.error(f"Failed to initialise staking checkpoint helper: {exc}")
            self.staking_checkpoint_client = None
            return

        if used_discovered_config and discovered_staking:
            self.logger.info(
                "Auto-detected staking configuration from %s (chain=%s)",
                discovered_staking.get("source", "operate services"),
                discovered_staking.get("chain_name"),
            )

    def _discover_staking_config(self) -> Optional[Dict[str, Any]]:
        """Attempt to infer staking configuration from environment variables."""

        def _lookup_env(name: Optional[str]) -> Optional[str]:
            if not name:
                return None
            candidates = [name]
            if not name.startswith("CONNECTION_CONFIGS_CONFIG_"):
                candidates.append(f"CONNECTION_CONFIGS_CONFIG_{name}")
            for candidate in candidates:
                value = os.environ.get(candidate)
                if value and str(value).strip():
                    return str(value).strip()
            return None

        def _lookup(*names: Optional[str]) -> Optional[str]:
            for name in names:
                value = _lookup_env(name)
                if value:
                    return value
            return None

        chain_name = _lookup("STAKING_CHAIN_NAME", "SERVICE_CHAIN_NAME", "CHAIN_NAME")
        rpc_url = _lookup(
            "STAKING_RPC_URL",
            "SERVICE_RPC_URL",
            "CHECKPOINT_RPC_URL",
            "STAKING_LEDGER_RPC",
        )
        raw_service_id = _lookup(
            "SERVICE_ID",
            "STAKING_SERVICE_ID",
            "SERVICE_TOKEN_ID",
            "SERVICE_TOKEN",
        )
        service_id_value = self._parse_int_like(raw_service_id)
        if raw_service_id and service_id_value is None:
            self.logger.warning(
                "Staking SERVICE_ID value %s is not a valid integer", raw_service_id
            )

        staking_program_id = _lookup("STAKING_PROGRAM_ID", "SERVICE_STAKING_PROGRAM_ID")
        staking_contract_address = _lookup(
            "STAKING_CONTRACT_ADDRESS",
            "STAKING_PROXY_ADDRESS",
            "SERVICE_STAKING_CONTRACT_ADDRESS",
            "SERVICE_STAKING_PROXY_ADDRESS",
        )
        staking_token_address = _lookup(
            "STAKING_TOKEN_ADDRESS",
            "STAKING_TOKEN_PROXY_ADDRESS",
            "SERVICE_STAKING_TOKEN_ADDRESS",
            "SERVICE_STAKING_TOKEN_PROXY_ADDRESS",
        )
        safe_address = self._resolve_safe_address()

        missing_required: List[str] = []
        if service_id_value is None or service_id_value < 0:
            missing_required.append("SERVICE_ID")
        has_staking_target = bool(
            staking_contract_address or staking_program_id or staking_token_address
        )
        if not has_staking_target:
            missing_required.append(
                "STAKING_CONTRACT_ADDRESS (or STAKING_PROGRAM_ID / STAKING_TOKEN_ADDRESS)"
            )

        if missing_required:
            self.logger.warning(
                "Staking configuration not provided via environment variables. Missing: %s",
                ", ".join(missing_required),
            )
            return None

        if not staking_contract_address and staking_program_id:
            staking_contract_address = staking_program_id
        if not staking_token_address:
            staking_token_address = staking_contract_address
        elif not staking_contract_address:
            staking_contract_address = staking_token_address

        self.logger.info(
            "Loaded staking configuration from environment (service_id=%s, chain=%s)",
            service_id_value,
            chain_name or "unknown",
        )

        return {
            "source": "environment",
            "chain_name": chain_name,
            "rpc_url": rpc_url,
            "service_id": service_id_value,
            "safe_address": safe_address,
            "staking_program_id": staking_program_id,
            "staking_contract_address": staking_contract_address,
            "staking_token_address": staking_token_address,
        }

    @staticmethod
    def _parse_int_like(value: Any) -> Optional[int]:
        """Coerce integers encoded as strings or hex literals."""
        if value is None:
            return None
        try:
            return int(str(value), 0)
        except Exception:
            return None

    async def _health_check_handler(self, request: web.Request) -> web.Response:
        """Handle health check endpoint (Olas SDK requirement)."""
        seconds_since_transition = self.get_seconds_since_last_transition()
        seconds_since_websocket_activity = None
        if self.last_websocket_activity:
            seconds_since_websocket_activity = (
                datetime.now() - self.last_websocket_activity
            ).total_seconds()

        # Get next-action timing from agent (optional)
        action_timing: Dict[str, Any] = {}
        if self.agent and hasattr(self.agent, "get_action_timing_info"):
            try:
                action_timing = self.agent.get_action_timing_info()
            except Exception:
                action_timing = {}

        refresh_flag = request.rel_url.query.get("refresh", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        refresh_result: Optional[Dict[str, Any]] = None
        if refresh_flag:
            refresh_result = await self._trigger_health_refresh()
        elif self._last_refresh_result:
            refresh_result = self._last_refresh_result

        # Compute environment variable status against expected Olas variables
        expected_env_vars: List[str] = [
            "OPENAI_API_KEY",
            "TELEGRAM_BOT_TOKEN",
            "PRIVY_TOKEN",
            "WEBSOCKET_URL",
        ]
        env_var_messages: Dict[str, str] = {}
        for var in expected_env_vars:
            direct = os.environ.get(var)
            prefixed = os.environ.get(f"CONNECTION_CONFIGS_CONFIG_{var}")
            if not direct and not prefixed:
                env_var_messages[var] = (
                    "Missing; set either the direct variable or its CONNECTION_CONFIGS_CONFIG_ prefixed variant."
                )
        needs_env_update = bool(env_var_messages)

        # Compute agent health placeholders (conservative defaults)
        # Note: These can be enhanced if PettAgent exposes richer telemetry
        agent_health: Dict[str, Any] = {
            "is_making_on_chain_transactions": False,
            "is_staking_kpi_met": False,
            "has_required_funds": False,
            "staking_status": "unknown",
        }

        if self.staking_metrics:
            staking_snapshot = dict(self.staking_metrics)
            agent_health["is_staking_kpi_met"] = bool(
                staking_snapshot.get("threshold_met")
            )
            agent_health["staking_status"] = staking_snapshot.get("status", "unknown")
            agent_health["staking_epoch"] = {
                "service_id": staking_snapshot.get("service_id"),
                "txs_in_epoch": staking_snapshot.get("txs_in_epoch"),
                "required_txs": staking_snapshot.get("required_txs"),
                "txs_remaining": staking_snapshot.get("txs_remaining"),
                "eta_seconds": staking_snapshot.get("seconds_to_epoch_end"),
                "eta_text": staking_snapshot.get("eta_text"),
                "threshold_met": staking_snapshot.get("threshold_met"),
                "updated_at": (
                    self._staking_metrics_updated_at.isoformat()
                    if self._staking_metrics_updated_at
                    else staking_snapshot.get("updated_at")
                ),
            }

        # Derive has_required_funds from known pet balance when available
        try:
            agent_health["has_required_funds"] = float(self.pet_balance) > 0.0
        except Exception:
            pass

        agent_address = self.agent_eoa_address
        if not agent_address:
            derived_address = self._derive_agent_address()
            if derived_address:
                self.agent_eoa_address = derived_address
                agent_address = derived_address

        health_data: Dict[str, Any] = {
            # New required schema (Pearl-compatible)
            "is_healthy": self.is_healthy,
            "health_reason": self.health_reason,
            "seconds_since_last_transition": seconds_since_transition,
            "is_tm_healthy": True,  # Not applicable for Olas SDK agents; report healthy
            "period": 0,
            "reset_pause_duration": 0,
            "rounds": [],  # Not applicable for this agent; ABCI rounds not used
            "is_transitioning_fast": (
                self.is_transitioning and seconds_since_transition < 30
            ),
            "agent_health": agent_health,
            "economy_mode": {
                "active": self.economy_mode_active,
                "message": self.economy_mode_message,
            },
            "rounds_info": {},
            "env_var_status": {
                "needs_update": needs_env_update,
                "env_vars": env_var_messages,
            },
            # Existing detailed data preserved for our UI and debugging
            "status": self.health_status,
            "agent_address": agent_address or "unknown",
            "withdrawal_mode": False,
            "health_refresh": refresh_result,
            "websocket": {
                "url": self.websocket_url,
                "connected": self.websocket_connected,
                "authenticated": self.websocket_authenticated,
                "last_activity_seconds_ago": seconds_since_websocket_activity,
            },
            "pet": {
                "connected": self.pet_connected,
                "status": self.pet_status,
                "name": self.pet_name,
                "id": self.pet_id,
                "balance": self.pet_balance,
                "hotel_tier": self.pet_hotel_tier,
                "dead": self.pet_dead,
                "sleeping": self.pet_sleeping,
                "stats": {
                    "hunger": self.pet_hunger,
                    "health": self.pet_health,
                    "energy": self.pet_energy,
                    "happiness": self.pet_happiness,
                    "hygiene": self.pet_hygiene,
                    "xp": self.pet_xp,
                    "xpMin": self.pet_xp_min,
                    "xpMax": self.pet_xp_max,
                    "level": self.pet_level,
                },
            },
            "pet_last_updated_at": (
                self.pet_updated_at.isoformat() if self.pet_updated_at else None
            ),
            "action_scheduling": action_timing,
            "timestamp": datetime.now().isoformat(),
            "recent": {
                "sent_messages": list(self.sent_messages_history)[-20:],
                "openai_prompts": list(self.openai_prompts_history)[-10:],
                "actions": [
                    m
                    for m in list(self.sent_messages_history)[-50:]
                    if str(m.get("type"))
                    in {
                        "RUB",
                        "SHOWER",
                        "SLEEP",
                        "THROWBALL",
                        "CONSUMABLES_USE",
                        "CONSUMABLES_BUY",
                        "HOTEL_CHECK_IN",
                        "HOTEL_CHECK_OUT",
                        "HOTEL_BUY",
                        "ACCESSORY_USE",
                        "ACCESSORY_BUY",
                    }
                ][-20:],
            },
        }

        return web.json_response(health_data)

    async def _action_history_handler(self, request: web.Request) -> web.Response:
        """Serve the current daily action history for the React dashboard."""
        if request.method != "GET":
            return web.json_response({"error": "Method not allowed"}, status=405)

        if not self.agent or not hasattr(self.agent, "get_daily_action_history"):
            return web.json_response(
                {"error": "Action history unavailable"}, status=503
            )

        try:
            history = self.agent.get_daily_action_history()
        except Exception as exc:
            self.logger.error(f"Failed to load action history: {exc}")
            return web.json_response(
                {"error": "Failed to load action history"}, status=500
            )

        return web.json_response(history)

    async def _agent_ui_handler(self, request: web.Request) -> web.Response:
        """Deprecated: HTML dashboard is replaced by React UI."""
        self.logger.info("HTML dashboard endpoint deprecated; use React UI at /")
        return web.json_response(
            {"error": "deprecated", "use": "/dashboard"}, status=410
        )

    async def _agent_api_handler(self, request: web.Request) -> web.Response:
        """Handle POST requests for agent communication."""
        if request.method == "POST":
            try:
                data = await request.json()
                self.logger.info(f"📨 Received API request: {data}")

                # Handle different API commands
                command = data.get("command")
                if command == "status":
                    return web.json_response(
                        {
                            "status": self.health_status,
                            "is_healthy": self.is_healthy,
                            "timestamp": datetime.now().isoformat(),
                        }
                    )
                elif command == "ping":
                    return web.json_response({"response": "pong"})
                else:
                    return web.json_response(
                        {"error": f"Unknown command: {command}"}, status=400
                    )

            except Exception as e:
                self.logger.error(f"Error handling API request: {e}")
                return web.json_response({"error": str(e)}, status=500)

        return web.json_response({"error": "Method not allowed"}, status=405)

    async def _exit_handler(self, request: web.Request) -> web.Response:
        """Handle exit requests."""
        self.logger.info("🛑 Exiting agent")
        if self.agent:
            await self.agent.shutdown()
        if self.app:
            await self.app.shutdown()
        if self.runner:
            await self.runner.cleanup()
        if self.site:
            await self.site.stop()
        # sys.exit(0)
        return web.json_response({"status": "ok"})

    async def _serve_static_file(self, request: web.Request) -> web.Response:
        """Serve static files from React build directory."""
        if not self.react_build_dir or not self.react_build_dir.exists():
            return web.json_response({"error": "React build not available"}, status=503)

        try:
            # Get the file path from the URL
            file_path = self.react_build_dir / request.path.lstrip("/")

            # Security check: ensure the path is within build directory
            try:
                file_path = file_path.resolve()
                self.react_build_dir.resolve()
                if not str(file_path).startswith(str(self.react_build_dir.resolve())):
                    return web.Response(status=403, text="Forbidden")
            except Exception:
                return web.Response(status=403, text="Forbidden")

            # Check if file exists
            if not file_path.is_file():
                return web.Response(status=404, text="Not Found")

            # Determine content type
            content_type, _ = mimetypes.guess_type(str(file_path))
            if not content_type:
                content_type = "application/octet-stream"

            # Read and return file
            with open(file_path, "rb") as f:
                content = f.read()

            return web.Response(body=content, content_type=content_type)

        except Exception as e:
            self.logger.error(f"❌ Error serving static file {request.path}: {e}")
            return web.Response(status=500, text="Internal Server Error")

    async def _serve_react_app(self, request: web.Request) -> web.Response:
        """Serve React app index.html for SPA routing."""
        if not self.react_build_dir or not self.react_build_dir.exists():
            return web.json_response({"error": "React build not available"}, status=503)

        try:
            index_path = self.react_build_dir / "index.html"

            if not index_path.is_file():
                return web.Response(status=404, text="index.html not found")

            # Read and return index.html
            with open(index_path, "rb") as f:
                content = f.read()

            return web.Response(body=content, content_type="text/html")

        except Exception as e:
            self.logger.error(f"❌ Error serving React app: {e}")
            return web.Response(status=500, text="Internal Server Error")

    async def _login_api_handler(self, request: web.Request) -> web.Response:
        """Handle login API requests from React frontend."""
        if request.method == "POST":
            try:
                data = await request.json()
                privy_token = data.get("privy_token")

                if not privy_token:
                    return web.json_response(
                        {"error": "privy_token is required"}, status=400
                    )

                self.logger.info("🔐 Received login request from React frontend")

                # Update privy token in agent
                if self.agent and hasattr(self.agent, "update_privy_token"):
                    success = await self.agent.update_privy_token(privy_token)

                    return web.json_response(
                        {
                            "success": success,
                            "authenticated": self.websocket_authenticated,
                            "websocket_connected": self.websocket_connected,
                            "pet_connected": self.pet_connected,
                            "pet_name": self.pet_name,
                            "pet_status": self.pet_status,
                            "pet_balance": self.pet_balance,
                            "pet_hotel_tier": self.pet_hotel_tier,
                            "pet": self.pet_data,
                            "requires_registration": self.registration_required,
                            "auth_error": self.last_auth_error,
                            "register_error": self.registration_error,
                        }
                    )
                else:
                    return web.json_response(
                        {"error": "Agent not initialized"}, status=500
                    )

            except Exception as e:
                print(f"❌ Error handling login: {str(e)!s}")
                self.logger.error(f"❌ Error handling login: {str(e)!s}")
                return web.json_response({"error": str(e)}, status=500)

        return web.json_response({"error": "Method not allowed"}, status=405)

    async def _register_api_handler(self, request: web.Request) -> web.Response:
        """Register a new pet for a Privy user via React frontend."""
        if request.method == "POST":
            try:
                data = await request.json()
                privy_token = data.get("privy_token")
                pet_name = data.get("pet_name")

                if not privy_token:
                    return web.json_response(
                        {
                            "success": False,
                            "error": "privy_token is required",
                            "requires_registration": True,
                        },
                        status=400,
                    )

                if not pet_name:
                    return web.json_response(
                        {
                            "success": False,
                            "error": "pet_name is required",
                            "requires_registration": True,
                        },
                        status=400,
                    )

                if self.agent and hasattr(self.agent, "register_pet"):
                    result = await self.agent.register_pet(pet_name, privy_token)
                    response_payload = {
                        **result,
                        "authenticated": self.websocket_authenticated,
                        "pet_connected": self.pet_connected,
                        "pet_name": self.pet_name,
                        "requires_registration": self.registration_required,
                        "auth_error": self.last_auth_error,
                        "register_error": self.registration_error,
                    }
                    status_code = 200 if result.get("success") else 400
                    return web.json_response(response_payload, status=status_code)
                else:
                    return web.json_response(
                        {"success": False, "error": "Agent not initialized"},
                        status=500,
                    )

            except Exception as e:
                self.logger.error(f"❌ Error handling registration: {e}")
                return web.json_response(
                    {"success": False, "error": str(e)}, status=500
                )

        return web.json_response({"error": "Method not allowed"}, status=405)

    async def _logout_api_handler(self, request: web.Request) -> web.Response:
        """Handle logout requests from React frontend."""
        if request.method == "POST":
            try:
                self.logger.info("🔓 Received logout request from React frontend")

                if self.agent and hasattr(self.agent, "logout_privy"):
                    success = await self.agent.logout_privy()
                    if success:
                        self.privy_token_preview = None
                    return web.json_response({"success": success})
                else:
                    return web.json_response(
                        {"error": "Agent not initialized"}, status=500
                    )

            except Exception as e:
                self.logger.error(f"❌ Error handling logout: {e}")
                return web.json_response({"error": str(e)}, status=500)

        return web.json_response({"error": "Method not allowed"}, status=405)

    def _command_exists(self, command: str) -> bool:
        """Check if a command exists in PATH."""
        try:
            subprocess.run(
                ["which", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False

    async def _ensure_npm_dependencies(self, react_dir: Path) -> bool:
        """Ensure React dependencies were installed at build time."""
        node_modules = react_dir / "node_modules"
        package_json = react_dir / "package.json"

        if not package_json.exists():
            self.logger.error(f"❌ No package.json found in {react_dir}")
            return False

        if node_modules.exists():
            self.logger.info("✅ Found node_modules installed ahead of time")
            return True

        self.logger.error(
            "❌ React dependencies missing. Install and build the frontend during your "
            "Docker image or binary build process before running the agent."
        )
        return False

    async def build_react_app(self, react_dir: Path) -> bool:
        """Build/detect React app. Attempts to build if no pre-built assets exist."""
        try:
            if not react_dir.exists():
                self.logger.warning(f"⚠️ React directory not found: {react_dir}")
                return False

            build_dir = react_dir / "build"
            build_index = build_dir / "index.html"

            if build_index.exists():
                self.logger.info(f"✅ Found pre-built React assets at {build_dir}")
                return True

            # No pre-built assets found - try to build React from Python
            self.logger.info(
                "🔨 React build not found, attempting to build from Python..."
            )
            return await self._build_react_from_python(react_dir)

        except Exception as e:
            self.logger.error(f"❌ Error locating React build: {e}")
            return False

    async def _build_react_from_python(self, react_dir: Path) -> bool:
        """Build React app using yarn or npm from Python."""
        import subprocess

        package_json = react_dir / "package.json"
        if not package_json.exists():
            self.logger.error(f"❌ No package.json found in {react_dir}")
            return False

        node_modules = react_dir / "node_modules"

        # Determine package manager
        use_yarn = (react_dir / "yarn.lock").exists()
        pkg_manager = "yarn" if use_yarn else "npm"

        if not self._command_exists(pkg_manager):
            self.logger.error(
                f"❌ {pkg_manager} not found in PATH. Cannot build React."
            )
            return False

        try:
            # Install dependencies if needed
            if not node_modules.exists():
                self.logger.info(
                    f"📦 Installing React dependencies with {pkg_manager}..."
                )
                install_cmd = [pkg_manager, "install"]
                if use_yarn:
                    install_cmd.append("--frozen-lockfile")
                result = subprocess.run(
                    install_cmd,
                    cwd=str(react_dir),
                    capture_output=True,
                    text=True,
                    timeout=300,  # 5 minute timeout
                )
                if result.returncode != 0:
                    self.logger.error(
                        f"❌ Failed to install dependencies: {result.stderr}"
                    )
                    return False
                self.logger.info("✅ Dependencies installed successfully")

            # Build React app
            self.logger.info(f"🏗️ Building React app with {pkg_manager}...")
            build_cmd = [pkg_manager, "run", "build"]
            result = subprocess.run(
                build_cmd,
                cwd=str(react_dir),
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )

            if result.returncode != 0:
                self.logger.error(f"❌ React build failed: {result.stderr}")
                return False

            # Verify build output
            build_index = react_dir / "build" / "index.html"
            if build_index.exists():
                self.logger.info("✅ React app built successfully")
                return True
            else:
                self.logger.error("❌ React build completed but index.html not found")
                return False

        except subprocess.TimeoutExpired:
            self.logger.error("❌ React build timed out")
            return False
        except Exception as e:
            self.logger.error(f"❌ React build failed: {e}")
            return False

    async def start_web_server(
        self, port: int = 8716, enable_react: bool = True
    ) -> None:
        """Start web server for health checks and UI (Olas SDK requirement)."""
        try:
            self.app = web.Application()

            # Try to build and serve React static files if enabled
            if enable_react:
                react_dir = Path(__file__).parent.parent / "frontend"
                react_dir = react_dir.resolve()  # Resolve to absolute path
                self.logger.debug(f"🔍 Looking for React frontend at: {react_dir}")
                if react_dir.exists():
                    self.logger.info(f"🎨 Loading React frontend from {react_dir}...")
                    react_built = await self.build_react_app(react_dir)
                    if react_built:
                        self.react_build_dir = react_dir / "build"
                        self.react_enabled = True
                        self.logger.info(
                            f"✅ React UI enabled, serving from {self.react_build_dir}"
                        )
                    else:
                        self.logger.warning(
                            f"⚠️ React build missing at {react_dir / 'build'}. "
                            "Will attempt to build or fallback to healthcheck on root."
                        )
                else:
                    self.logger.info(f"ℹ️ No React frontend found at {react_dir}")

            # Deprecated HTML dashboard removed in favor of React UI
            self.app.router.add_get(
                "/api/health", self._health_check_handler
            )  # JSON health
            self.app.router.add_get("/api/status", self._health_check_handler)
            self.app.router.add_get("/healthcheck", self._health_check_handler)
            self.app.router.add_get("/funds-status", self._funds_status_handler)
            self.app.router.add_get("/api/funds-status", self._funds_status_handler)
            self.app.router.add_get("/api/action-history", self._action_history_handler)
            self.app.router.add_post("/api/login", self._login_api_handler)
            self.app.router.add_post("/api/register", self._register_api_handler)
            self.app.router.add_post("/api/logout", self._logout_api_handler)
            self.app.router.add_post("/api/chat", self._chat_api_handler)
            self.app.router.add_post("/", self._agent_api_handler)
            self.app.router.add_get("/exit", self._exit_handler)

            self.logger.debug(
                f"🔄 Adding React static file routes: {self.react_enabled}"
            )
            # Add React static file routes (if enabled)
            if self.react_enabled and self.react_build_dir:
                # Serve static files
                self.app.router.add_get("/static/{tail:.*}", self._serve_static_file)
                self.app.router.add_get("/assets/{tail:.*}", self._serve_static_file)

                # Serve React routes (SPA fallback to index.html)
                self.app.router.add_get("/login", self._serve_react_app)
                self.app.router.add_get("/login/{tail:.*}", self._serve_react_app)
                self.app.router.add_get("/privy-login", self._serve_react_app)
                self.app.router.add_get(
                    "/privy-login/{tail:.*}", self._serve_react_app
                )
                self.app.router.add_get("/dashboard", self._serve_react_app)
                self.app.router.add_get("/dashboard/{tail:.*}", self._serve_react_app)
                self.app.router.add_get("/action-history", self._serve_react_app)
                self.app.router.add_get(
                    "/action-history/{tail:.*}", self._serve_react_app
                )

                # Root serves React
                self.app.router.add_get("/", self._serve_react_app)
            else:
                # Fallback to JSON health if React not available
                self.app.router.add_get("/", self._health_check_handler)

            # Start server (disable aiohttp access logs)
            self.runner = web.AppRunner(self.app, access_log=None)
            await self.runner.setup()

            # Bind to 0.0.0.0 to allow access from outside Docker container
            self.site = web.TCPSite(self.runner, "0.0.0.0", port)
            await self.site.start()

            self.logger.info(
                f"🌐 Web server started on http://0.0.0.0:{port} (access via http://localhost:{port} or http://127.0.0.1:{port})"
            )
            if self.react_enabled:
                self.logger.info(
                    f"🎨 React App available at http://localhost:{port}/ or http://127.0.0.1:{port}/ (Dashboard at /dashboard)"
                )
                self.logger.info(
                    f"🏥 Health API: http://localhost:{port}/api/health (also http://127.0.0.1:{port}/api/health)"
                )
            else:
                self.logger.info(
                    f"🏥 Health API: http://localhost:{port}/api/health (also http://127.0.0.1:{port}/api/health)"
                )

        except Exception as e:
            self.logger.error(f"Failed to start web server: {e}")
            raise

    async def stop_web_server(self) -> None:
        """Stop the web server."""
        try:
            if self.site:
                await self.site.stop()
            if self.runner:
                await self.runner.cleanup()
            self.logger.info("🛑 Web server stopped")
        except Exception as e:
            self.logger.error(f"Error stopping web server: {e}")

    def log_to_file(self, message: str, level: str = "INFO") -> None:
        """Log message to log.txt file (Olas SDK requirement)."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
        log_entry = f"[{timestamp}] [{level}] [agent] {message}\n"

        level_value = getattr(logging, level.upper(), logging.INFO)
        self.logger.log(level_value, message)

        try:
            log_file_path = Path(__file__).resolve().parent / "log.txt"
            log_file_path.parent.mkdir(parents=True, exist_ok=True)
            with log_file_path.open("a", encoding="utf-8") as f:
                f.write(log_entry)
        except Exception as exc:
            self.logger.error("Failed to write to log.txt: %s", exc)

    def handle_withdrawal(self) -> bool:
        """Handle withdrawal mode (Olas SDK optional requirement)."""
        if not self.withdrawal_mode:
            return False

        self.logger.info("💰 Withdrawal mode activated")
        # TODO: Implement actual withdrawal logic
        # This would typically:
        # 1. Stop normal operations
        # 2. Withdraw funds from Safe to Agent EOA
        # 3. Prepare for shutdown

        return True

    async def _trigger_health_refresh(self) -> Dict[str, Any]:
        """Invoke the agent-level AUTH ping to refresh websocket + pet snapshot."""
        if not self.agent:
            failure = {"success": False, "error": "agent_unavailable"}
            self._last_refresh_result = failure
            return failure

        refresh_result: Dict[str, Any]
        async with self._health_refresh_lock:
            try:
                refresh_result = await self.agent.run_auth_health_check()
            except Exception as exc:
                self.logger.error("Health refresh failed: %s", exc)
                refresh_result = {"success": False, "error": str(exc)}

            self._last_refresh_result = refresh_result
            return refresh_result

    async def _funds_status_handler(self, request: web.Request) -> web.Response:
        """Return current funding requirements following Pearl v1 schema."""
        try:
            payload = await asyncio.to_thread(self._compute_funds_status)
        except Exception as exc:
            self.logger.error("Failed to compute funds status: %s", exc)
            return web.json_response({"error": "funds_status_unavailable"}, status=500)
        return web.json_response(payload, status=200)

    async def _chat_api_handler(self, request: web.Request) -> web.Response:
        """Simple chat proxy using OpenAI for frontend chat."""
        if request.method != "POST":
            return web.json_response({"error": "Method not allowed"}, status=405)
        try:
            data = await request.json()
            user_message = (data.get("message") or "").strip()
            context_text = (data.get("context") or "").strip()
            if not user_message:
                return web.json_response({"error": "message is required"}, status=400)

            # Resolve OpenAI API key from env (supports prefixed variant)
            api_key = self.get_env_var("OPENAI_API_KEY")
            if not api_key:
                return web.json_response(
                    {"error": "OpenAI API key not configured"}, status=500
                )

            # Build lightweight context from recent actions and current stats
            recent_actions = []
            try:
                recent_actions = list(self.sent_messages_history)[-10:]
            except Exception:
                recent_actions = []

            def _action_to_phrase(entry: Dict[str, Any]) -> str:
                t = str(entry.get("type", "")).upper()
                if t == "SHOWER":
                    return "I just took a bath."
                if t == "SLEEP":
                    return "I went to sleep and rested."
                if t == "THROWBALL":
                    return "I played with the ball."
                if t == "RUB":
                    return "I got some pets and rubs."
                if t == "CONSUMABLES_USE":
                    return "I used a consumable to feel better."
                if t == "CONSUMABLES_BUY":
                    return "I bought a consumable for later."
                if t == "HOTEL_CHECK_IN":
                    return "I checked into the hotel."
                if t == "HOTEL_CHECK_OUT":
                    return "I checked out of the hotel."
                if t == "HOTEL_BUY":
                    return "I upgraded my hotel tier."
                if t == "ACCESSORY_USE":
                    return "I used an accessory."
                if t == "ACCESSORY_BUY":
                    return "I bought a new accessory."
                return f"I performed an action: {t or 'unknown'}."

            action_phrases = "\n".join(
                f"- { _action_to_phrase(a) }"
                for a in recent_actions
                if isinstance(a, dict)
            )

            # Current stats snapshot
            stats_snapshot = {
                "hunger": self.pet_hunger,
                "health": self.pet_health,
                "energy": self.pet_energy,
                "happiness": self.pet_happiness,
                "hygiene": self.pet_hygiene,
                "xp": self.pet_xp,
                "level": self.pet_level,
            }

            system_prompt = (
                "You are Pett, a playful, caring virtual pet. Speak in first person, be warm and concise. "
                "Use recent actions and current stats to ground your replies when relevant. "
                "Avoid long explanations; keep messages under 2 short sentences unless the user asks for detail."
            )

            # Compose OpenAI chat request
            body = {
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "system",
                        "content": f"Pet name: {self.pet_name}\nRecent actions:\n{action_phrases or '- (none)'}\nCurrent stats: {stats_snapshot}\nExtra context: {context_text}",
                    },
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.7,
            }

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            # Use aiohttp client to call OpenAI
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    "https://api.openai.com/v1/chat/completions",
                    json=body,
                    headers=headers,
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        self.logger.error(f"OpenAI error {resp.status}: {text}")
                        return web.json_response(
                            {"error": "OpenAI request failed"}, status=500
                        )
                    payload = await resp.json()
                    content = (
                        (
                            ((payload or {}).get("choices") or [{}])[0].get("message")
                            or {}
                        ).get("content")
                        or ""
                    ).strip()

            # Record prompt for UI parity
            try:
                self.record_openai_prompt("chat_user", user_message)
                self.record_openai_prompt("chat_pet", content)
            except Exception:
                pass

            return web.json_response({"response": content})
        except Exception as e:
            self.logger.error(f"❌ Chat handler error: {e}")
            return web.json_response({"error": str(e)}, status=500)
