"""
Pett Agent - Olas SDK Integration
Main agent class that integrates your existing Pett Agent logic with Olas SDK requirements.
"""

import os
import asyncio
import logging
import random
import time
import re
from pathlib import Path
from typing import (
    Optional,
    TypedDict,
    Dict,
    Any,
    Union,
    List,
    Callable,
    Awaitable,
    Tuple,
)
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Import your existing logic
from .pett_websocket_client import PettWebSocketClient
from .pett_tools import PettTools
from .telegram_bot import PetTelegramBot
from .olas_interface import OlasInterface
from .decision_engine import PetDecisionEngine
from .daily_action_tracker import DailyActionTracker
from .staking_checkpoint import DEFAULT_LIVENESS_PERIOD

# Load environment variables
load_dotenv()


class PettAgent:
    """Main Pett Agent class with Olas SDK integration."""

    LOW_THRESHOLD = 70.0
    LOW_ENERGY_THRESHOLD = 25.0
    WAKE_ENERGY_THRESHOLD = 65.0
    POST_KPI_SLEEP_TRIGGER = 85.0
    POST_KPI_SLEEP_TARGET = 80.0
    REQUIRED_ACTIONS_PER_EPOCH = 8
    CRITICAL_CORE_STATS: Tuple[str, ...] = ("hunger", "health", "hygiene", "happiness")
    CRITICAL_STAT_THRESHOLD = 5.0
    ECONOMY_BALANCE_THRESHOLD = 350.0
    CONSUMABLE_CACHE_TTL = timedelta(minutes=5)
    HEALTH_CONSUMABLE_PRIORITY: Tuple[str, ...] = (
        "SMALL_POTION",
        "POTION",
        "LARGE_POTION",
        "SALAD",
    )
    KNOWN_FOOD_BLUEPRINTS: Tuple[str, ...] = (
        "BURGER",
        "SALAD",
        "STEAK",
        "COOKIE",
        "PIZZA",
        "SUSHI",
    )
    POTION_STAT_KEYS: Tuple[str, ...] = (
        "hunger",
        "health",
        "hygiene",
        "happiness",
        "energy",
    )

    def __init__(
        self,
        olas_interface: OlasInterface,
        logger: logging.Logger,
        is_production: bool = True,
    ):
        """Initialize the Pett Agent."""
        self.olas = olas_interface
        self.logger = logger
        self.is_production = is_production
        self.running = False
        self.olas.register_agent(self)

        # Your existing components
        self.websocket_client: Optional[PettWebSocketClient] = None
        self.telegram_bot: Optional[PetTelegramBot] = None
        self.pett_tools: Optional[PettTools] = None
        self.decision_engine: Optional[PetDecisionEngine] = None

        # Configuration
        self.telegram_token = (
            self.olas.get_env_var("TELEGRAM_BOT_TOKEN") or ""
        ).strip()
        self._telegram_token_valid = self._is_valid_telegram_token(self.telegram_token)
        self.privy_token = (self.olas.get_env_var("PRIVY_TOKEN") or "").strip()
        self.websocket_url = self.olas.get_env_var("WEBSOCKET_URL", "wss://ws.pett.ai")

        self.logger.info("🐾 Pett Agent initialized")
        # Action scheduler configuration
        self.action_interval_minutes: float = (
            7 if is_production else 1
        )  # should be 7 minutes in prod
        self.next_action_at: Optional[datetime] = None
        self.last_action_at: Optional[datetime] = None
        self._checkpoint_check_interval: timedelta = timedelta(minutes=7)
        self._next_checkpoint_check_at: datetime = datetime.now()

        # Flag to indicate we're waiting for React login
        self.waiting_for_react_login: bool = False
        self._low_health_recovery_in_progress: bool = False
        # Control mid-interval staking KPI logging
        self._mid_interval_logged: bool = True
        self._staking_kpi_log_suppressed: bool = False
        self._auth_refresh_lock: asyncio.Lock = asyncio.Lock()
        self._epoch_checkpoint_lock: asyncio.Lock = asyncio.Lock()
        self._last_health_refresh: Optional[datetime] = None
        self._last_known_epoch_end_ts: Optional[int] = None
        self._last_checkpointed_epoch_end_ts: Optional[int] = None
        self._epoch_length_seconds: Optional[int] = None
        tracker_path = Path("logs") / "daily_action_state.json"
        self._daily_action_tracker = DailyActionTracker(
            tracker_path, required_actions=self.REQUIRED_ACTIONS_PER_EPOCH
        )
        self._economy_mode_active: bool = False
        self._owned_consumables_cache: Dict[str, "PettAgent.OwnedConsumable"] = {}
        self._owned_consumables_updated_at: Optional[datetime] = None
        self._consumables_cache_ttl: timedelta = self.CONSUMABLE_CACHE_TTL

    def get_daily_action_history(self) -> Dict[str, Any]:
        """Return the current snapshot of the daily action tracker."""
        try:
            return self._daily_action_tracker.snapshot()
        except Exception as exc:
            self.logger.warning("Failed to read daily action history: %s", exc)
            return {
                "epoch": None,
                "required_actions": 0,
                "completed": 0,
                "remaining": 0,
                "actions": [],
            }

    # TypedDicts for pet data shape
    class PetTokensDict(TypedDict, total=False):
        petID: str
        tokens: str
        ethTokens: str
        solanaTokens: str
        useSolana: bool
        depositedTokens: str

    class PetStatsDict(TypedDict, total=False):
        petID: str
        hunger: Union[str, int, float]
        health: Union[str, int, float]
        hygiene: Union[str, int, float]
        energy: Union[str, int, float]
        happiness: Union[str, int, float]
        xp: Union[str, int, float]
        level: int
        xpMax: Union[str, int, float]
        xpMin: Union[str, int, float]

    class PetDataDict(TypedDict, total=False):
        id: str
        name: str
        userID: str
        sleeping: bool
        dead: bool
        god: bool
        active: bool
        currentHotelTier: int
        deadTime: Union[str, None]
        inRiskOfDeathTime: Union[str, None]
        PetTokens: "PettAgent.PetTokensDict"
        PetStats: "PettAgent.PetStatsDict"

    class OwnedConsumable(TypedDict, total=False):
        blueprint_id: str
        quantity: int
        type: str
        name: str

    @staticmethod
    def _is_valid_telegram_token(token: str) -> bool:
        """Return True if the token matches the expected bot token format."""
        if not token:
            return False
        return bool(re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token))

    async def initialize(self) -> bool:
        """Initialize all agent components."""
        try:
            self.logger.info("🚀 Initializing Pett Agent components...")
            self.olas.update_health_status("initializing", is_transitioning=True)

            # Start Olas web server for health checks
            await self.olas.start_web_server()

            # Initialize WebSocket client (but don't fail if token is expired)
            if self.privy_token:
                self.logger.info("🔌 Initializing WebSocket client...")
                self.websocket_client = PettWebSocketClient(
                    websocket_url=self.websocket_url
                )
                try:
                    self.websocket_client.set_action_recorder(
                        self.olas.get_action_recorder()
                    )
                    try:
                        recorder = self.olas.get_action_recorder()
                        if recorder and recorder.is_enabled:
                            addr_preview = "unknown"
                            if recorder.account_address:
                                aa = recorder.account_address
                                addr_preview = f"{aa[:6]}...{aa[-4:]}"
                            self.logger.info(
                                "🧾 On-chain action recorder ENABLED: contract=%s rpc=%s agent=%s",
                                recorder.contract_address,
                                recorder.rpc_url,
                                addr_preview,
                            )
                        else:
                            self.logger.info(
                                "🧾 On-chain action recorder DISABLED (missing key or RPC)"
                            )
                    except Exception as e:
                        self.logger.error("❌ Failed to set action recorder: %s", e)
                        pass
                except Exception as e:
                    self.logger.error("❌ Failed to set action recorder: %s", e)
                    pass
                # Wire outgoing message telemetry to Olas
                try:

                    def _recorder_msg(
                        m: Dict[str, Any], success: bool, err: Optional[str]
                    ) -> None:
                        self.olas.record_client_send(m, success=success, error=err)

                    self.websocket_client.set_telemetry_recorder(_recorder_msg)
                except Exception:
                    pass

                # Try to connect and authenticate (but don't fail if token expired)
                self.logger.info(
                    "🔐 Attempting authentication with environment token..."
                )
                connected = await self.websocket_client.connect_and_authenticate()
                if connected:
                    self.logger.info("✅ WebSocket connected and authenticated")

                    # Update Olas interface with WebSocket status
                    self.olas.update_websocket_status(
                        connected=True, authenticated=True
                    )

                    # Set OpenAI API key for decision engine
                    openai_key = self.olas.get_env_var("OPENAI_API_KEY")
                    if openai_key:
                        os.environ["OPENAI_API_KEY"] = openai_key
                        self.logger.info(
                            f"🔑 OpenAI API key configured: {openai_key[:5]}...{openai_key[-5:]}"
                        )
                    else:
                        self.logger.warning(
                            "⚠️ No OpenAI API key found - AI features will be limited"
                        )

                    # Initialize Decision Engine and Pett Tools
                    self.decision_engine = PetDecisionEngine(self.websocket_client)
                    # Wire prompt recorder to Olas
                    try:

                        def _recorder_prompt(
                            kind: str, prompt: str, ctx: Optional[Dict[str, Any]]
                        ) -> None:
                            self.olas.record_openai_prompt(kind, prompt, context=ctx)

                        self.decision_engine.set_prompt_recorder(_recorder_prompt)
                    except Exception:
                        pass
                    self.pett_tools = self.decision_engine.pett_tools
                    self.logger.info("🛠️ Decision Engine and Pett Tools initialized")

                    # React to server-side errors (e.g., low health) with recovery actions
                    try:
                        if self.websocket_client:
                            self.websocket_client.register_message_handler(
                                "error", self._on_client_error_message
                            )
                        # Keep Olas pet data in sync on live updates
                        self.websocket_client.register_message_handler(
                            "pet_update", self._on_client_pet_update_message
                        )
                        # Update olas interface when auth_result is received (check for death)

                        async def _handle_auth_result_for_olas(
                            message: Dict[str, Any],
                        ) -> None:
                            """Update olas interface with pet data from auth_result message."""
                            try:
                                # Extract pet data from auth_result message
                                if "data" in message:
                                    data = message.get("data", {})
                                    pet_data = data.get("pet", {})
                                else:
                                    pet_data = message.get("pet", {})

                                if pet_data:
                                    # Update olas interface with pet data (this will check for death)
                                    self.olas.update_pet_data(pet_data)
                            except Exception as exc:
                                self.logger.debug(
                                    f"Error updating olas interface from auth_result: {exc}"
                                )

                        self.websocket_client.register_message_handler(
                            "auth_result", _handle_auth_result_for_olas
                        )
                    except Exception:
                        pass

                    # Try to get pet status
                    try:
                        pet_status_result = self.pett_tools.get_pet_status()
                        if "❌" not in pet_status_result:
                            pet_connected = True
                            # Extract a summary from the pet status
                            if "Pet Status:" in pet_status_result:
                                pet_status = "Active"
                            else:
                                pet_status = "Connected"

                            # Also get and update the actual pet data
                            if (
                                self.websocket_client
                                and self.websocket_client.is_connected()
                            ):
                                pet_data = self.websocket_client.get_pet_data()
                                if pet_data:
                                    self.olas.update_pet_data(pet_data)
                                    self.logger.debug(
                                        f"Initial pet data updated: {pet_data.get('name', 'Unknown')}"
                                    )
                        else:
                            pet_connected = False
                            pet_status = "Error"
                            # Clear pet data on error
                            self.olas.update_pet_data(None)

                        self.olas.update_pet_status(pet_connected, pet_status)
                    except Exception as e:
                        self.logger.debug(f"Could not get pet status: {e}")
                        self.olas.update_pet_status(False, "Unknown")
                        self.olas.update_pet_data(None)
                else:
                    self.logger.info(
                        "⏸️  Environment token authentication failed (expired or invalid)"
                    )
                    self.logger.info(
                        "✨ Waiting for user to login via React app at http://localhost:8716/ (also available via http://127.0.0.1:8716/)"
                    )
                    self.olas.update_websocket_status(
                        connected=False, authenticated=False
                    )
                    # Keep websocket_client initialized for later use
                    self.waiting_for_react_login = True
            else:
                self.logger.info("ℹ️  No PRIVY_TOKEN in environment")
                self.logger.info(
                    "✨ Waiting for user to login via React app at http://localhost:8716/ (also available via http://127.0.0.1:8716/)"
                )
                # Initialize WebSocket client for later use
                self.websocket_client = PettWebSocketClient(
                    websocket_url=self.websocket_url
                )
                try:
                    self.websocket_client.set_action_recorder(
                        self.olas.get_action_recorder()
                    )
                    try:
                        recorder2 = self.olas.get_action_recorder()
                        if recorder2 and recorder2.is_enabled:
                            addr_preview2 = "unknown"
                            if recorder2.account_address:
                                aa2 = recorder2.account_address
                                addr_preview2 = f"{aa2[:6]}...{aa2[-4:]}"
                            self.logger.info(
                                "🧾 On-chain action recorder ENABLED: contract=%s rpc=%s agent=%s",
                                recorder2.contract_address,
                                recorder2.rpc_url,
                                addr_preview2,
                            )
                        else:
                            self.logger.info(
                                "🧾 On-chain action recorder DISABLED (missing key or RPC)"
                            )
                    except Exception:
                        pass
                except Exception:
                    pass
                try:

                    def _recorder_msg(
                        m: Dict[str, Any], success: bool, err: Optional[str]
                    ) -> None:
                        self.olas.record_client_send(m, success=success, error=err)

                    self.websocket_client.set_telemetry_recorder(_recorder_msg)
                except Exception:
                    pass
                try:
                    # Register handler to update olas interface when auth_result is received
                    async def _handle_auth_result_for_olas(
                        message: Dict[str, Any],
                    ) -> None:
                        """Update olas interface with pet data from auth_result message."""
                        try:
                            # Extract pet data from auth_result message
                            if "data" in message:
                                data = message.get("data", {})
                                pet_data = data.get("pet", {})
                            else:
                                pet_data = message.get("pet", {})

                            if pet_data:
                                # Update olas interface with pet data (this will check for death)
                                self.olas.update_pet_data(pet_data)
                        except Exception as exc:
                            self.logger.debug(
                                f"Error updating olas interface from auth_result: {exc}"
                            )

                    self.websocket_client.register_message_handler(
                        "auth_result", _handle_auth_result_for_olas
                    )
                except Exception:
                    pass
                self.waiting_for_react_login = True

            # Initialize Telegram bot if token is available
            if self.telegram_token and self._telegram_token_valid:
                self.logger.info("🤖 Initializing Telegram bot...")
                try:
                    # Share WebSocket client and decision engine to avoid duplicates
                    self.telegram_bot = PetTelegramBot(
                        websocket_client=self.websocket_client,
                        decision_engine=self.decision_engine,
                    )
                    # Start Telegram bot in background
                    asyncio.create_task(self._run_telegram_bot())
                    self.logger.info(
                        "✅ Telegram bot initialized with shared components"
                    )
                except Exception as e:
                    self.logger.error(f"❌ Failed to initialize Telegram bot: {e}")
            else:
                if self.telegram_token and not self._telegram_token_valid:
                    self.logger.info(
                        "ℹ️ Telegram integration is optional and not required to run Pett Agent. A valid TELEGRAM_BOT_TOKEN was not provided, so Telegram features will stay disabled until a valid token is configured."
                    )
                else:
                    self.logger.info(
                        "ℹ️ Telegram integration is optional and not required to run Pett Agent. A TELEGRAM_BOT_TOKEN was not provided, so Telegram features will be unavailable while the agent keeps running."
                    )

            self.olas.update_health_status("running", is_transitioning=False)
            self.logger.info("✅ Pett Agent initialization complete")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Pett Agent: {e}")
            self.olas.update_health_status("error", is_transitioning=False)
            return False

    async def _run_telegram_bot(self):
        """Run Telegram bot in background."""
        try:
            if self.telegram_bot:
                self.logger.info("🤖 Starting Telegram bot...")
                await self.telegram_bot.run()
        except Exception as e:
            self.logger.error(
                f"❌ Error in Telegram bot (optional component, Pett Agent keeps running without Telegram): {e}"
            )

    async def _check_withdrawal_mode(self):
        """Check and handle withdrawal mode."""
        if self.olas.withdrawal_mode:
            self.logger.info("💰 Withdrawal mode detected")
            if self.olas.handle_withdrawal():
                self.logger.info("💰 Withdrawal completed, shutting down...")
                self.running = False

    async def _health_monitor(self):
        """Monitor agent health and update status."""
        while self.running:
            try:
                # Check WebSocket connection
                if self.websocket_client:
                    if not self.websocket_client.is_connected():
                        # Skip reconnection if waiting for React login
                        if self.waiting_for_react_login:
                            self.logger.debug(
                                "⏸️  Waiting for user to login via React - skipping reconnection"
                            )
                            await asyncio.sleep(30)
                            continue

                        self.logger.warning(
                            "⚠️ WebSocket disconnected, attempting reconnection..."
                        )
                        self.olas.update_health_status(
                            "reconnecting", is_transitioning=True
                        )
                        self.olas.update_websocket_status(
                            connected=False, authenticated=False
                        )

                        # Try to reconnect
                        connected = (
                            await self.websocket_client.connect_and_authenticate()
                        )
                        if connected:
                            self.logger.info("✅ WebSocket reconnected")

                            self.olas.update_health_status(
                                "running", is_transitioning=False
                            )
                            self.olas.update_websocket_status(
                                connected=True, authenticated=True
                            )

                            # Try to get updated pet status
                            try:
                                pet_status_result = self.pett_tools.get_pet_status()
                                if "❌" not in pet_status_result:
                                    pet_connected = True
                                    if "Pet Status:" in pet_status_result:
                                        pet_status = "Active"
                                    else:
                                        pet_status = "Connected"
                                else:
                                    pet_connected = False
                                    pet_status = "Error"
                                self.olas.update_pet_status(pet_connected, pet_status)
                            except Exception as e:
                                self.logger.debug(
                                    f"Could not get pet status after reconnect: {e}"
                                )
                                self.olas.update_pet_status(False, "Unknown")
                        else:
                            self.logger.error("❌ WebSocket reconnection failed")
                            self.olas.update_health_status(
                                "error", is_transitioning=False
                            )
                            self.olas.update_websocket_status(
                                connected=False, authenticated=False
                            )
                            self.olas.update_pet_status(False, "Disconnected")

                # Check for withdrawal mode
                await self._check_withdrawal_mode()

                # Sleep for health check interval
                await asyncio.sleep(30)  # Check every 30 seconds

            except Exception as e:
                self.logger.error(f"❌ Error in health monitor: {e}")
                await asyncio.sleep(10)  # Shorter sleep on error

    def _configure_websocket_client_for_token(self, token: str) -> None:
        """Ensure the websocket client exists and is configured for the provided token."""
        if not token:
            return

        if not self.websocket_client:
            self.logger.info("🔌 Creating WebSocket client with new Privy token")
            self.websocket_client = PettWebSocketClient(
                websocket_url=self.websocket_url, privy_token=token
            )
            try:
                self.websocket_client.set_action_recorder(
                    self.olas.get_action_recorder()
                )
                try:
                    recorder = self.olas.get_action_recorder()
                    if recorder and recorder.is_enabled:
                        addr_preview = "unknown"
                        if recorder.account_address:
                            addr_preview = (
                                f"{recorder.account_address[:6]}..."
                                f"{recorder.account_address[-4:]}"
                            )
                        self.logger.info(
                            "🧾 On-chain action recorder ENABLED: contract=%s rpc=%s agent=%s",
                            recorder.contract_address,
                            recorder.rpc_url,
                            addr_preview,
                        )
                    else:
                        self.logger.info(
                            "🧾 On-chain action recorder DISABLED (missing key or RPC)"
                        )
                except Exception:
                    pass
            except Exception:
                pass
            try:

                def _recorder_msg(
                    message: Dict[str, Any], success: bool, err: Optional[str]
                ) -> None:
                    self.olas.record_client_send(message, success=success, error=err)

                self.websocket_client.set_telemetry_recorder(_recorder_msg)
            except Exception:
                pass
            try:
                # Register handler to update olas interface when auth_result is received
                async def _handle_auth_result_for_olas(message: Dict[str, Any]) -> None:
                    """Update olas interface with pet data from auth_result message."""
                    try:
                        # Extract pet data from auth_result message
                        if "data" in message:
                            data = message.get("data", {})
                            pet_data = data.get("pet", {})
                        else:
                            pet_data = message.get("pet", {})

                        if pet_data:
                            # Update olas interface with pet data (this will check for death)
                            self.olas.update_pet_data(pet_data)
                    except Exception as exc:
                        self.logger.debug(
                            f"Error updating olas interface from auth_result: {exc}"
                        )

                self.websocket_client.register_message_handler(
                    "auth_result", _handle_auth_result_for_olas
                )
            except Exception:
                pass
        else:
            self.websocket_client.set_privy_token(token)
            try:
                self.websocket_client.set_action_recorder(
                    self.olas.get_action_recorder()
                )
            except Exception:
                pass
            try:
                # Register handler to update olas interface when auth_result is received
                async def _handle_auth_result_for_olas(message: Dict[str, Any]) -> None:
                    """Update olas interface with pet data from auth_result message."""
                    try:
                        # Extract pet data from auth_result message
                        if "data" in message:
                            data = message.get("data", {})
                            pet_data = data.get("pet", {})
                        else:
                            pet_data = message.get("pet", {})

                        if pet_data:
                            # Update olas interface with pet data (this will check for death)
                            self.olas.update_pet_data(pet_data)
                    except Exception as exc:
                        self.logger.debug(
                            f"Error updating olas interface from auth_result: {exc}"
                        )

                self.websocket_client.register_message_handler(
                    "auth_result", _handle_auth_result_for_olas
                )
            except Exception:
                pass

    async def update_privy_token(
        self, privy_token: str, *, max_retries: int = 3, auth_timeout: int = 10
    ) -> bool:
        """Update the Privy token at runtime and refresh WebSocket state."""
        token = (privy_token or "").strip()
        if not token:
            self.logger.error("❌ Received empty Privy token from UI")
            return False

        self.logger.info("🔐 Updating Privy token and refreshing WebSocket connection")

        # Reset registration prompts and auth error state before attempting login
        try:
            self.olas.update_registration_state(False, None)
            self.olas.update_auth_error(None)
        except Exception:
            pass

        # Clear waiting flag since we have a new token
        self.waiting_for_react_login = False

        # Persist the token for other components
        self.privy_token = token
        os.environ["PRIVY_TOKEN"] = token

        token_preview = f"{token[:6]}...{token[-4:]}" if len(token) > 12 else token
        self.olas.env_vars["PRIVY_TOKEN"] = token_preview

        self._configure_websocket_client_for_token(token)

        client = self.websocket_client
        if client is None:
            self.logger.error("❌ WebSocket client unavailable after configuration")
            self.olas.update_websocket_status(connected=False, authenticated=False)
            return False

        connected = await client.refresh_token_and_reconnect(
            token, max_retries=max_retries, auth_timeout=auth_timeout
        )
        if not connected:
            self.logger.error(
                "❌ Failed to authenticate WebSocket with new Privy token"
            )
            last_error = None
            try:
                if hasattr(client, "get_last_auth_error"):
                    last_error = client.get_last_auth_error()
            except Exception:
                last_error = None
            if last_error:
                try:
                    self.olas.update_auth_error(last_error)
                except Exception:
                    pass
            requires_registration = self._is_registration_error(last_error)
            if requires_registration:
                # Attempt automatic registration with a default or configured name
                pet_name = self._get_default_pet_name()

                self.logger.info(
                    "🆕 Attempting automatic pet registration with name: %s", pet_name
                )

                try:
                    reg_success, reg_response = await client.register_privy(
                        pet_name, token, timeout=max(20, auth_timeout + 5)
                    )
                except Exception as reg_exc:
                    reg_success = False
                    reg_response = None
                    self.logger.error("❌ Registration threw an exception: %s", reg_exc)

                if not reg_success:
                    # Surface registration error and keep UI in registration-required state
                    try:
                        err_text = None
                        if isinstance(reg_response, dict):
                            err_text = reg_response.get("error") or reg_response.get(
                                "data", {}
                            ).get("error")
                        err_text = (
                            err_text
                            or getattr(client, "get_last_action_error", lambda: None)()
                            or last_error
                            or "Registration failed"
                        )
                        self.olas.update_registration_state(True, err_text)
                        self.olas.update_auth_error(err_text)
                    except Exception:
                        pass
                    self.olas.update_websocket_status(
                        connected=client.is_connected(), authenticated=False
                    )
                    return False

                # Registration reported success; re-authenticate with the same token
                self.logger.info("🔐 Re-authenticating after successful registration")
                connected_after_register = await client.refresh_token_and_reconnect(
                    token, max_retries=max_retries, auth_timeout=auth_timeout
                )
                if not connected_after_register:
                    err_after = None
                    try:
                        err_after = client.get_last_auth_error()
                    except Exception:
                        pass
                    self.logger.error(
                        "❌ Authentication failed after registration: %s", err_after
                    )
                    try:
                        self.olas.update_auth_error(
                            err_after or "Authentication failed after registration"
                        )
                        self.olas.update_registration_state(False, None)
                    except Exception:
                        pass
                    self.olas.update_websocket_status(
                        connected=client.is_connected(), authenticated=False
                    )
                    return False

                # Treat as overall success
                self.logger.info("✅ Registration + authentication successful")
                self.olas.update_websocket_status(connected=True, authenticated=True)
                # Update pet data if available
                try:
                    pet_data = client.get_pet_data()
                    if pet_data:
                        self.olas.update_pet_data(pet_data)
                except Exception:
                    pass
                self.olas.update_registration_state(False, None)
                self.olas.update_auth_error(None)

                # Ensure OpenAI API key is set
                try:
                    openai_key = self.olas.get_env_var("OPENAI_API_KEY")
                    if openai_key:
                        os.environ["OPENAI_API_KEY"] = openai_key
                except Exception:
                    pass

                # Wire up decision engine and tools if needed
                if self.decision_engine:
                    self.decision_engine.websocket_client = client
                    try:
                        self.decision_engine.pett_tools.set_client(client)
                    except Exception:
                        pass
                    self.pett_tools = self.decision_engine.pett_tools
                else:
                    self.decision_engine = PetDecisionEngine(client)
                    try:

                        def _recorder_prompt2(
                            kind: str, prompt: str, ctx: Optional[Dict[str, Any]]
                        ) -> None:
                            self.olas.record_openai_prompt(kind, prompt, context=ctx)

                        self.decision_engine.set_prompt_recorder(_recorder_prompt2)
                    except Exception:
                        pass
                    self.pett_tools = self.decision_engine.pett_tools

                # Refresh pet status once more for UI
                try:
                    pet_status_result = self.pett_tools.get_pet_status()
                    if "❌" not in pet_status_result:
                        pet_connected = True
                        pet_status = (
                            "Active"
                            if "Pet Status:" in pet_status_result
                            else "Connected"
                        )
                        try:
                            pd = client.get_pet_data()
                            if pd:
                                self.olas.update_pet_data(pd)
                        except Exception:
                            pass
                    else:
                        pet_connected = False
                        pet_status = "Error"
                        self.olas.update_pet_data(None)
                    self.olas.update_pet_status(pet_connected, pet_status)
                except Exception:
                    self.olas.update_pet_status(True, "Connected")
                # Continue to standard success return below
                return True

            # Non-registration failure path
            self.olas.update_websocket_status(
                connected=client.is_connected(), authenticated=False
            )
            return False

        # self.logger.info("✅ WebSocket re-authenticated with updated Privy token")
        self.olas.update_websocket_status(connected=True, authenticated=True)
        self.olas.update_health_status("running", is_transitioning=False)

        # Ensure OpenAI API key is set
        openai_key = self.olas.get_env_var("OPENAI_API_KEY")
        if openai_key:
            os.environ["OPENAI_API_KEY"] = openai_key

        if self.decision_engine:
            self.decision_engine.websocket_client = client
            self.decision_engine.pett_tools.set_client(client)
            # Ensure recorder remains wired
            try:

                def _recorder_prompt2(
                    kind: str, prompt: str, ctx: Optional[Dict[str, Any]]
                ) -> None:
                    self.olas.record_openai_prompt(kind, prompt, context=ctx)

                self.decision_engine.set_prompt_recorder(_recorder_prompt2)
            except Exception:
                pass
            self.pett_tools = self.decision_engine.pett_tools
        else:
            self.decision_engine = PetDecisionEngine(client)
            try:

                def _recorder_prompt3(
                    kind: str, prompt: str, ctx: Optional[Dict[str, Any]]
                ) -> None:
                    self.olas.record_openai_prompt(kind, prompt, context=ctx)

                self.decision_engine.set_prompt_recorder(_recorder_prompt3)
            except Exception:
                pass
            self.pett_tools = self.decision_engine.pett_tools

        try:
            pet_status_result = self.pett_tools.get_pet_status()
            if "❌" not in pet_status_result:
                pet_connected = True
                pet_status = (
                    "Active" if "Pet Status:" in pet_status_result else "Connected"
                )
                pet_data = client.get_pet_data()
                if pet_data:
                    self.olas.update_pet_data(pet_data)
            else:
                pet_connected = False
                pet_status = "Error"
                self.olas.update_pet_data(None)
            self.olas.update_pet_status(pet_connected, pet_status)
        except Exception as e:
            self.logger.debug(f"Could not refresh pet status after Privy login: {e}")
            self.olas.update_pet_status(True, "Connected")

        return True

    def _get_default_pet_name(self) -> str:
        """Choose a default pet name using env var or a generated fallback."""
        # Fallback randomized name
        return f"MyPett{random.randint(1, 1000000)}"

    @staticmethod
    def _is_registration_error(error: Optional[str]) -> bool:
        if not error:
            return False
        lowered = error.lower()
        registration_indicators = [
            "user not found",
            "pet not found",
            "no pet",
            "needs registration",
        ]
        return any(indicator in lowered for indicator in registration_indicators)

    async def register_pet(
        self,
        pet_name: str,
        privy_token: str,
        *,
        timeout: int = 20,
    ) -> Dict[str, Any]:
        """Register a new pet for the Privy user when no existing pet is found."""
        name = (pet_name or "").strip()
        token = (privy_token or "").strip()

        if not token:
            error_msg = "Privy token is required for registration"
            self.logger.error(error_msg)
            self.olas.update_registration_state(True, error_msg)
            self.olas.update_auth_error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "requires_registration": True,
            }

        if not name:
            error_msg = "Pet name is required"
            self.logger.error(error_msg)
            self.olas.update_registration_state(True, error_msg)
            return {
                "success": False,
                "error": error_msg,
                "requires_registration": True,
            }

        try:
            self.logger.info("🆕 Registering new pet with name: %s", name)

            self.waiting_for_react_login = False
            self.privy_token = token
            os.environ["PRIVY_TOKEN"] = token
            token_preview = f"{token[:6]}...{token[-4:]}" if len(token) > 12 else token
            self.olas.env_vars["PRIVY_TOKEN"] = token_preview

            self._configure_websocket_client_for_token(token)

            if not self.websocket_client:
                error_msg = "WebSocket client unavailable for registration"
                self.logger.error(error_msg)
                self.olas.update_registration_state(True, error_msg)
                return {
                    "success": False,
                    "error": error_msg,
                    "requires_registration": True,
                }

            register_success, response = await self.websocket_client.register_privy(
                name, token, timeout=timeout
            )

            if not register_success:
                potential_errors: List[Optional[str]] = [
                    self.websocket_client.get_last_action_error(),
                    self.websocket_client.get_last_auth_error(),
                ]
                if isinstance(response, dict):
                    potential_errors.append(response.get("error"))
                    data_section = response.get("data")
                    if isinstance(data_section, dict):
                        potential_errors.append(data_section.get("error"))
                error_msg = next(
                    (msg for msg in potential_errors if msg), "Registration failed"
                )
                self.logger.error("❌ Pet registration failed: %s", error_msg)
                self.olas.update_registration_state(True, error_msg)
                self.olas.update_auth_error(error_msg)
                self.olas.update_websocket_status(
                    connected=self.websocket_client.is_connected(), authenticated=False
                )
                return {
                    "success": False,
                    "error": error_msg,
                    "requires_registration": True,
                }

            payload: Dict[str, Any] = {}
            if isinstance(response, dict):
                payload = response.get("data", response)
            user_payload = payload.get("user") if isinstance(payload, dict) else None
            pet_payload = payload.get("pet") if isinstance(payload, dict) else None

            auth_success = await self.update_privy_token(token)
            if not auth_success:
                fallback_error: Optional[str] = None
                if self.websocket_client:
                    fallback_error = self.websocket_client.get_last_auth_error()
                error_msg = fallback_error or "Authentication failed after registration"
                self.logger.error(
                    "❌ Authentication after registration failed: %s", error_msg
                )
                self.olas.update_auth_error(error_msg)
                return {
                    "success": False,
                    "error": error_msg,
                    "requires_registration": False,
                    "user": user_payload,
                    "pet": pet_payload,
                }

            pet_data = (
                self.websocket_client.get_pet_data() if self.websocket_client else None
            )
            if pet_data:
                self.olas.update_pet_data(pet_data)

            self.olas.update_registration_state(False, None)
            self.olas.update_auth_error(None)

            auth_result = response or {"success": True, "pet": pet_data}

            return {
                "success": True,
                "requires_registration": False,
                "user": user_payload,
                "pet": pet_data or pet_payload,
                "auth_result": auth_result,
                "pet_name": (
                    (pet_data or pet_payload or {}).get("name", name)
                    if isinstance(pet_data or pet_payload, dict)
                    else name
                ),
            }

        except Exception as exc:
            error_msg = f"Registration error: {exc}"
            self.logger.error("❌ Unexpected error during registration: %s", exc)
            self.olas.update_registration_state(True, error_msg)
            self.olas.update_auth_error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "requires_registration": True,
            }

    async def logout_privy(self) -> bool:
        """Clear Privy token, disconnect, and return to pre-login state."""
        try:
            self.logger.info("🔓 Logging out: clearing Privy token and disconnecting")

            # Enter waiting state for next React login
            self.waiting_for_react_login = True

            # Clear stored token and environment
            self.privy_token = ""
            try:
                if "PRIVY_TOKEN" in os.environ:
                    del os.environ["PRIVY_TOKEN"]
            except Exception:
                pass

            # Update Olas visible env snapshot
            try:
                if "PRIVY_TOKEN" in self.olas.env_vars:
                    self.olas.env_vars.pop("PRIVY_TOKEN", None)
            except Exception:
                pass

            # Tear down websocket auth and disconnect
            if self.websocket_client:
                try:
                    self.websocket_client.set_privy_token("")
                    # Clear saved auth token to prevent automatic reconnection
                    self.websocket_client.clear_saved_auth_token()
                except Exception:
                    pass
                try:
                    await self.websocket_client.disconnect()
                except Exception:
                    pass

            # Reset runtime status
            self.olas.update_websocket_status(connected=False, authenticated=False)
            self.olas.update_pet_status(False, "Disconnected")
            self.olas.update_pet_data(None)
            self.olas.update_health_status("running", is_transitioning=False)
            self.olas.update_registration_state(False, None)
            self.olas.update_auth_error(None)

            self.logger.info("✅ Logout complete; awaiting React login")
            return True
        except Exception as e:
            self.logger.error(f"❌ Logout failed: {e}")
            return False

    async def run_auth_health_check(self, timeout: int = 8) -> Dict[str, Any]:
        """Trigger a lightweight AUTH call to sync websocket + pet data for UI polling."""
        result: Dict[str, Any] = {
            "success": False,
            "websocket_connected": bool(
                self.websocket_client and self.websocket_client.is_connected()
            ),
            "websocket_authenticated": bool(
                self.websocket_client and self.websocket_client.is_authenticated()
            ),
        }

        client = self.websocket_client
        if not client:
            result["reason"] = "websocket_unavailable"
            return result

        token = (self.privy_token or "").strip()
        if not token:
            result["reason"] = "privy_token_missing"
            return result

        async with self._auth_refresh_lock:
            auth_success = await client.auth_ping(token, timeout=timeout)
            result["success"] = bool(auth_success)
            result["websocket_connected"] = client.is_connected()
            result["websocket_authenticated"] = client.is_authenticated()

            if auth_success:
                try:
                    pet_data = client.get_pet_data()
                    if pet_data:
                        # Update pet data (this will check for death status)
                        self.olas.update_pet_data(pet_data)
                        result["pet"] = pet_data

                        # Explicitly check if pet is dead after AUTH state update
                        if pet_data.get("dead", False):
                            self.logger.warning(
                                f"💀 Pet death detected after AUTH health check: "
                                f"{pet_data.get('name', 'Unknown')} (ID: {pet_data.get('id', 'Unknown')}) "
                                "is dead. Actions cannot be performed until the pet is revived."
                            )
                except Exception as exc:
                    self.logger.debug(
                        "Health refresh: failed to capture latest pet snapshot: %s", exc
                    )
                self._last_health_refresh = datetime.now()
                result["refreshed_at"] = self._last_health_refresh.isoformat()
                self.olas.update_websocket_status(
                    connected=client.is_connected(),
                    authenticated=client.is_authenticated(),
                )
                self.olas.update_health_status("running", is_transitioning=False)
            else:
                result["reason"] = client.get_last_auth_error() or "auth_failed"

        return result

    async def _pet_action_loop(self):
        """Main pet action loop - your existing logic."""
        while self.running:
            try:
                # Initialize next_action_at lazily
                if self.next_action_at is None:
                    self.next_action_at = datetime.now()

                if self.websocket_client and self.websocket_client.is_authenticated():
                    # Your existing pet management logic can go here

                    # Get pet status periodically
                    if self.pett_tools:
                        try:
                            # If it's not yet time, sleep just until the next action
                            now = datetime.now()
                            if self.next_action_at and now < self.next_action_at:
                                await self._maybe_log_mid_interval(now)
                                sleep_seconds = max(
                                    (self.next_action_at - now).total_seconds(), 1
                                )
                                await asyncio.sleep(min(sleep_seconds, 30))
                                continue

                            pet_status_result = self.pett_tools.get_pet_status()
                            if "❌" not in pet_status_result:
                                self.logger.debug("🐾 Pet agent running action...")

                                pet_connected = True
                                if "Pet Status:" in pet_status_result:
                                    pet_status = "Active"
                                else:
                                    pet_status = "Connected"

                                # Also get and update the actual pet data
                                if (
                                    self.websocket_client
                                    and self.websocket_client.is_connected()
                                ):
                                    pet_data = self.websocket_client.get_pet_data()
                                    if pet_data:
                                        self.olas.update_pet_data(pet_data)
                                        self.logger.debug(
                                            f"Pet data updated: {pet_data}"
                                        )

                                        # Decide and perform actions based on current state
                                        try:
                                            await self._decide_and_perform_actions(pet_data)  # type: ignore[arg-type]
                                            # Update scheduler timestamps
                                            self.last_action_at = datetime.now()
                                            self.next_action_at = (
                                                self.last_action_at
                                                + timedelta(
                                                    minutes=self.action_interval_minutes
                                                )
                                            )
                                            self._mid_interval_logged = False
                                        except Exception as e:
                                            self.logger.debug(
                                                f"Action decision error: {e}"
                                            )
                            else:
                                pet_connected = False
                                pet_status = "Error"
                                # Clear pet data on error
                                self.olas.update_pet_data(None)

                            self.olas.update_pet_status(pet_connected, pet_status)
                            self.logger.debug(f"Pet status updated: {pet_status}")
                        except Exception as e:
                            self.logger.debug(f"Pet tools error: {e}")
                            self.olas.update_pet_status(False, "Error")
                            self.olas.update_pet_data(None)
                    else:
                        self.logger.error("❌ No WebSocket client or PettTools found")
                        self.olas.update_pet_status(False, "Disconnected")
                        self.olas.update_pet_data(None)

                # Idle sleep; keep modest to allow shutdown responsiveness
                await asyncio.sleep(5)

            except Exception as e:
                self.logger.error(f"❌ Error in pet action loop: {e}")
                await asyncio.sleep(30)  # Sleep on error

            await self._maybe_call_staking_checkpoint()

    def _to_float(self, value: Union[str, int, float, None]) -> float:
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value))
        except Exception:
            return 0.0

    def _all_core_stats_below_threshold(
        self, stats: Dict[str, Any], threshold: float
    ) -> bool:
        """Check whether all critical stats fall below a threshold."""
        values: List[float] = []
        for key in self.CRITICAL_CORE_STATS:
            if key not in stats:
                continue
            raw_value = stats.get(key)
            if raw_value is None:
                continue
            try:
                numeric_value = float(str(raw_value))
            except Exception:
                continue
            values.append(numeric_value)

        if not values:
            return False

        return all(value < threshold for value in values)

    async def _recover_low_health(self) -> bool:
        """Attempt to recover health using SMALL_POTION or SALAD.

        Preference:
        1) Use SMALL_POTION via CONSUMABLES_USE (blueprintID: SMALL_POTION)
        2) Fallback to eat SALAD (blueprintID: SALAD) via use_consumable
        """
        if self._low_health_recovery_in_progress:
            return False
        self._low_health_recovery_in_progress = True
        try:
            if not self.websocket_client:
                return False

            client = self.websocket_client

            # Try small health potion first
            self.logger.info("🧪 Trying SMALL_POTION to restore health")
            # The API path for potion use is via buy/use or direct consumable use when owned.
            # We try direct consumable use by blueprint id.
            try:
                success = await self._execute_action_with_tracking(
                    "CONSUMABLES_USE", lambda: client.use_consumable("SMALL_POTION")
                )
                if success:
                    self.logger.info("✅ SMALL_POTION use confirmed")
                    await asyncio.sleep(0.5)
                    return True
                # If use failed, try to buy one then use again
                self.logger.info("🛒 SMALL_POTION not available; attempting to buy 1")
                bought = await client.buy_consumable(
                    "SMALL_POTION", 1, record_on_chain=False
                )
                if bought:
                    await asyncio.sleep(0.5)
                    self.logger.info("🔁 Using SMALL_POTION after purchase")
                    success = await self._execute_action_with_tracking(
                        "CONSUMABLES_USE", lambda: client.use_consumable("SMALL_POTION")
                    )
                    if success:
                        self.logger.info("✅ SMALL_POTION use confirmed after purchase")
                        await asyncio.sleep(0.5)
                        return True
            except Exception as e:
                self.logger.debug(f"Small potion use/buy failed: {e}")

            # Fallback to SALAD (improves health and hunger)
            self.logger.info("🥗 Falling back to SALAD to recover health")
            try:
                success = await self._execute_action_with_tracking(
                    "CONSUMABLES_USE", lambda: client.use_consumable("SALAD")
                )
                if success:
                    self.logger.info("✅ SALAD consumption confirmed")
                    await asyncio.sleep(0.5)
                    return True
                # If use failed, try to buy one then use again
                self.logger.info("🛒 SALAD not available; attempting to buy 1")
                bought = await client.buy_consumable("SALAD", 1, record_on_chain=False)
                if bought:
                    await asyncio.sleep(0.5)
                    self.logger.info("🔁 Using SALAD after purchase")
                    success = await self._execute_action_with_tracking(
                        "CONSUMABLES_USE", lambda: client.use_consumable("SALAD")
                    )
                    if success:
                        self.logger.info(
                            "✅ SALAD consumption confirmed after purchase"
                        )
                        await asyncio.sleep(0.5)
                        return True
            except Exception as e:
                self.logger.debug(f"Salad use/buy failed: {e}")

            return False
        finally:
            self._low_health_recovery_in_progress = False

    def _get_aip_balance(self, pet_data: "PettAgent.PetDataDict") -> float:
        """Convert raw PetTokens balance to a floating point $AIP value."""
        tokens = pet_data.get("PetTokens", {}) or {}
        raw_balance = tokens.get("tokens") or pet_data.get("balance", 0)
        try:
            if isinstance(raw_balance, str):
                value = raw_balance.strip()
                if not value:
                    return 0.0
                if value.lower().startswith("0x"):
                    base_value = int(value, 16)
                else:
                    base_value = int(value)
            elif isinstance(raw_balance, (int, float)):
                base_value = int(float(raw_balance))
            else:
                return 0.0
            return base_value / (10**18)
        except Exception:
            return self._to_float(raw_balance)  # type: ignore[arg-type]

    def _update_economy_mode_state(self, balance: float) -> bool:
        """Toggle economy mode when the available balance crosses the threshold."""
        new_state = balance < self.ECONOMY_BALANCE_THRESHOLD
        warning_msg = None
        if new_state != self._economy_mode_active:
            if new_state:
                warning_msg = (
                    "Economy mode active: insufficient $AIP available for purchases."
                )
                self.logger.warning(
                    "🔻 Economy mode enabled: %.2f $AIP below %.2f",
                    balance,
                    self.ECONOMY_BALANCE_THRESHOLD,
                )
            else:
                self.logger.info(
                    "💰 Economy mode disabled: %.2f $AIP available",
                    balance,
                )
        self._economy_mode_active = new_state
        try:
            if self.olas:
                self.olas.update_economy_mode_status(
                    new_state,
                    warning_msg
                    or (
                        "Economy mode active: insufficient $AIP available for purchases."
                        if new_state
                        else None
                    ),
                )
        except Exception:
            pass
        return new_state

    def _normalize_consumable_key(self, blueprint: Any) -> str:
        if blueprint is None:
            return ""
        try:
            return str(blueprint).strip().upper()
        except Exception:
            return ""

    def _clone_owned_consumables_cache(self) -> Dict[str, "PettAgent.OwnedConsumable"]:
        return {
            key: dict(value) for key, value in self._owned_consumables_cache.items()
        }

    def _is_food_consumable(
        self,
        blueprint_id: str,
        info: Optional["PettAgent.OwnedConsumable"],
    ) -> bool:
        type_hint = ((info or {}).get("type") or "").upper()
        if type_hint:
            return type_hint == "FOOD"
        normalized = self._normalize_consumable_key(blueprint_id)
        return normalized in self.KNOWN_FOOD_BLUEPRINTS

    def _all_specified_stats_zero(
        self, stats: Dict[str, Any], keys: Tuple[str, ...]
    ) -> bool:
        for key in keys:
            value = self._to_float(stats.get(key, 0))
            if value > 0.0:
                return False
        return True

    def _potion_usage_allowed(self, stats: Dict[str, Any]) -> bool:
        return self._all_specified_stats_zero(stats, self.POTION_STAT_KEYS)

    def _consumable_allowed_for_use(
        self,
        blueprint_id: str,
        info: Optional["PettAgent.OwnedConsumable"],
        stats: Dict[str, Any],
    ) -> bool:
        if not info or int(info.get("quantity", 0) or 0) <= 0:
            return False
        if not self._is_food_consumable(blueprint_id, info):
            return False
        if self._normalize_consumable_key(
            blueprint_id
        ) == "POTION" and not self._potion_usage_allowed(stats):
            return False
        return True

    async def _get_owned_consumables(
        self, *, force_refresh: bool = False
    ) -> Dict[str, "PettAgent.OwnedConsumable"]:
        """Return a cached mapping of owned consumables keyed by blueprint name."""

        now = datetime.now()
        if (
            not force_refresh
            and self._owned_consumables_updated_at
            and (now - self._owned_consumables_updated_at) < self._consumables_cache_ttl
        ):
            return self._clone_owned_consumables_cache()

        if not self.websocket_client:
            return self._clone_owned_consumables_cache()

        raw_items: Optional[List[Dict[str, Any]]] = None
        try:
            raw_items = await self.websocket_client.fetch_consumables_inventory()
        except Exception as exc:
            self.logger.debug("Failed to refresh consumables inventory: %s", exc)
            return self._clone_owned_consumables_cache()

        inventory: Dict[str, "PettAgent.OwnedConsumable"] = {}
        for item in raw_items or []:
            if not isinstance(item, dict):
                continue

            blueprint_payload = item.get("blueprint")
            blueprint_cfg = item.get("blueprintConfig")
            blueprint_key = ""
            blueprint_name = ""
            blueprint_type = ""

            if not blueprint_cfg and isinstance(item.get("blueprintData"), dict):
                blueprint_cfg = item.get("blueprintData")

            if isinstance(blueprint_payload, dict):
                blueprint_name = str(blueprint_payload.get("name", "") or "")
                blueprint_type = str(blueprint_payload.get("type", "") or "").upper()
                blueprint_key = self._normalize_consumable_key(
                    blueprint_payload.get("blueprintID")
                    or blueprint_payload.get("id")
                    or blueprint_payload.get("slug")
                )
                if not blueprint_cfg:
                    inner_cfg = blueprint_payload.get("config")
                    if isinstance(inner_cfg, dict):
                        blueprint_cfg = inner_cfg
            elif isinstance(blueprint_payload, str):
                blueprint_key = self._normalize_consumable_key(blueprint_payload)

            if not blueprint_type and isinstance(blueprint_cfg, dict):
                blueprint_type = str(blueprint_cfg.get("type", "") or "").upper()
                if not blueprint_name:
                    blueprint_name = str(blueprint_cfg.get("name", "") or "")
                if not blueprint_key:
                    blueprint_key = self._normalize_consumable_key(
                        blueprint_cfg.get("blueprintID")
                        or blueprint_cfg.get("id")
                        or blueprint_cfg.get("slug")
                    )

            if not blueprint_key:
                blueprint_key = self._normalize_consumable_key(item.get("blueprintID"))
            if not blueprint_key:
                continue

            quantity_raw = item.get("quantity", 0)
            try:
                quantity = int(quantity_raw)
            except Exception:
                try:
                    quantity = int(float(str(quantity_raw)))
                except Exception:
                    quantity = 0
            if quantity <= 0:
                continue

            entry = inventory.setdefault(
                blueprint_key,
                {"blueprint_id": blueprint_key, "quantity": 0},
            )
            entry["quantity"] = int(entry.get("quantity", 0)) + quantity
            if blueprint_type and not entry.get("type"):
                entry["type"] = blueprint_type
            if blueprint_name and not entry.get("name"):
                entry["name"] = blueprint_name

        self._owned_consumables_cache = inventory
        self._owned_consumables_updated_at = now
        return self._clone_owned_consumables_cache()

    def _decrement_consumable_cache(self, blueprint: str) -> None:
        key = self._normalize_consumable_key(blueprint)
        if not key:
            return
        entry = self._owned_consumables_cache.get(key)
        if not entry:
            return
        new_qty = max(int(entry.get("quantity", 0)) - 1, 0)
        if new_qty <= 0:
            self._owned_consumables_cache.pop(key, None)
        else:
            entry["quantity"] = new_qty
        self._owned_consumables_updated_at = datetime.now()

    async def _use_owned_health_consumable(
        self,
        *,
        inventory: Optional[Dict[str, "PettAgent.OwnedConsumable"]] = None,
        force_refresh: bool = False,
        stats: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not self.websocket_client:
            return False

        inv = inventory
        if inv is None:
            inv = await self._get_owned_consumables(force_refresh=force_refresh)

        if not inv:
            return False

        current_stats = stats or {}
        allowed_blueprints = [
            bp
            for bp, info in inv.items()
            if self._consumable_allowed_for_use(bp, info, current_stats)
        ]
        if not allowed_blueprints:
            return False

        client = self.websocket_client
        for blueprint in self.HEALTH_CONSUMABLE_PRIORITY:
            info = inv.get(blueprint)
            if blueprint not in allowed_blueprints or not info:
                continue
            qty = int(info.get("quantity", 0))
            if qty:
                self.logger.info(
                    "🩹 Economy mode: using owned %s (qty %d)", blueprint, qty
                )
                success = await self._execute_action_with_tracking(
                    "CONSUMABLES_USE",
                    lambda bp=blueprint: client.use_consumable(bp),
                )
                if success:
                    self._decrement_consumable_cache(blueprint)
                    await self._get_owned_consumables(force_refresh=True)
                return success
        return False

    async def _attempt_owned_food_feed(
        self,
        stats: Dict[str, Any],
        *,
        inventory: Optional[Dict[str, int]] = None,
        force_refresh: bool = False,
    ) -> bool:
        if not self.decision_engine:
            return False

        inv = inventory
        if inv is None:
            inv = await self._get_owned_consumables(force_refresh=force_refresh)
        if not inv:
            return False

        stats_snapshot = dict(stats)

        allowed = [
            bp
            for bp, info in inv.items()
            if bp and self._consumable_allowed_for_use(bp, info, stats_snapshot)
        ]
        if not allowed:
            return False

        self.logger.info(
            "🍱 Economy mode: attempting to feed using %d owned consumable types",
            len(allowed),
        )

        async def feed_action() -> bool:
            if not self.decision_engine:
                return False
            return await self.decision_engine.feed_best_owned_food(
                stats_snapshot, allowed_blueprints=allowed
            )

        success = await self._execute_action_with_tracking(
            "CONSUMABLES_USE", feed_action
        )
        if success:
            await self._get_owned_consumables(force_refresh=True)
        return success

    async def _consume_owned_resources_for_needs(
        self,
        *,
        hunger_needed: bool,
        health_needed: bool,
        stats: Dict[str, Any],
        inventory: Optional[Dict[str, int]] = None,
        force_refresh: bool = False,
    ) -> bool:
        if not (hunger_needed or health_needed):
            return False

        inv = inventory
        if inv is None:
            inv = await self._get_owned_consumables(force_refresh=force_refresh)

        if not inv:
            self.logger.info(
                "Economy mode: no owned consumables available for current needs"
            )
            return False

        if health_needed:
            used_health = await self._use_owned_health_consumable(
                inventory=inv, force_refresh=False, stats=stats
            )
            if used_health:
                return True

        if hunger_needed:
            used_food = await self._attempt_owned_food_feed(
                stats, inventory=inv, force_refresh=False
            )
            if used_food:
                return True

        return False

    async def _perform_economy_token_actions(
        self,
        client: PettWebSocketClient,
        *,
        energy: float,
        hygiene: float,
        happiness: float,
    ) -> bool:
        if energy < self.LOW_ENERGY_THRESHOLD:
            self.logger.info(
                "Economy mode: sleeping to rebuild energy before earning actions"
            )
            await self._execute_action_with_tracking("SLEEP", client.sleep_pet)
            return True

        if hygiene < self.LOW_THRESHOLD:
            self.logger.info("Economy mode: hygiene low; showering for free gains")
            await self._execute_action_with_tracking(
                "SHOWER", client.shower_pet, treat_already_clean_as_success=True
            )
            return True

        if happiness < self.LOW_THRESHOLD:
            self.logger.info(
                "Economy mode: happiness low; throwing ball series to earn tokens"
            )
            for _ in range(3):
                await self._execute_action_with_tracking("THROWBALL", client.throw_ball)
                await asyncio.sleep(0.5)
            return True

        self.logger.info("Economy mode: stats stable; throwing ball to earn tokens")
        await self._execute_action_with_tracking("THROWBALL", client.throw_ball)
        return True

    async def _maybe_log_mid_interval(self, now: datetime) -> None:
        """Log staking KPI progress when halfway to the next scheduled action."""
        if self._mid_interval_logged:
            return
        if not self.next_action_at:
            return
        interval_seconds = max(self.action_interval_minutes * 60.0, 0.0)
        if interval_seconds <= 0:
            return
        time_remaining = (self.next_action_at - now).total_seconds()
        if time_remaining <= 0:
            return
        if time_remaining > interval_seconds / 2:
            return

        try:
            await self._log_staking_epoch_progress()
        finally:
            self._mid_interval_logged = True

    async def _log_staking_epoch_progress(self) -> None:
        """Fetch and log staking KPIs for the current epoch."""
        client = self.olas.get_staking_checkpoint_client()
        if not client or not client.is_enabled:
            if not self._staking_kpi_log_suppressed:
                self.logger.debug(
                    "Staking KPIs unavailable: checkpoint client not configured or disabled"
                )
                self._staking_kpi_log_suppressed = True
            return

        try:
            metrics = await client.get_epoch_kpis()
        except Exception as exc:
            if not self._staking_kpi_log_suppressed:
                self.logger.debug(
                    "Failed to fetch staking KPIs from checkpoint client: %s", exc
                )
                self._staking_kpi_log_suppressed = True
            return

        if metrics is None:
            if not self._staking_kpi_log_suppressed:
                self.logger.debug(
                    "Staking KPIs not available; ensure SERVICE_ID / staking env vars are configured"
                )
                self._staking_kpi_log_suppressed = True
            return

        self._staking_kpi_log_suppressed = False

        eta_text = metrics.eta_text()
        status_icon = "✅" if metrics.threshold_met else "⚠️"
        self.logger.info(
            "%s Staking epoch progress: %s/%s txs (remaining %s) — epoch ends %s",
            status_icon,
            metrics.txs_in_epoch,
            metrics.required_txs,
            metrics.txs_remaining,
            eta_text,
        )

        try:
            self.olas.update_staking_metrics(metrics.to_dict())
        except Exception:
            # Do not let telemetry failures block the loop
            pass

    async def _maybe_checkpoint_epoch_end(self, trigger: str) -> None:
        """Call checkpoint from the agent wallet once the epoch end timestamp is hit."""
        client = self.olas.get_staking_checkpoint_client()
        if not client or not client.is_enabled:
            return

        async with self._epoch_checkpoint_lock:
            try:
                metrics = await client.get_epoch_kpis(force_refresh=True)
            except Exception as exc:
                self.logger.debug(
                    "Failed to refresh staking KPIs for %s checkpoint trigger: %s",
                    trigger,
                    exc,
                )
                return

            if metrics is None:
                return

            epoch_end_ts_raw = metrics.epoch_end_timestamp
            if epoch_end_ts_raw is None:
                return
            epoch_end_ts = int(epoch_end_ts_raw)

            epoch_length = metrics.liveness_period or self._epoch_length_seconds
            if not epoch_length or epoch_length <= 0:
                epoch_length = DEFAULT_LIVENESS_PERIOD
            self._epoch_length_seconds = epoch_length

            last_known_end = self._last_known_epoch_end_ts
            if last_known_end is not None and epoch_length:
                delta = abs(epoch_end_ts - last_known_end)
                if delta > epoch_length:
                    self.logger.debug(
                        "Epoch boundary jumped by %ss (> expected %s): %s -> %s",
                        delta,
                        epoch_length,
                        last_known_end,
                        epoch_end_ts,
                    )
            self._last_known_epoch_end_ts = epoch_end_ts

            now_ts = int(time.time())
            if now_ts < epoch_end_ts:
                remaining = epoch_end_ts - now_ts
                self.logger.debug(
                    "Skipping staking checkpoint (%s): epoch end %s in %ss",
                    trigger,
                    epoch_end_ts,
                    remaining,
                )
                return

            last_checkpointed = self._last_checkpointed_epoch_end_ts
            if last_checkpointed is not None and epoch_end_ts <= last_checkpointed:
                self.logger.debug(
                    "Skipping staking checkpoint (%s): epoch end %s already handled (last %s)",
                    trigger,
                    epoch_end_ts,
                    last_checkpointed,
                )
                return

            try:
                tx_hash = await client.call_checkpoint_if_needed(force=False)
            except Exception as exc:
                self.logger.warning(
                    "Staking checkpoint trigger '%s' failed: %s", trigger, exc
                )
                return

            if tx_hash:
                self.logger.info(
                    "⛽️ Epoch end (%s) reached via %s; checkpoint tx submitted: %s",
                    epoch_end_ts,
                    trigger,
                    tx_hash,
                )
                self._last_checkpointed_epoch_end_ts = epoch_end_ts
            else:
                self.logger.debug(
                    "Epoch end (%s) reached via %s but checkpoint skipped (conditions unmet)",
                    epoch_end_ts,
                    trigger,
                )

    async def _maybe_call_staking_checkpoint(self, force: bool = False) -> None:
        """Attempt to call the staking checkpoint when the liveness window expires."""
        client = self.olas.get_staking_checkpoint_client()
        if not client:
            return

        now = datetime.now()
        if not force and now < self._next_checkpoint_check_at:
            return

        self._next_checkpoint_check_at = now + self._checkpoint_check_interval
        try:
            tx_hash = await client.call_checkpoint_if_needed(force=force)
            if tx_hash:
                self.logger.info("⛽️ Staking checkpoint submitted: %s", tx_hash)
            else:
                self.logger.debug("Staking checkpoint not required at this time")
        except Exception as exc:
            self.logger.warning(f"Staking checkpoint attempt failed: {exc}")

    async def _on_client_error_message(self, message: Dict[str, Any]) -> None:
        """Handle server error messages to auto-recover from low health errors."""
        try:
            error = None
            if "data" in message:
                error = message.get("data", {}).get("error")
            if not error:
                error = message.get("error")

            if not error:
                return

            error_str = str(error).lower()
            if (
                "not have enough health" in error_str
                or "not enough health" in error_str
            ):
                self.logger.info(
                    "🩹 Detected 'not enough health' error; attempting recovery"
                )
                await self._recover_low_health()
        except Exception as e:
            self.logger.debug(f"Error handler encountered exception: {e}")

    async def _on_client_pet_update_message(self, message: Dict[str, Any]) -> None:
        """Update Olas pet data immediately when live pet_update arrives."""
        try:
            if not self.websocket_client:
                return
            pet_data = self.websocket_client.get_pet_data()
            if pet_data:
                self.olas.update_pet_data(pet_data)
                # Attach post-action stats to the latest recorded action
                self.olas.update_last_action_stats()
        except Exception as e:
            self.logger.debug(f"Pet update handler encountered exception: {e}")

    async def _record_passive_sleep_action(self) -> None:
        """Record a synthetic SLEEP action without toggling the pet's state."""
        self.logger.info("🧾 Recording passive SLEEP action while maintaining rest")
        self._daily_action_tracker.record_action("SLEEP")
        await self._log_action_progress("SLEEP")
        recorder = self.olas.get_action_recorder()
        if recorder and recorder.is_enabled:
            self.logger.info(
                "🧾 On-chain SLEEP record skipped: verification unavailable without toggling state"
            )

    async def _get_epoch_action_progress(
        self, *, force_refresh: bool = False, allow_local_fallback: bool = True
    ) -> Tuple[Optional[Tuple[int, int, int, bool]], Optional[str]]:
        """Return staking progress tuple and failure reason, if any.

        When allow_local_fallback is False the method returns (None, reason)
        instead of using the local daily tracker.
        """
        reason: Optional[str] = None
        client = self.olas.get_staking_checkpoint_client()
        has_client = bool(client and client.is_enabled)
        if has_client:
            try:
                metrics = await client.get_epoch_kpis(force_refresh=force_refresh)
            except Exception as exc:
                reason = f"failed to fetch staking KPIs: {exc}"
                self.logger.debug(
                    "Failed to fetch staking KPIs for action progress: %s", exc
                )
            else:
                if metrics is not None:
                    required = int(
                        metrics.required_txs or self.REQUIRED_ACTIONS_PER_EPOCH
                    )
                    if required <= 0:
                        required = self.REQUIRED_ACTIONS_PER_EPOCH
                    completed = max(int(metrics.txs_in_epoch), 0)
                    remaining = max(required - completed, 0)
                    return (completed, required, remaining, True), None
                reason = "checkpoint client is running in checkpoint-only mode; staking KPIs unavailable"
        else:
            reason = "staking checkpoint client is not configured or disabled"
            self.logger.debug(
                "No staking checkpoint client available; using daily action tracker"
            )

        if not allow_local_fallback:
            return None, reason

        completed = self._daily_action_tracker.actions_completed()
        required = self.REQUIRED_ACTIONS_PER_EPOCH
        remaining = self._daily_action_tracker.actions_remaining()
        return (completed, required, remaining, False), None

    async def _log_action_progress(self, action_name: str) -> None:
        """Log staking-aware counters or explain why they are unavailable."""
        progress, reason = await self._get_epoch_action_progress(
            force_refresh=True, allow_local_fallback=False
        )
        if not progress:
            reason_text = reason or "unknown cause"
            self.logger.warning(
                "Staking KPI snapshot unavailable for %s: %s",
                action_name,
                reason_text,
            )
            fallback_progress, _ = await self._get_epoch_action_progress(
                force_refresh=False, allow_local_fallback=True
            )
            if fallback_progress:
                f_completed, f_required, f_remaining, using_staking = fallback_progress
                scope_label = (
                    "this epoch (cached)" if using_staking else "today (local tracker)"
                )
                self.logger.info(
                    "📋 Action %s recorded (%d/%d %s, %d remaining) — pending staking KPIs",
                    action_name,
                    f_completed,
                    f_required,
                    scope_label,
                    f_remaining,
                )
            else:
                self.logger.info(
                    "📋 Action %s recorded — pending staking KPIs", action_name
                )
            return

        completed, required, remaining, using_staking = progress
        if not using_staking:
            self.logger.debug(
                "Staking KPI data unexpectedly unavailable for %s; skipping log",
                action_name,
            )
            return
        self.logger.info(
            "📋 On-chain action %s recorded (%d/%d this epoch, %d remaining)",
            action_name,
            completed,
            required,
            remaining,
        )

    async def _record_resting_sleep_action(self, client: PettWebSocketClient) -> bool:
        """Emit a verified SLEEP action while keeping the pet asleep overall."""
        self.logger.info(
            "🧾 Passive SLEEP requires verification; briefly toggling rest to submit on-chain record"
        )
        temporarily_awake = False
        try:
            wake_success = await client.sleep_pet(record_on_chain=False)
        except Exception as exc:
            self.logger.warning(
                "⚠️ Failed to pulse wake before passive SLEEP verification: %s", exc
            )
            return False
        if not wake_success:
            self.logger.warning(
                "⚠️ Unable to wake pet before passive SLEEP verification; skipping on-chain record"
            )
            return False

        temporarily_awake = True
        await asyncio.sleep(0.5)
        success = await self._execute_action_with_tracking("SLEEP", client.sleep_pet)
        if not success and temporarily_awake:
            try:
                await client.sleep_pet(record_on_chain=False)
            except Exception:
                pass
        return success

    async def _execute_action_with_tracking(
        self,
        action_name: str,
        action_callable: Callable[[], Awaitable[bool]],
        *,
        treat_already_clean_as_success: bool = False,
    ) -> bool:
        """Run an action coroutine and record it toward the daily requirement."""
        normalized_name = (action_name or "").upper() or "UNKNOWN"
        success = False
        try:
            result = await action_callable()
            success = bool(result)
        except Exception as exc:
            self.logger.error("❌ Action %s raised: %s", normalized_name, exc)

        if (
            not success
            and treat_already_clean_as_success
            and self._last_action_was_already_clean()
        ):
            success = True

        if success:
            self._daily_action_tracker.record_action(normalized_name)
            await self._log_action_progress(normalized_name)

        try:
            await self._maybe_call_staking_checkpoint()
        except Exception as exc:
            self.logger.debug(
                "Liveness checkpoint check after %s action failed: %s",
                normalized_name,
                exc,
            )
        try:
            await self._maybe_checkpoint_epoch_end(f"action:{normalized_name}")
        except Exception as exc:
            self.logger.debug(
                "Checkpoint trigger after %s action failed: %s", normalized_name, exc
            )
        return success

    def _last_action_was_already_clean(self) -> bool:
        """Return True when the last server error was an 'already clean' message."""
        try:
            if not self.websocket_client:
                return False
            err_text = self.websocket_client.get_last_action_error()
            if not err_text:
                return False
            return "already clean" in err_text.lower()
        except Exception:
            return False

    def _needs_structured_actions(self) -> bool:
        """True until the agent logs the minimum required transactions for the epoch."""
        return not self._daily_action_tracker.has_met_required_actions()

    def _build_structured_candidates(
        self,
        client: PettWebSocketClient,
        stats: Dict[str, Any],
    ) -> List[Tuple[float, str, Callable[[], Awaitable[bool]], bool]]:
        """Create a priority-ordered list of care actions based on stat deficits."""
        hunger = self._to_float(stats.get("hunger", 0))
        hygiene = self._to_float(stats.get("hygiene", 0))
        happiness = self._to_float(stats.get("happiness", 0))

        candidates: List[Tuple[float, str, Callable[[], Awaitable[bool]], bool]] = []

        def add_candidate(
            priority: float,
            action_name: str,
            func: Callable[[], Awaitable[bool]],
            allow_clean: bool = False,
        ) -> None:
            candidates.append((priority, action_name, func, allow_clean))

        hunger_deficit = max(0.0, 100.0 - hunger)
        if self.decision_engine and hunger_deficit > 2.0:
            stats_snapshot = dict(stats)

            async def feed_candidate() -> bool:
                if not self.decision_engine:
                    return False
                return await self.decision_engine.feed_best_owned_food(stats_snapshot)

            add_candidate(
                hunger_deficit + 20.0, "CONSUMABLES_USE", feed_candidate, False
            )

        """  health_deficit = max(0.0, 100.0 - health)
        if health_deficit > 2.0:
            add_candidate(health_deficit + 10.0, "CONSUMABLES_USE", client.recover_health, False) """

        hygiene_deficit = max(0.0, 100.0 - hygiene)
        happiness_deficit = max(0.0, 100.0 - happiness)

        if hygiene_deficit > 50.0:
            add_candidate(hygiene_deficit + 10.0, "SHOWER", client.shower_pet, True)

            if happiness_deficit > 6.0:
                add_candidate(happiness_deficit, "RUB", client.rub_pet, False)

        if happiness_deficit > 5.0:
            add_candidate(
                happiness_deficit + 5.0, "THROWBALL", client.throw_ball, False
            )

        if not candidates:
            add_candidate(1.0, "THROWBALL", client.throw_ball, False)

        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates

    async def _perform_structured_action(
        self,
        client: PettWebSocketClient,
        stats: Dict[str, Any],
    ) -> bool:
        """Execute the highest priority action from the structured plan."""
        for (
            _priority,
            action_name,
            action_callable,
            allow_clean,
        ) in self._build_structured_candidates(client, stats):
            success = await self._execute_action_with_tracking(
                action_name,
                action_callable,
                treat_already_clean_as_success=allow_clean,
            )
            if success:
                return True
        return False

    async def _random_action(self, client: PettWebSocketClient) -> None:
        actions = [
            (client.rub_pet, "rub"),
            (client.shower_pet, "shower"),
            (client.throw_ball, "throw_ball"),
            (client.throw_ball, "throw_ball"),
            (client.throw_ball, "throw_ball"),
        ]
        action_func, action_name = random.choice(actions)
        self.logger.info(f"🎲 Performing random action: {action_name}")
        normalized_name = action_name.upper()
        action_success = await self._execute_action_with_tracking(
            normalized_name,
            action_func,
            treat_already_clean_as_success=normalized_name in {"RUB", "SHOWER"},
        )

        if not action_success and normalized_name in {"RUB", "SHOWER"}:
            self.logger.info(
                "🧽 Random %s failed; falling back to throw_ball", action_name
            )
            fallback_success = await self._execute_action_with_tracking(
                "THROWBALL", client.throw_ball
            )
            if not fallback_success:
                self.logger.warning(
                    "⚠️ Fallback throw_ball after %s failure did not succeed",
                    action_name,
                )

        # If action failed due to low energy, put pet to sleep instead
        try:
            last_err = (
                client.get_last_action_error()
                if hasattr(client, "get_last_action_error")
                else None
            )
        except Exception:
            last_err = None
        if last_err and ("not have enough energy" in str(last_err).lower()):
            pet_data = client.get_pet_data() or {}
            sleeping_now = bool(pet_data.get("sleeping", False))
            if not sleeping_now:
                self.logger.info(
                    "⚡️ Energy too low for random action; putting pet to sleep instead"
                )
                await self._execute_action_with_tracking("SLEEP", client.sleep_pet)
            else:
                self.logger.info(
                    "⚡️ Energy too low and pet already sleeping; not toggling sleep"
                )

    async def _decide_and_perform_actions(
        self, pet_data: "PettAgent.PetDataDict"
    ) -> None:
        if not self.websocket_client or not self.pett_tools:
            self.logger.error("❌ No WebSocket client or PettTools found")
            self.olas.update_pet_status(False, "Error")
            self.olas.update_pet_data(None)
            return

        client = self.websocket_client
        stats: Dict[str, Any] = pet_data.get("PetStats", {})  # type: ignore[assignment]
        sleeping: bool = bool(pet_data.get("sleeping", False))

        hygiene = self._to_float(stats.get("hygiene", 0))
        happiness = self._to_float(stats.get("happiness", 0))
        energy = self._to_float(stats.get("energy", 0))
        hunger = self._to_float(stats.get("hunger", 0))
        health = self._to_float(stats.get("health", 0))
        actions_remaining = self._daily_action_tracker.actions_remaining()
        kpi_met = actions_remaining == 0
        critical_threshold = self.CRITICAL_STAT_THRESHOLD
        stats_critical = self._all_core_stats_below_threshold(stats, critical_threshold)
        sleep_blocked = stats_critical
        token_balance = self._get_aip_balance(pet_data)
        economy_mode = self._update_economy_mode_state(token_balance)
        needs_consumables = hunger < self.LOW_THRESHOLD or health < self.LOW_THRESHOLD
        inventory_snapshot: Optional[Dict[str, "PettAgent.OwnedConsumable"]] = None
        if economy_mode and needs_consumables:
            inventory_snapshot = await self._get_owned_consumables(force_refresh=True)
        owned_consumable_used = False

        if stats_critical and sleeping:
            self.logger.info(
                "⚠️ Critical stats detected while pet sleeping; waking to use consumables"
            )
            await client.sleep_pet(record_on_chain=False)
            await asyncio.sleep(0.5)
            sleeping = False

        if stats_critical:
            if economy_mode and needs_consumables and inventory_snapshot is not None:
                owned_consumable_used = await self._consume_owned_resources_for_needs(
                    hunger_needed=hunger < self.LOW_THRESHOLD,
                    health_needed=health < self.LOW_THRESHOLD,
                    stats=stats,
                    inventory=inventory_snapshot,
                )
                if owned_consumable_used:
                    self.logger.info(
                        "🍱 Economy mode resolved critical stats using owned consumables"
                    )
                    return

            self.logger.warning(
                "⚠️ Hunger, health, hygiene, and happiness all below %.1f; prioritizing consumables",
                critical_threshold,
            )
            stats_snapshot = dict(stats)

            feed_success = False
            if self.decision_engine:

                async def emergency_feed() -> bool:
                    if not self.decision_engine:
                        return False
                    return await self.decision_engine.feed_best_owned_food(
                        stats_snapshot
                    )

                feed_success = await self._execute_action_with_tracking(
                    "CONSUMABLES_USE", emergency_feed
                )
            else:
                self.logger.warning(
                    "⚠️ Decision engine unavailable; cannot perform emergency feeding"
                )

            if feed_success:
                self.logger.info(
                    "🍽️ Emergency consumable consumed; deferring other actions this cycle"
                )
                return

            self.logger.warning(
                "⚠️ Emergency feeding failed; attempting targeted health recovery"
            )
            recovered = await self._recover_low_health()
            if recovered:
                self.logger.info(
                    "🩹 Emergency health consumable consumed; deferring other actions this cycle"
                )
                return

            self.logger.warning(
                "⚠️ Consumable attempts failed; sleeping remains disabled for this cycle"
            )

        if (
            economy_mode
            and not owned_consumable_used
            and needs_consumables
            and inventory_snapshot is not None
        ):
            owned_consumable_used = await self._consume_owned_resources_for_needs(
                hunger_needed=hunger < self.LOW_THRESHOLD,
                health_needed=health < self.LOW_THRESHOLD,
                stats=stats,
                inventory=inventory_snapshot,
            )
            if owned_consumable_used:
                return

        if actions_remaining == 1:
            if sleep_blocked:
                self.logger.warning(
                    "⚠️ Final required action would be sleep but is blocked due to critical stats"
                )
            else:
                self.logger.info(
                    "😴 Forcing sleep as the final required action in this epoch"
                )
                if sleeping:
                    self.logger.info(
                        "🛌 Pet already sleeping; briefly waking to record the final sleep action"
                    )
                    await client.sleep_pet(record_on_chain=False)
                    await asyncio.sleep(0.5)
                    sleeping = False
                await self._execute_action_with_tracking("SLEEP", client.sleep_pet)
                return

        # Top-priority: low energy -> sleep
        if not sleep_blocked and energy < self.LOW_ENERGY_THRESHOLD and not sleeping:
            self.logger.info("😴 Low energy detected; initiating sleep")
            await self._execute_action_with_tracking("SLEEP", client.sleep_pet)
            return
        elif sleep_blocked and energy < self.LOW_ENERGY_THRESHOLD and not sleeping:
            self.logger.info(
                "⚡️ Energy low but sleeping is disabled until stats recover"
            )

        # Bias post-KPI actions toward sleeping so energy can fully recover
        if kpi_met and energy < self.POST_KPI_SLEEP_TRIGGER:
            if sleep_blocked:
                self.logger.info(
                    "⚠️ Post-KPI sleep skipped because stats are critically low"
                )
            else:
                if sleeping:
                    self.logger.info(
                        "😴 KPI threshold met; keeping pet asleep to rebuild energy (%.1f%%)",
                        energy,
                    )
                    return
                self.logger.info(
                    "😴 KPI threshold met; scheduling additional sleep to rebuild energy (%.1f%%)",
                    energy,
                )
                await self._execute_action_with_tracking("SLEEP", client.sleep_pet)
                return

        # Manage transitions while the pet is already sleeping
        if sleeping:
            if sleep_blocked:
                self.logger.info(
                    "⚠️ Pet is sleeping but stats demand immediate care; waking now"
                )
                await client.sleep_pet(record_on_chain=False)
                await asyncio.sleep(0.5)
                sleeping = False
            else:
                wake_threshold = (
                    self.POST_KPI_SLEEP_TARGET
                    if kpi_met
                    else self.WAKE_ENERGY_THRESHOLD
                )
                if energy >= wake_threshold:
                    self.logger.info(
                        "🔥 Energy recovered to %.1f%% (wake threshold %.1f%%); waking pet",
                        energy,
                        wake_threshold,
                    )
                    await client.sleep_pet(
                        record_on_chain=False
                    )  # dont record since we will perform other actions
                    await asyncio.sleep(0.5)
                    sleeping = False
                else:
                    self.logger.info(
                        "🛌 Pet still resting (energy %.1f%%, wake threshold %.1f%%); deferring other actions",
                        energy,
                        wake_threshold,
                    )
                    if not kpi_met:
                        recorded = await self._record_resting_sleep_action(client)
                        if not recorded:
                            await self._record_passive_sleep_action()
                    return

        # Priority 0: low health -> attempt recovery
        if health < self.LOW_THRESHOLD:
            self.logger.info("🩹 Low health detected; attempting recovery")
            recovered = await self._recover_low_health()
            if not recovered:
                self.logger.warning("⚠️ Health recovery failed")
            return

        if not kpi_met:
            completed = self._daily_action_tracker.actions_completed()
            self.logger.info(
                "📋 Structured plan active (%d/%d complete, %d remaining)",
                completed,
                self.REQUIRED_ACTIONS_PER_EPOCH,
                actions_remaining,
            )
            structured_done = await self._perform_structured_action(client, stats)
            if structured_done:
                return

            self.logger.warning(
                "⚠️ Structured plan could not execute an action; falling back to adaptive logic"
            )

        if random.random() < 0.05:
            self.logger.info(
                "🎲 Random variance: performing random_action instead of shower"
            )
            await self._random_action(client)
            return

        # Priority 1: low hygiene -> shower
        if hygiene < self.LOW_THRESHOLD:
            # Small randomness to occasionally do a different engaging action
            self.logger.info("🚿 Low hygiene detected; showering pet")
            await self._execute_action_with_tracking("SHOWER", client.shower_pet)
            return

        # Priority 2: low hunger -> use AI decision engine to pick best food
        if hunger < self.LOW_THRESHOLD:
            self.logger.info("🍔 Low hunger detected; using AI to select best food")
            if self.decision_engine:

                async def feed_action() -> bool:
                    if not self.decision_engine:
                        return False
                    return await self.decision_engine.feed_best_owned_food(stats)

                feed_success = await self._execute_action_with_tracking(
                    "CONSUMABLES_USE", feed_action
                )
                if not feed_success:
                    self.logger.warning(
                        "⚠️ AI food selection failed; skipping fallback use"
                    )
            else:
                self.logger.warning(
                    "⚠️ No decision engine available; skipping food selection"
                )
            return

        if economy_mode:
            handled = await self._perform_economy_token_actions(
                client,
                energy=energy,
                hygiene=hygiene,
                happiness=happiness,
            )
            if handled:
                return

        # Priority 3: low happiness -> throw ball 3 times with delays
        if happiness < self.LOW_THRESHOLD:
            self.logger.info("🎾 Low happiness detected; throwing ball 3 times")
            for _ in range(3):
                await self._execute_action_with_tracking("THROWBALL", client.throw_ball)
                await asyncio.sleep(0.5)
            return

        # Fallback: random action
        self.logger.info("🎲 No priority actions; performing random_action")
        await self._random_action(client)

    async def run(self):
        """Run the Pett Agent."""
        if not await self.initialize():
            self.logger.error("❌ Failed to initialize agent")
            return

        self.running = True
        self.logger.info("🎯 Pett Agent is now running...")
        self.logger.info(
            "Waiting the user to enter http://localhost:8716/ (or http://127.0.0.1:8716/) to log in and start running successfully the agent"
        )

        try:
            await self._maybe_call_staking_checkpoint()
        except Exception as exc:
            self.logger.debug("Startup checkpoint check failed: %s", exc)

        try:
            # Start background tasks
            tasks = [
                asyncio.create_task(self._health_monitor()),
                asyncio.create_task(self._pet_action_loop()),
            ]

            # Run until shutdown
            await asyncio.gather(*tasks)

        except KeyboardInterrupt:
            self.logger.info("🛑 Shutdown requested by user")
        except Exception as e:
            self.logger.error(f"❌ Error in main loop: {e}")
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Shutdown the agent gracefully."""
        self.logger.info("🛑 Shutting down Pett Agent...")
        self.running = False

        try:
            # Update health status
            self.olas.update_health_status("shutting_down", is_transitioning=True)

            # Disconnect WebSocket
            if self.websocket_client:
                await self.websocket_client.disconnect()
                self.logger.info("🔌 WebSocket disconnected")

            # Stop web server
            await self.olas.stop_web_server()

            # Final health status
            self.olas.update_health_status("stopped", is_transitioning=False)
            self.logger.info("✅ Pett Agent shutdown complete")

        except Exception as e:
            self.logger.error(f"❌ Error during shutdown: {e}")
        finally:
            try:
                if self.olas:
                    self.olas.persist_agent_performance_metrics()
            except Exception as exc:
                self.logger.debug(
                    "Failed to persist agent performance metrics on shutdown: %s", exc
                )

    def get_action_timing_info(self) -> Dict[str, Any]:
        """Expose action scheduling info for UI/health."""
        now = datetime.now()
        next_at = self.next_action_at or now
        minutes_until = max(int((next_at - now).total_seconds() // 60), 0)
        return {
            "action_interval_minutes": self.action_interval_minutes,
            "next_action_at": next_at.isoformat(),
            "minutes_until_next_action": minutes_until,
            "next_action_scheduled": True,
        }
