import asyncio
import json
import logging
import os
import platform
import random
import ssl
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import certifi
import websockets
from dotenv import load_dotenv

from .action_recorder import ActionRecorder

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AGENT_CERTS_DIR = Path(__file__).resolve().parent / "certs"
DEFAULT_WS_CA_FILE = AGENT_CERTS_DIR / "ws_pett_ai_ca.pem"


def format_wei_to_eth(wei_value: str | int, decimals: int = 4) -> str:
    """
    Convert wei value to ETH with specified decimal places.

    Args:
        wei_value: The wei value as string or int
        decimals: Number of decimal places to show (default: 4)

    Returns:
        Formatted ETH value as string
    """
    try:
        # Convert to int if it's a string
        if isinstance(wei_value, str):
            wei_value = int(wei_value)

        # Convert wei to ETH (1 ETH = 10^18 wei)
        eth_value = wei_value / (10**18)

        # Format with specified decimal places
        return f"{eth_value:.{decimals}f}"
    except (ValueError, TypeError, ZeroDivisionError):
        return "0.0000"


class PettWebSocketClient:
    def __init__(
        self,
        websocket_url: str | None = os.getenv(
            "WEBSOCKET_URL",
            (
                "wss://ws.pett.ai"
                if os.getenv("NODE_ENV") == "production"
                else "wss://ws.pett.ai"
            ),
        ),
        privy_token: Optional[str] = None,
    ):
        self.websocket_url = websocket_url
        self.websocket: Optional[Any] = None
        self.authenticated = False
        self.pet_data: Optional[Dict[str, Any]] = None
        self.message_handlers: Dict[str, List[Callable]] = {}
        self.connection_established = False
        self.privy_token = (privy_token or os.getenv("PRIVY_TOKEN") or "").strip()
        self.data_message: Optional[Dict[str, Any]] = None
        self.ai_search_future: Optional[asyncio.Future[str]] = None
        self.kitchen_future: Optional[asyncio.Future[str]] = None
        self.mall_future: Optional[asyncio.Future[str]] = None
        self.closet_future: Optional[asyncio.Future[str]] = None
        self.auth_future: Optional[asyncio.Future[bool]] = None
        self._last_auth_error: Optional[str] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._jwt_expired: bool = False
        self._auth_ping_lock: asyncio.Lock = asyncio.Lock()
        # Outgoing message telemetry recorder: (message, success, error)
        self._telemetry_recorder: Optional[
            Callable[[Dict[str, Any], bool, Optional[str]], None]
        ] = None
        # Enable/disable on-chain recordAction scheduling globally
        self._onchain_recording_enabled: bool = True
        # Pending nonce -> future mapping for correlating responses
        self._pending_nonces: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
        if not self.privy_token:
            logger.warning(
                "Privy token not provided during initialization; authentication will be disabled until a token is set."
            )
        self._action_recorder: Optional[ActionRecorder] = None
        # Last action error text captured from server responses
        self._last_action_error: Optional[str] = None
        # Persistent auth token storage for reconnection
        self._saved_auth_token: Optional[str] = None
        self._was_previously_authenticated: bool = False
        self._ssl_context = self._build_ssl_context()
        # Callback to check for staking epoch changes when about to skip recording
        self._epoch_change_checker: Optional[Callable[[], Awaitable[bool]]] = None
        # Callback to record successful on-chain action (for staking counter)
        self._onchain_success_recorder: Optional[Callable[[str], None]] = None

    def set_telemetry_recorder(
        self, recorder: Optional[Callable[[Dict[str, Any], bool, Optional[str]], None]]
    ) -> None:
        """Set a callback to record outgoing messages and outcomes."""
        self._telemetry_recorder = recorder

    def set_action_recorder(self, recorder: Optional[ActionRecorder]) -> None:
        """Attach the action recorder used for on-chain reporting."""
        self._action_recorder = recorder

    def set_onchain_recording_enabled(self, enabled: bool) -> None:
        """Globally enable/disable on-chain recordAction submissions."""
        self._onchain_recording_enabled = bool(enabled)

    def set_epoch_change_checker(
        self, checker: Optional[Callable[[], Awaitable[bool]]]
    ) -> None:
        """Set callback to check for staking epoch changes.

        The callback should return True if the epoch changed and the action
        tracker was reset (meaning recording should be re-enabled).
        """
        self._epoch_change_checker = checker

    def set_onchain_success_recorder(
        self, recorder: Optional[Callable[[str], None]]
    ) -> None:
        """Set callback to record successful on-chain action submissions.

        The callback receives the action name and should increment the staking
        counter only when on-chain recording actually succeeds.
        """
        self._onchain_success_recorder = recorder

    def _schedule_verified_record_action(
        self, action_type: str, verification: Dict[str, Any]
    ) -> None:
        """Schedule an asynchronous verified recordAction transaction if available."""
        # Always check for epoch changes on every action
        if self._epoch_change_checker:
            try:
                loop = asyncio.get_running_loop()
                # Schedule the epoch check and conditional recording
                loop.create_task(
                    self._check_epoch_and_maybe_record(action_type, verification)
                )
                return
            except RuntimeError:
                pass

        # Fallback if no epoch checker is set
        if not self._onchain_recording_enabled:
            logger.info(
                "Already have 8+ verified on-chain txs (staking threshold met); "
                "skipping on-chain recording for %s",
                action_type,
            )
            return
        if not self._action_recorder:
            logger.info(
                "🧾 Skipping on-chain record for %s: action recorder not configured",
                action_type,
            )
            return
        if not self._action_recorder.is_enabled:
            logger.info(
                "🧾 Skipping on-chain record for %s: action recorder disabled (missing key/RPC)",
                action_type,
            )
            return

        normalized_type = (action_type or "").upper()
        if not normalized_type:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        task = loop.create_task(
            self._action_recorder.record_action_verified(normalized_type, verification)
        )

        def _handle_result(fut: asyncio.Future) -> None:
            if fut.cancelled():
                return
            exc = fut.exception()
            if exc:
                logger.debug(
                    "Verified action recorder task raised for %s: %s", action_type, exc
                )

        task.add_done_callback(_handle_result)

    async def _check_epoch_and_maybe_record(
        self, action_type: str, verification: Dict[str, Any]
    ) -> None:
        """Check if epoch changed and record action accordingly.

        This is called on EVERY action to ensure we detect epoch changes
        regardless of current action count.
        """
        # First, check if the staking epoch has changed
        epoch_changed = False
        if self._epoch_change_checker:
            try:
                epoch_changed = await self._epoch_change_checker()
            except Exception as exc:
                logger.debug("Failed to check for epoch change: %s", exc)

        if epoch_changed:
            logger.info(
                "🔄 Epoch changed! Re-enabling on-chain recording for %s",
                action_type,
            )
            self._onchain_recording_enabled = True

        # Now decide whether to record based on current state
        if not self._onchain_recording_enabled:
            logger.info(
                "⏭️ Already have 8+ verified on-chain txs (staking threshold met); "
                "skipping on-chain recording for %s",
                action_type,
            )
            return

        # Proceed with recording
        if not self._action_recorder:
            logger.info(
                "🧾 Skipping on-chain record for %s: action recorder not configured",
                action_type,
            )
            return
        if not self._action_recorder.is_enabled:
            logger.info(
                "🧾 Skipping on-chain record for %s: action recorder disabled (missing key/RPC)",
                action_type,
            )
            return

        normalized_type = (action_type or "").upper()
        if not normalized_type:
            return

        try:
            success = await self._action_recorder.record_action_verified(
                normalized_type, verification
            )
            if success:
                logger.info(
                    "✅ On-chain recording succeeded for %s; incrementing staking counter",
                    normalized_type,
                )
                # Call the success recorder to increment the counter
                if self._onchain_success_recorder:
                    try:
                        self._onchain_success_recorder(normalized_type)
                    except Exception as rec_exc:
                        logger.debug(
                            "Failed to call onchain success recorder for %s: %s",
                            normalized_type,
                            rec_exc,
                        )
            else:
                logger.info(
                    "⚠️ On-chain recording failed for %s; counter NOT incremented",
                    normalized_type,
                )
        except Exception as exc:
            logger.debug(
                "Failed to record action for %s: %s",
                action_type,
                exc,
            )

    def _generate_nonce(self) -> str:
        """Generate a simple random numeric nonce as a string."""
        return str(random.randint(10000, 99999))

    def _register_pending(self, nonce: str) -> asyncio.Future:
        """Create and register a pending future for the given nonce."""
        fut: asyncio.Future = asyncio.Future()
        self._pending_nonces[nonce] = fut  # type: ignore[assignment]
        return fut

    def _resolve_pending(self, nonce: Optional[str], message: Dict[str, Any]) -> None:
        """Resolve any pending future by nonce with the provided message."""
        if not nonce:
            return
        fut = self._pending_nonces.pop(nonce, None)
        if fut and not fut.done():
            try:
                fut.set_result(message)
            except Exception:
                # Ignore resolution errors to avoid cascading failures
                pass

    async def connect(self) -> bool:
        """Establish WebSocket connection to Pett.ai server."""
        try:
            if not self.websocket_url:
                logger.error("WebSocket URL is not set")
                return False

            logger.info(f"🔌 Connecting to WebSocket: {self.websocket_url}")
            connect_kwargs: Dict[str, Any] = {
                "ping_interval": 20,
                "ping_timeout": 10,
                "close_timeout": 10,
            }

            ssl_context = self._ssl_context
            if ssl_context is None and self.websocket_url.startswith("wss://"):
                try:
                    # On macOS, use system certificates; on other platforms, use certifi
                    if platform.system() == "Darwin":
                        ssl_context = ssl.create_default_context()
                        # Also add certifi as additional source
                        try:
                            ssl_context.load_verify_locations(cafile=certifi.where())
                        except Exception:
                            pass  # May already be included
                    else:
                        ssl_context = ssl.create_default_context(cafile=certifi.where())
                except Exception as exc:
                    logger.error(
                        "❌ Failed to build SSL context: %s",
                        exc,
                    )
                    return False

            if ssl_context is not None:
                connect_kwargs["ssl"] = ssl_context

            self.websocket = await websockets.connect(
                self.websocket_url,
                **connect_kwargs,
            )
            self.connection_established = True
            logger.info("✅ WebSocket connection established")
            return True
        except websockets.exceptions.InvalidURI as e:
            logger.error(f"❌ Invalid WebSocket URL: {e}")
            return False
        except websockets.exceptions.ConnectionClosed as e:
            logger.error(f"❌ WebSocket connection closed: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Failed to connect to WebSocket: {e}")
            return False

    def _build_ssl_context(self) -> Optional[ssl.SSLContext]:
        """Build SSL context honoring CA overrides and optional verification bypass."""
        if not self.websocket_url or not self.websocket_url.startswith("wss://"):
            return None

        skip_verify = os.getenv("WEBSOCKET_SKIP_SSL_VERIFY", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        ca_file = (os.getenv("WEBSOCKET_CA_FILE") or "").strip()
        ca_path = (os.getenv("WEBSOCKET_CA_PATH") or "").strip()

        if skip_verify:
            logger.warning(
                "⚠️ WEBSOCKET_SKIP_SSL_VERIFY enabled - TLS certificate verification "
                "for Pett WebSocket connections is DISABLED. Use only in trusted environments."
            )
            return ssl._create_unverified_context()  # type: ignore[attr-defined]

        # On macOS, create_default_context() without cafile uses system certificates
        # On other platforms, we'll use certifi as the base
        try:
            if platform.system() == "Darwin":
                # macOS: use system certificates first, then add certifi and custom CAs
                context = ssl.create_default_context()
            else:
                # Linux/Windows: use certifi as base
                context = ssl.create_default_context(cafile=certifi.where())
        except Exception as exc:
            logger.error(f"❌ Failed to create default SSL context: {exc}")
            return None

        # Always add certifi bundle as additional source (works on all platforms)
        try:
            context.load_verify_locations(cafile=certifi.where())
        except Exception as exc:
            logger.debug(
                f"Could not load certifi bundle (may already be included): {exc}"
            )

        default_used = False
        if not ca_file and not ca_path and DEFAULT_WS_CA_FILE.exists():
            ca_file = str(DEFAULT_WS_CA_FILE)
            default_used = True

        if ca_file or ca_path:
            try:
                resolved_file = Path(ca_file).expanduser() if ca_file else None
                resolved_path = Path(ca_path).expanduser() if ca_path else None
                context.load_verify_locations(
                    cafile=str(resolved_file) if resolved_file else None,
                    capath=str(resolved_path) if resolved_path else None,
                )
                if default_used:
                    logger.info(
                        "🔐 Loaded bundled Pett WebSocket CA from %s", resolved_file
                    )
                else:
                    logger.info(
                        "🔐 Loaded custom CA bundle for WebSocket verification (file=%s, path=%s)",
                        resolved_file,
                        resolved_path,
                    )
            except Exception as exc:
                logger.error(f"❌ Failed to load custom CA bundle: {exc}")

        return context

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        self._listener_task = None
        if self.websocket:
            await self.websocket.close()
        self.websocket = None
        self.connection_established = False
        self.authenticated = False
        # Note: We preserve _saved_auth_token and _was_previously_authenticated
        # for reconnection attempts
        logger.info("WebSocket connection closed")

    def set_privy_token(self, privy_token: str) -> None:
        """Update the stored Privy token without reconnecting."""
        token = (privy_token or "").strip()
        if not token:
            logger.warning(
                "⚠️ Attempted to set an empty Privy token - authentication will be disabled"
            )
            self.privy_token = ""
            return
        self.privy_token = token
        self._jwt_expired = False
        self._last_auth_error = None
        # logger.info("Privy token updated on WebSocket client")

    def clear_saved_auth_token(self) -> None:
        """Clear saved auth token and previous authentication state."""
        self._saved_auth_token = None
        self._was_previously_authenticated = False
        logger.info("Saved auth token cleared")

    async def refresh_token_and_reconnect(
        self, privy_token: str, max_retries: int = 3, auth_timeout: int = 10
    ) -> bool:
        """Update token, reset state, and reconnect/authenticate."""
        token = (privy_token or "").strip()
        if not token:
            logger.error("Cannot refresh connection with empty Privy token")
            return False

        self.set_privy_token(token)

        await self.disconnect()
        return await self.connect_and_authenticate(
            max_retries=max_retries, auth_timeout=auth_timeout
        )

    async def authenticate(self, timeout: int = 10) -> bool:
        """Default authentication using Privy token with timeout."""
        if not self.privy_token or not self.privy_token.strip():
            logger.warning("⚠️ No Privy token available for authentication")
            return False

        return await self.authenticate_privy(self.privy_token, timeout)

    async def authenticate_privy(
        self, privy_auth_token: str, timeout: int = 10
    ) -> bool:
        """Authenticate using Privy credentials with timeout and result waiting."""
        if not privy_auth_token or not privy_auth_token.strip():
            logger.error("Invalid Privy auth token provided")
            return False

        try:
            # Create a future to wait for the auth result
            auth_future: asyncio.Future[bool] = asyncio.Future()

            # Store the future so we can resolve it in the message handler
            self.auth_future = auth_future

            auth_message = {
                "type": "AUTH",
                "data": {
                    "params": {
                        "authHash": {"hash": "Bearer " + privy_auth_token.strip()},
                        "authType": "privy",
                    }
                },
            }

            # Send the auth message
            success = await self._send_message(auth_message)
            if not success:
                logger.error("Failed to send authentication message")
                return False

            # logger.debug("🔐 Authentication message sent, waiting for response...")

            # Wait for the auth result with timeout
            try:
                auth_result = await asyncio.wait_for(auth_future, timeout=timeout)
                # logger.info(f"🔐 Authentication result: {auth_result}")
                return auth_result
            except asyncio.TimeoutError:
                # Timeout on single attempt is not critical - caller will handle retries
                logger.debug(
                    f"⏱️ Authentication response not received within {timeout}s"
                )
                return False

        except Exception as e:
            logger.error(f"❌ Error during authentication: {e}")
            return False
        finally:
            # Clean up the future
            self.auth_future = None

    async def register_privy(
        self,
        pet_name: str,
        privy_auth_token: str,
        *,
        timeout: int = 15,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Register a new pet using Privy authentication and wait for the result."""
        name = (pet_name or "").strip()
        token = (privy_auth_token or "").strip()

        if not name:
            logger.error("Invalid pet name provided")
            self._last_action_error = "Pet name is required"
            return False, None

        if not token:
            logger.error("Invalid Privy auth token provided")
            self._last_action_error = "Privy token is required"
            return False, None

        # Ensure the client is ready to send messages
        self.set_privy_token(token)

        if not self.connection_established or not self.websocket:
            logger.info("🔌 Connecting WebSocket for pet registration")
            if not await self.connect():
                error_msg = "WebSocket connection failed during registration"
                logger.error(error_msg)
                self._last_action_error = error_msg
                return False, None

        # Ensure we are listening for responses before sending the register command
        if not self._listener_task or self._listener_task.done():
            logger.debug("👂 Starting listener task prior to registration")
            self._listener_task = asyncio.create_task(self.listen_for_messages())

        register_payload = {
            "params": {
                "registerHash": {
                    "name": name,
                    "hash": "Bearer " + token,
                },
                "authType": "privy",
            }
        }

        success, response = await self._send_and_wait(
            "REGISTER", register_payload, timeout=timeout
        )

        if not success:
            # Preserve any error routed through the correlated response
            if isinstance(response, dict):
                self._last_action_error = (
                    response.get("error")
                    or response.get("data", {}).get("error")
                    or self._last_action_error
                )
            return False, response

        # Inspect the response (if present) for explicit success/failure
        if isinstance(response, dict):
            payload = response.get("data", response)
            register_success = bool(payload.get("success", self.authenticated))
            if not register_success:
                err_text = payload.get("error") or "Registration failed"
                self._last_action_error = str(err_text)
                return False, response

        return True, response

    async def connect_and_authenticate(
        self, max_retries: int = 5, auth_timeout: int = 20
    ) -> bool:
        """Connect to WebSocket and authenticate using Privy token with retry logic."""
        # Check if we have a saved auth token to reuse
        token_to_use = None
        if self._was_previously_authenticated and self._saved_auth_token:
            token_to_use = self._saved_auth_token
            logger.info("🔄 Using saved auth token for reconnection")
        elif self.privy_token and self.privy_token.strip():
            token_to_use = self.privy_token
            logger.info("🆕 Using environment auth token for connection")

        # Skip authentication if no token available
        if not token_to_use:
            logger.warning(
                "⚠️ No auth token available (env or saved) - skipping authentication and retries"
            )
            return False

        for attempt in range(max_retries):
            try:
                logger.info(f"🔄 Connection attempt {attempt + 1}/{max_retries}")

                # Try to connect
                if not await self.connect():
                    logger.warning(f"❌ Connection attempt {attempt + 1} failed")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2**attempt)  # Exponential backoff
                        continue
                    return False

                logger.info("✅ WebSocket connected, starting message listener...")

                # Start listening for messages BEFORE authentication
                if self._listener_task and not self._listener_task.done():
                    self._listener_task.cancel()
                self._listener_task = asyncio.create_task(self.listen_for_messages())
                logger.info("👂 Started WebSocket message listener")

                logger.info("🔐 Attempting authentication...")
                # Try to authenticate using the selected token
                auth_success = await self.authenticate_privy(
                    token_to_use, timeout=auth_timeout
                )
                if not auth_success:
                    # Only warn on later attempts - first few failures are common during reconnection
                    if attempt >= 3:
                        logger.warning(
                            f"❌ Authentication attempt {attempt + 1}/{max_retries} failed"
                        )
                    else:
                        logger.info(
                            f"🔄 Authentication attempt {attempt + 1}/{max_retries} - retrying..."
                        )
                    await self.disconnect()

                    # If we were using a saved token and it failed, clear it
                    if (
                        self._was_previously_authenticated
                        and token_to_use == self._saved_auth_token
                    ):
                        logger.warning(
                            "🔑 Saved auth token failed, clearing saved state"
                        )
                        self.clear_saved_auth_token()
                        # Don't retry with the same failed token
                        return False

                    # Check if it's a JWT expiration error - don't retry in this case
                    if hasattr(self, "_last_auth_error") and self._last_auth_error:
                        if any(
                            keyword in str(self._last_auth_error).lower()
                            for keyword in [
                                "exp",
                                "jwt_expired",
                                "timestamp check failed",
                                "jwt",
                            ]
                        ):
                            self._jwt_expired = True
                            logger.critical(
                                "💀 JWT token expired - awaiting new token before reconnecting."
                            )
                            return False

                    # If server reports missing user/pet, do not continue retries; let caller handle registration
                    if hasattr(self, "_last_auth_error") and self._last_auth_error:
                        if "user not found" in str(self._last_auth_error).lower():
                            logger.info(
                                "🛑 Stopping auth retries due to missing user; caller should register"
                            )
                            return False

                    if attempt < max_retries - 1:
                        await asyncio.sleep(2**attempt)  # Exponential backoff
                        continue
                    return False

                logger.info("✅ Connection and authentication successful!")
                return True

            except Exception as e:
                logger.error(f"❌ Error in connection attempt {attempt + 1}: {e}")
                await self.disconnect()
                if attempt < max_retries - 1:
                    await asyncio.sleep(2**attempt)  # Exponential backoff
                    continue
                return False

        return False

    async def auth_ping(self, token: Optional[str] = None, timeout: int = 10) -> bool:
        """Send a lightweight AUTH to refresh pet data without restarting the client."""
        auth_token = (token or self.privy_token or "").strip()
        if not auth_token:
            logger.warning("auth_ping skipped: no Privy token available")
            return False

        async with self._auth_ping_lock:
            if not self.is_connected():
                logger.info("auth_ping: WebSocket disconnected, attempting reconnect")
                if not await self.connect():
                    logger.error("auth_ping failed: unable to connect WebSocket")
                    return False

            # Ensure listener is running to capture auth_result messages
            if not self._listener_task or self._listener_task.done():
                logger.debug("auth_ping: starting listener task for auth response")
                self._listener_task = asyncio.create_task(self.listen_for_messages())

            try:
                return await self.authenticate_privy(auth_token, timeout=timeout)
            except Exception as exc:
                logger.error("auth_ping error: %s", exc)
                return False

    async def _send_message(self, message: Dict[str, Any]) -> bool:
        """Send a message to the WebSocket server."""
        if not self.websocket or not self.connection_established:
            logger.error("WebSocket not connected")
            if self._telemetry_recorder:
                try:
                    self._telemetry_recorder(message, False, "WebSocket not connected")
                except Exception:
                    pass
            return False

        try:
            # Ensure a nonce is present on every outgoing message
            if "nonce" not in message:
                message["nonce"] = self._generate_nonce()
            message_json = json.dumps(message)
            await self.websocket.send(message_json)
            logger.info(f"📤 Sent message type: {message['type']}")
            if message.get("type") != "AUTH":
                logger.info(f"📤 Message content: {message_json}")

            if self._telemetry_recorder:
                try:
                    self._telemetry_recorder(message, True, None)
                except Exception:
                    pass
            return True
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            if self._telemetry_recorder:
                try:
                    self._telemetry_recorder(message, False, str(e))
                except Exception:
                    pass
            return False

    async def _send_and_wait(
        self,
        msg_type: str,
        data: Optional[Dict[str, Any]] = None,
        timeout: int = 10,
        *,
        verify: bool = False,
    ) -> tuple[bool, Optional[Dict[str, Any]]]:
        """Send a message with a nonce and wait for the correlated response.

        Returns a tuple of (success, response_message). Success is False if an error
        message is received or the wait times out or sending fails.
        """
        # Clear last error before starting a new request
        self._last_action_error = None
        nonce = self._generate_nonce()
        future = self._register_pending(nonce)

        message: Dict[str, Any] = {
            "type": msg_type,
            "data": data or {},
            "nonce": nonce,
        }
        if verify:
            message["verify"] = True

        sent = await self._send_message(message)
        if not sent:
            # Clean up pending future
            try:
                if nonce in self._pending_nonces:
                    self._pending_nonces.pop(nonce, None)
            except Exception:
                pass
            return False, None

        try:
            response: Dict[str, Any] = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            # No correlated error arrived within the window; assume success
            logger.info(
                f"⏱️ No error received within {timeout}s for {msg_type} (nonce {nonce}); assuming success"
            )
            return True, None
        except Exception as e:
            logger.error(
                f"❌ Error awaiting response for {msg_type} (nonce {nonce}): {e}"
            )
            try:
                self._last_action_error = str(e)
            except Exception:
                pass
            return False, None

        # Treat explicit error type as failure
        if isinstance(response, dict) and (response.get("type") == "error"):
            try:
                err_text = response.get("error")
                if err_text is not None:
                    self._last_action_error = str(err_text)
            except Exception:
                pass
            return False, response

        return True, response

    def _extract_verification(
        self, message: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Extract verification payload from a correlated response message."""
        try:
            if not isinstance(message, dict):
                return None
            data = message.get("data", {})
            verification = data.get("verification")
            if isinstance(verification, dict):
                return verification
        except Exception:
            pass
        return None

    def _contains_already_clean_error(self, message: Optional[Dict[str, Any]]) -> bool:
        """Return True if the message contains an 'already clean' type error."""
        try:
            if not isinstance(message, dict):
                return False
            err = message.get("error") or message.get("data", {}).get("error")
            if not err:
                return False
            return "already clean" in str(err).lower()
        except Exception:
            return False

    async def listen_for_messages(self) -> None:
        """Listen for incoming messages from the server."""
        if not self.websocket or not self.connection_established:
            logger.error("❌ WebSocket not connected - cannot listen for messages")
            return

        logger.info("👂 Starting WebSocket message listener...")
        try:
            async for message in self.websocket:
                try:
                    message_data = json.loads(message)
                    await self._handle_message(message_data)
                except json.JSONDecodeError as e:
                    logger.error(f"❌ Failed to parse WebSocket message: {e}")
                    logger.error(f"❌ Raw message: {message}")
                except Exception as e:
                    logger.error(f"❌ Error handling WebSocket message: {e}")

        except websockets.exceptions.ConnectionClosed:
            logger.warning("⚠️ WebSocket connection closed during message listening")
            self.connection_established = False
        except Exception as e:
            logger.error(f"❌ Error in WebSocket message listener: {e}")
            self.connection_established = False

    async def _handle_message(self, message: Dict[str, Any]) -> None:
        """Handle incoming messages from the server."""
        message_type = message.get("type")
        # Resolve any waiting caller by nonce, if present
        try:
            self._resolve_pending(message.get("nonce"), message)
        except Exception:
            pass
        if message_type == "auth_result":
            await self._handle_auth_result(message)
        elif message_type == "pet_update":
            await self._handle_pet_update(message)
        elif message_type == "error":
            await self._handle_error(message)
        elif message_type == "data":
            await self._handle_data(message)

        # Call registered handlers
        if message_type in self.message_handlers:
            for handler in self.message_handlers[message_type]:
                try:
                    await handler(message)
                except Exception as e:
                    logger.error(f"Error in message handler: {e}")

    async def _handle_auth_result(self, message: Dict[str, Any]) -> None:
        """Handle authentication result message."""
        # Handle both message structures: with and without 'data' wrapper
        if "data" in message:
            data = message.get("data", {})
            success = data.get("success", False)
            error = data.get("error", "Unknown error")
            user_data = data.get("user", {})
            pet_data = data.get("pet", {})
        else:
            # Direct structure: {'type': 'auth_result', 'success': False, 'error': '...'}
            success = message.get("success", False)
            error = message.get("error", "Unknown error")
            user_data = message.get("user", {})
            pet_data = message.get("pet", {})

        if success:
            self.authenticated = True
            # Reset JWT expiration flag on successful auth
            self._jwt_expired = False
            self._last_auth_error = None  # Clear any previous errors
            # Save auth token for reconnection use
            self._saved_auth_token = self.privy_token
            self._was_previously_authenticated = True

            # Extract pet data - now it's directly in the pet field
            if pet_data:
                # Use the pet data directly
                self.pet_data = pet_data
                """
                logger.info("✅ Authentication successful!")
                logger.info(f"👤 User: {user_data.get('id', 'Unknown')}")
                logger.info(f"🔑 Privy ID: {user_data.get('privyID', 'Unknown')}")
                logger.info(f"📱 Telegram ID: {user_data.get('telegramID', 'Unknown')}")

                # Log pet information
                pet = self.pet_data
                if pet:
                    logger.info(f"🐾 Pet: {pet.get('name', 'Unknown')}")
                    logger.info(f"🆔 Pet ID: {pet.get('id', 'Unknown')}")
                    # Format balance from wei to ETH
                    raw_balance = pet.get("PetTokens", {}).get("tokens", "0")
                    formatted_balance = format_wei_to_eth(raw_balance)
                    logger.info(f"💰 Balance: {formatted_balance} $AIP")
                    logger.info(f"🏨 Hotel Tier: {pet.get('currentHotelTier', 0)}")
                    logger.info(f"💀 Dead: {pet.get('dead', False)}")
                    logger.info(f"😴 Sleeping: {pet.get('sleeping', False)}")

                    # Log pet stats
                    pet_stats = pet.get("PetStats", {})
                    if pet_stats:
                        logger.info("📊 Pet Stats:")
                        logger.info(f"   🍽️  Hunger: {pet_stats.get('hunger', 0)}")
                        logger.info(f"   ❤️  Health: {pet_stats.get('health', 0)}")
                        logger.info(f"   ⚡ Energy: {pet_stats.get('energy', 0)}")
                        logger.info(f"   😊 Happiness: {pet_stats.get('happiness', 0)}")
                        logger.info(f"   🧼 Hygiene: {pet_stats.get('hygiene', 0)}")
                        logger.info(
                            f"   🎯 XP: {pet_stats.get('xp', 0)}/"
                            f"{pet_stats.get('xpMax', 0)} (Level {pet_stats.get('level', 1)})"
                        )
                        
                """

            else:
                self.pet_data = {}
                logger.info("✅ Authentication successful but no pet found")
                logger.info(f"👤 User: {user_data.get('id', 'Unknown')}")
                logger.info(f"🔑 Privy ID: {user_data.get('privyID', 'Unknown')}")
                logger.info(f"📱 Telegram ID: {user_data.get('telegramID', 'Unknown')}")
        else:
            logger.error(f"❌ Authentication failed: {error}")
            self.authenticated = False

            # Store the error for retry logic
            self._last_auth_error = str(error)

            # Clear saved auth token on authentication failure
            if self._was_previously_authenticated:
                logger.info(
                    "🔑 Clearing saved auth token due to authentication failure"
                )
                self.clear_saved_auth_token()

            # Check if it's a JWT expiration error
            if any(
                k in str(error)
                for k in ("exp", "JWT_EXPIRED", "timestamp check failed")
            ):
                self._jwt_expired = True
                logger.error(
                    "🔑 JWT token has expired! Please get a new token from your authentication provider."
                )
                logger.error(
                    "💡 This usually means you need to refresh your Privy token or get a new one."
                )
                logger.error(self.get_token_refresh_instructions())
                logger.critical("💀 JWT token expired - waiting for refresh.")

        # Resolve the auth future if it exists
        if self.auth_future and not self.auth_future.done():
            self.auth_future.set_result(success)

    def _merge_pet_data(
        self, base: Dict[str, Any], new: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Merge new pet data into existing data, preserving nested fields when missing.

        - Only overwrite keys present in the new payload
        - For dict values (e.g., PetStats, PetTokens), perform a shallow merge
        - Preserve existing PetStats if the new payload lacks it or it is empty
        """
        if not isinstance(base, dict):
            base = {}

        merged: Dict[str, Any] = dict(base)

        for key, new_value in (new or {}).items():
            # Special handling for PetStats: ignore empty updates
            if key == "PetStats":
                if isinstance(new_value, dict) and new_value:
                    old_stats = merged.get("PetStats", {})
                    if isinstance(old_stats, dict):
                        # Shallow merge stats
                        updated_stats = dict(old_stats)
                        updated_stats.update(new_value)
                        merged["PetStats"] = updated_stats
                    else:
                        merged["PetStats"] = new_value
                else:
                    # Skip overwriting existing stats with empty/none
                    continue
                continue

            # Generic shallow merge for nested dicts
            if isinstance(new_value, dict) and isinstance(merged.get(key), dict):
                updated_dict = dict(merged.get(key) or {})
                updated_dict.update(new_value)
                merged[key] = updated_dict
            else:
                merged[key] = new_value

        return merged

    async def _handle_pet_update(self, message: Dict[str, Any]) -> None:
        """Handle pet update message."""
        # Handle both message structures: with and without 'data' wrapper
        if "data" in message:
            data = message.get("data", {})
            user_data = data.get("user", {})
            pet_data = data.get("pet", {})
        else:
            # Direct structure
            user_data = message.get("user", {})
            pet_data = message.get("pet", {})

        # Update pet data
        if pet_data:
            # Merge with existing data to avoid losing fields on partial updates
            if self.pet_data and isinstance(self.pet_data, dict):
                merged = self._merge_pet_data(self.pet_data, pet_data)
                # If pet id changes, prefer new payload entirely
                old_id = self.pet_data.get("id")
                new_id = pet_data.get("id")
                self.pet_data = merged if not old_id or old_id == new_id else pet_data
                logger.info("Pet Status updated (merged partial update)")
            else:
                self.pet_data = pet_data
                logger.info("Pet Status updated")
            logger.info(f"Updated pet data: {self.pet_data}")
        elif user_data:
            # If we got user data, extract pet from it
            pets = user_data.get("pets", [])
            if pets:
                pet_from_user = pets[0]
                if self.pet_data and isinstance(self.pet_data, dict):
                    self.pet_data = self._merge_pet_data(self.pet_data, pet_from_user)
                    logger.info("Pet updated from user data (merged)")
                else:
                    self.pet_data = pet_from_user
                    logger.info("Pet updated from user data")
                logger.info(f"Updated pet data: {self.pet_data}")

    async def _handle_error(self, message: Dict[str, Any]) -> None:
        """Handle error message."""
        error = message.get("error")
        logger.error(f"Server error: {error}")
        try:
            if error is not None:
                self._last_action_error = str(error)
        except Exception:
            pass

    async def _handle_data(self, message: Dict[str, Any]) -> None:
        """Handle data message."""
        self.data_message = message
        logger.info("📊 Received data message")
        logger.info(f"Data message: {message}")

        # Handle AI search results
        if self.ai_search_future and not self.ai_search_future.done():
            try:
                # Extract AI search result from the message
                ai_result = message.get("data", {}).get("result", "")
                if ai_result:
                    self.ai_search_future.set_result(ai_result)
                else:
                    self.ai_search_future.set_result("No search results found")
            except Exception as e:
                logger.error(f"Error handling AI search result: {e}")
                if not self.ai_search_future.done():
                    self.ai_search_future.set_result(
                        f"Error processing search result: {str(e)}"
                    )

        # Handle kitchen data
        if self.kitchen_future and not self.kitchen_future.done():
            try:
                kitchen_data = message.get("data", {})
                if kitchen_data:
                    self.kitchen_future.set_result(json.dumps(kitchen_data, indent=2))
                else:
                    self.kitchen_future.set_result("No kitchen data found")
            except Exception as e:
                logger.error(f"Error handling kitchen data: {e}")
                if not self.kitchen_future.done():
                    self.kitchen_future.set_result(
                        f"Error processing kitchen data: {str(e)}"
                    )

        # Handle mall data
        if self.mall_future and not self.mall_future.done():
            try:
                mall_data = message.get("data", {})
                if mall_data:
                    self.mall_future.set_result(json.dumps(mall_data, indent=2))
                else:
                    self.mall_future.set_result("No mall data found")
            except Exception as e:
                logger.error(f"Error handling mall data: {e}")
                if not self.mall_future.done():
                    self.mall_future.set_result(f"Error processing mall data: {str(e)}")

        # Handle closet data
        if self.closet_future and not self.closet_future.done():
            try:
                closet_data = message.get("data", {})
                if closet_data:
                    self.closet_future.set_result(json.dumps(closet_data, indent=2))
                else:
                    self.closet_future.set_result("No closet data found")
            except Exception as e:
                logger.error(f"Error handling closet data: {e}")
                if not self.closet_future.done():
                    self.closet_future.set_result(
                        f"Error processing closet data: {str(e)}"
                    )

    def register_message_handler(self, message_type: str, handler: Callable) -> None:
        """Register a handler for a specific message type."""
        if message_type not in self.message_handlers:
            self.message_handlers[message_type] = []
        self.message_handlers[message_type].append(handler)

    # Pet action methods
    async def rub_pet(self, *, record_on_chain: Optional[bool] = None) -> bool:
        """Rub the pet."""
        record = (
            self._onchain_recording_enabled
            if record_on_chain is None
            else bool(record_on_chain)
        )
        success, response = await self._send_and_wait(
            "RUB", {}, timeout=10, verify=record
        )
        verification = self._extract_verification(response)
        if success or (not success and self._contains_already_clean_error(response)):
            if verification and record:
                logger.info(
                    "🧾 RUB: submitting verified on-chain record (success or already clean)"
                )
                self._schedule_verified_record_action("RUB", verification)
        return bool(success)

    async def shower_pet(self, *, record_on_chain: Optional[bool] = None) -> bool:
        """Give the pet a shower."""
        record = (
            self._onchain_recording_enabled
            if record_on_chain is None
            else bool(record_on_chain)
        )
        success, response = await self._send_and_wait(
            "SHOWER", {}, timeout=10, verify=record
        )
        verification = self._extract_verification(response)
        if success or (not success and self._contains_already_clean_error(response)):
            if verification and record:
                logger.info(
                    "🧾 SHOWER: submitting verified on-chain record (success or already clean)"
                )
                self._schedule_verified_record_action("SHOWER", verification)
        return bool(success)

    async def sleep_pet(self, record_on_chain: Optional[bool] = None) -> bool:
        """Put the pet to sleep."""
        record = (
            self._onchain_recording_enabled
            if record_on_chain is None
            else bool(record_on_chain)
        )
        success, response = await self._send_and_wait(
            "SLEEP", {}, timeout=10, verify=record
        )
        if not success:
            return False

        logger.info("✅ SLEEP action confirmed by server")
        if not record:
            return True

        verification = self._extract_verification(response)
        if verification:
            logger.info("📗 Submitting verified SLEEP action on-chain")
            self._schedule_verified_record_action("SLEEP", verification)
            return True

        recorder_enabled = bool(
            self._action_recorder and self._action_recorder.is_enabled
        )
        if recorder_enabled:
            logger.warning(
                "🧾 SLEEP verification missing; will retry to ensure on-chain record"
            )
            return False

        return True

    async def throw_ball(self, *, record_on_chain: Optional[bool] = None) -> bool:
        """Throw a ball for the pet."""
        record = (
            self._onchain_recording_enabled
            if record_on_chain is None
            else bool(record_on_chain)
        )
        success, response = await self._send_and_wait(
            "THROWBALL", {}, timeout=10, verify=record
        )
        if success:
            verification = self._extract_verification(response)
            if verification and record:
                logger.info(
                    "✅ THROWBALL action confirmed; submitting verified on-chain record"
                )
                self._schedule_verified_record_action("THROWBALL", verification)
        return bool(success)

    async def use_consumable(
        self, consumable_id: str, *, record_on_chain: Optional[bool] = None
    ) -> bool:
        """Use a consumable item."""
        if not consumable_id or not consumable_id.strip():
            logger.error(f"Invalid consumable ID provided: {consumable_id!r}")
            return False

        consumable_id = consumable_id.strip().strip('"').strip("'")
        logger.info(f"🍴 Using consumable: {consumable_id}")

        record = (
            self._onchain_recording_enabled
            if record_on_chain is None
            else bool(record_on_chain)
        )
        success, response = await self._send_and_wait(
            "CONSUMABLES_USE",
            {"params": {"foodId": consumable_id}},
            timeout=15,
            verify=record,
        )

        if success:
            verification = self._extract_verification(response)
            if verification and record:
                self._schedule_verified_record_action("CONSUMABLES_USE", verification)
            return True

        # Check for rate limiting errors
        error_text = ""
        if isinstance(response, dict):
            error_text = str(response.get("error", ""))

        # Handle rate limiting with exponential backoff
        if error_text and (
            "too quickly" in error_text.lower() or "rate limit" in error_text.lower()
        ):
            logger.warning(
                f"⏳ Rate limited when using {consumable_id}. Waiting before retry..."
            )
            await asyncio.sleep(2.0)  # Wait 2 seconds before returning False
            return False

        # If use failed (but not rate limited), wait before any retry to avoid rate limiting
        if not success:
            await asyncio.sleep(1.0)

        # Attempt auto-buy on "not found" error then retry once
        if error_text and ("not found" in error_text.lower()):
            logger.info(
                f"🛒 Consumable {consumable_id} not owned. Attempting to buy one and retry."
            )
            buy_success, _ = await self._send_and_wait(
                "CONSUMABLES_BUY",
                {"params": {"foodId": consumable_id, "amount": 1}},
                timeout=15,
            )
            if not buy_success:
                logger.warning(
                    f"❌ Failed to buy missing consumable {consumable_id}; will not retry use."
                )
                return False

            # Wait before retrying use after purchase to avoid rate limiting
            await asyncio.sleep(1.0)
            # Retry once after successful buy
            logger.info(f"🔁 Retrying use of {consumable_id} after purchase")
            retry_success, retry_resp = await self._send_and_wait(
                "CONSUMABLES_USE",
                {"params": {"foodId": consumable_id}},
                timeout=15,
                verify=record,
            )
            if retry_success:
                verification2 = self._extract_verification(retry_resp)
                if verification2 and record:
                    self._schedule_verified_record_action(
                        "CONSUMABLES_USE", verification2
                    )
            return bool(retry_success)

        return False

    async def buy_consumable(
        self,
        consumable_id: str,
        amount: int,
        *,
        record_on_chain: Optional[bool] = None,
    ) -> bool:
        """Buy a consumable item for the pet.

        Args:
            consumable_id: The ID of the consumable to buy. Allowed values:
                "BURGER", "SALAD", "STEAK", "COOKIE", "PIZZA", "SUSHI",
                "ENERGIZER", "POTION", "XP_POTION", "SUPER_XP_POTION",
                "SMALL_POTION", "LARGE_POTION", "REVIVE_POTION",
                "POISONOUS_ARROW", "REINFORCED_SHIELD", "BATTLE_SWORD",
                "ACCOUNTANT"
            amount: The number of consumables to buy (default: 1).
        """
        if not consumable_id or not consumable_id.strip():
            logger.error("Invalid consumable ID provided")
            return False

        if amount <= 0:
            logger.error("Amount must be greater than 0")
            return False

        # Normalize the ID to avoid accidental surrounding quotes
        consumable_id = consumable_id.strip().strip('"').strip("'")
        record = (
            self._onchain_recording_enabled
            if record_on_chain is None
            else bool(record_on_chain)
        )

        success, resp = await self._send_and_wait(
            "CONSUMABLES_BUY",
            {"params": {"foodId": consumable_id, "amount": amount}},
            timeout=15,
            verify=record,
        )

        # Check for rate limiting errors
        if not success and isinstance(resp, dict):
            error_text = str(resp.get("error", ""))
            if error_text and (
                "too quickly" in error_text.lower()
                or "rate limit" in error_text.lower()
            ):
                logger.warning(
                    f"⏳ Rate limited when buying {consumable_id}. Waiting before returning..."
                )
                await asyncio.sleep(2.0)  # Wait 2 seconds before returning False

        if success and record:
            verification = self._extract_verification(resp)
            if verification:
                self._schedule_verified_record_action("CONSUMABLES_BUY", verification)
        return bool(success)

    async def get_consumables(self) -> bool:
        """Get available consumables."""
        logger.info("[TOOL] Getting consumables")
        success, _ = await self._send_and_wait("CONSUMABLES_GET", {}, timeout=10)
        return bool(success)

    async def fetch_consumables_inventory(
        self, timeout: int = 10
    ) -> Optional[List[Dict[str, Any]]]:
        """Return the structured list of owned consumables via CONSUMABLES_GET."""

        success, response = await self._send_and_wait(
            "CONSUMABLES_GET", {}, timeout=timeout
        )
        if not success:
            logger.warning("❌ Failed to fetch consumables inventory")
            return None

        payload: Dict[str, Any] = {}
        if isinstance(response, dict):
            payload = response.get("data", response) or {}

        raw_items = payload.get("consumables", [])
        if not isinstance(raw_items, list):
            logger.debug(
                "Consumables inventory payload missing list; received type %s",
                type(raw_items).__name__,
            )
            return []

        inventory: List[Dict[str, Any]] = []
        for item in raw_items:
            if isinstance(item, dict):
                inventory.append(item)

        logger.debug("📦 Retrieved %d owned consumables", len(inventory))
        return inventory

    async def get_kitchen(self) -> bool:
        """Get kitchen information."""
        success, _ = await self._send_and_wait("KITCHEN_GET", {}, timeout=10)
        return bool(success)

    async def get_kitchen_data(self, timeout: int = 10) -> str:
        """Get kitchen information and wait for the result.

        Args:
            timeout: Maximum time to wait for response in seconds (default: 10)

        Returns:
            The kitchen data as a JSON string, or error message if failed
        """
        try:
            # Create a future to wait for the kitchen data
            self.kitchen_future = asyncio.Future()

            # Send the kitchen request
            success = await self._send_message({"type": "KITCHEN_GET", "data": {}})

            if not success:
                return "❌ Failed to send kitchen request"

            logger.info("[TOOL] Sent kitchen request")
            logger.info(f"[TOOL] Waiting up to {timeout} seconds for response...")

            # Wait for the result with timeout
            try:
                result: str = await asyncio.wait_for(
                    self.kitchen_future, timeout=timeout
                )
                return result

            except asyncio.TimeoutError:
                logger.warning(
                    f"[TOOL] Kitchen request timed out after {timeout} seconds"
                )
                return f"❌ Kitchen request timed out after {timeout} seconds. Please try again."

        except Exception as e:
            logger.error(f"[TOOL] Error during kitchen request: {e}")
            return f"❌ Error during kitchen request: {str(e)}"
        finally:
            # Clean up the future
            self.kitchen_future = None

    async def get_mall(self) -> bool:
        """Get mall information."""
        success, _ = await self._send_and_wait("MALL_GET", {}, timeout=10)
        return bool(success)

    async def get_mall_data(self, timeout: int = 10) -> str:
        """Get mall information and wait for the result.

        Args:
            timeout: Maximum time to wait for response in seconds (default: 10)

        Returns:
            The mall data as a JSON string, or error message if failed
        """
        try:
            # Create a future to wait for the mall data
            self.mall_future = asyncio.Future()

            # Send the mall request
            success = await self._send_message({"type": "MALL_GET", "data": {}})

            if not success:
                return "❌ Failed to send mall request"

            logger.info("[TOOL] Sent mall request")
            logger.info(f"[TOOL] Waiting up to {timeout} seconds for response...")

            # Wait for the result with timeout
            try:
                result: str = await asyncio.wait_for(self.mall_future, timeout=timeout)
                return result

            except asyncio.TimeoutError:
                logger.warning(f"[TOOL] Mall request timed out after {timeout} seconds")
                return f"❌ Mall request timed out after {timeout} seconds. Please try again."

        except Exception as e:
            logger.error(f"[TOOL] Error during mall request: {e}")
            return f"❌ Error during mall request: {str(e)}"
        finally:
            # Clean up the future
            self.mall_future = None

    async def get_closet(self) -> bool:
        """Get closet information."""
        success, _ = await self._send_and_wait("CLOSET_GET", {}, timeout=10)
        return bool(success)

    async def get_closet_data(self, timeout: int = 10) -> str:
        """Get closet information and wait for the result.

        Args:
            timeout: Maximum time to wait for response in seconds (default: 10)

        Returns:
            The closet data as a JSON string, or error message if failed
        """
        try:
            # Create a future to wait for the closet data
            self.closet_future = asyncio.Future()

            # Send the closet request
            success = await self._send_message({"type": "CLOSET_GET", "data": {}})

            if not success:
                return "❌ Failed to send closet request"

            logger.info("[TOOL] Sent closet request")
            logger.info(f"[TOOL] Waiting up to {timeout} seconds for response...")

            # Wait for the result with timeout
            try:
                result: str = await asyncio.wait_for(
                    self.closet_future, timeout=timeout
                )
                return result

            except asyncio.TimeoutError:
                logger.warning(
                    f"[TOOL] Closet request timed out after {timeout} seconds"
                )
                return f"❌ Closet request timed out after {timeout} seconds. Please try again."

        except Exception as e:
            logger.error(f"[TOOL] Error during closet request: {e}")
            return f"❌ Error during closet request: {str(e)}"
        finally:
            # Clean up the future
            self.closet_future = None

    async def use_accessory(
        self, accessory_id: str, *, record_on_chain: Optional[bool] = None
    ) -> bool:
        """Use an accessory."""
        if not accessory_id or not accessory_id.strip():
            logger.error("Invalid accessory ID provided")
            return False

        record = (
            self._onchain_recording_enabled
            if record_on_chain is None
            else bool(record_on_chain)
        )
        success, response = await self._send_and_wait(
            "ACCESSORY_USE",
            {"params": {"accessoryId": accessory_id.strip()}},
            timeout=10,
            verify=record,
        )
        if success:
            verification = self._extract_verification(response)
            if verification and record:
                self._schedule_verified_record_action("ACCESSORY_USE", verification)
        return bool(success)

    async def buy_accessory(
        self, accessory_id: str, *, record_on_chain: Optional[bool] = None
    ) -> bool:
        """Buy an accessory."""
        if not accessory_id or not accessory_id.strip():
            logger.error("Invalid accessory ID provided")
            return False

        record = (
            self._onchain_recording_enabled
            if record_on_chain is None
            else bool(record_on_chain)
        )
        success, response = await self._send_and_wait(
            "ACCESSORY_BUY",
            {"params": {"accessoryId": accessory_id.strip()}},
            timeout=10,
            verify=record,
        )
        if success:
            verification = self._extract_verification(response)
            if verification and record:
                self._schedule_verified_record_action("ACCESSORY_BUY", verification)
        return bool(success)

    async def ai_search(self, prompt: str, timeout: int = 30) -> str:
        """Perform AI search and wait for the result.

        Args:
            prompt: The search prompt to send
            timeout: Maximum time to wait for response in seconds (default: 30)

        Returns:
            The search result as a string, or error message if failed
        """
        if not prompt or not prompt.strip():
            logger.error("Invalid search prompt provided")
            return "❌ Invalid search prompt provided"

        try:
            # Create a future to wait for the AI search result
            self.ai_search_future = asyncio.Future()

            # Send the AI search request
            success = await self._send_message(
                {
                    "type": "AI_SEARCH",
                    "data": {"params": {"prompt": prompt.strip(), "type": "web"}},
                }
            )

            if not success:
                return "❌ Failed to send AI search request"

            logger.info(f"[TOOL] Sent AI search request: {prompt}")
            logger.info(f"[TOOL] Waiting up to {timeout} seconds for response...")

            # Wait for the result with timeout
            try:
                result: str = await asyncio.wait_for(
                    self.ai_search_future, timeout=timeout
                )
                return result

            except asyncio.TimeoutError:
                logger.warning(f"[TOOL] AI search timed out after {timeout} seconds")
                return (
                    f"❌ AI search timed out after {timeout} seconds. Please try again."
                )

        except Exception as e:
            logger.error(f"[TOOL] Error during AI search: {e}")
            return f"❌ Error during AI search: {str(e)}"
        finally:
            # Clean up the future
            self.ai_search_future = None

    async def proxy_llm_completion(
        self,
        params: Dict[str, Any],
        *,
        timeout: int = 45,
    ) -> Optional[Dict[str, Any]]:
        """Proxy a LangChain chat completion via the backend WebSocket."""
        messages = params.get("messages")
        if not isinstance(messages, list) or not messages:
            logger.error("LLM proxy requires a non-empty 'messages' list")
            return None

        metadata = params.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.setdefault("source", "agent_llm_proxy")
        params["metadata"] = metadata

        success, response = await self._send_and_wait(
            "LLM_PROXY",
            {"params": params},
            timeout=timeout,
        )
        if not success:
            logger.error("LLM proxy request failed: %s", self._last_action_error)
            return None

        if isinstance(response, dict):
            data = response.get("data")
            if isinstance(data, dict):
                return data
            return response
        return None

    async def get_personality(self) -> bool:
        """Get pet personality information."""
        logger.info("[TOOL] Getting pet personality information")
        return await self._send_message({"type": "PERSONALITY_GET", "data": {}})

    async def generate_image(self, prompt: str) -> bool:
        """Generate an image."""
        if not prompt or not prompt.strip():
            logger.error("Invalid image prompt provided")
            return False

        return await self._send_message(
            {"type": "GEN_IMAGE", "data": {"params": {"prompt": prompt.strip()}}}
        )

    async def hotel_check_in(self, *, record_on_chain: Optional[bool] = None) -> bool:
        """Check pet into hotel."""
        logger.info("[TOOL] Checking pet into hotel")
        record = (
            self._onchain_recording_enabled
            if record_on_chain is None
            else bool(record_on_chain)
        )
        success, response = await self._send_and_wait(
            "HOTEL_CHECK_IN", {}, timeout=10, verify=record
        )
        if success:
            verification = self._extract_verification(response)
            if verification and record:
                self._schedule_verified_record_action("HOTEL_CHECK_IN", verification)
        return bool(success)

    async def hotel_check_out(self, *, record_on_chain: Optional[bool] = None) -> bool:
        """Check pet out of hotel."""
        logger.info("[TOOL] Checking pet out of hotel")
        record = (
            self._onchain_recording_enabled
            if record_on_chain is None
            else bool(record_on_chain)
        )
        success, response = await self._send_and_wait(
            "HOTEL_CHECK_OUT", {}, timeout=10, verify=record
        )
        if success:
            verification = self._extract_verification(response)
            if verification and record:
                self._schedule_verified_record_action("HOTEL_CHECK_OUT", verification)
        return bool(success)

    async def buy_hotel(self, tier: str) -> bool:
        """Buy hotel tier."""
        if not tier or not tier.strip():
            logger.error("Invalid hotel tier provided")
            return False

        success, response = await self._send_and_wait(
            "HOTEL_BUY", {"params": {"tier": tier.strip()}}, timeout=10
        )
        if success:
            verification = self._extract_verification(response)
            if verification:
                self._schedule_verified_record_action("HOTEL_BUY", verification)
        return bool(success)

    async def get_office(self) -> bool:
        """Get office information."""
        success, _ = await self._send_and_wait("OFFICE_GET", {}, timeout=10)
        return bool(success)

    def get_pet_data(self) -> Optional[Dict[str, Any]]:
        """Get current pet data."""
        return self.pet_data

    def get_pet_stats(self) -> Optional[Dict[str, Any]]:
        """Get current pet stats."""
        if self.pet_data:
            return self.pet_data.get("PetStats", {})
        return None

    def get_pet_name(self) -> Optional[str]:
        """Get current pet name."""
        if self.pet_data:
            return self.pet_data.get("name")
        return None

    def get_pet_id(self) -> Optional[str]:
        """Get current pet ID."""
        if self.pet_data:
            return self.pet_data.get("id")
        return None

    def get_pet_balance(self) -> Optional[str]:
        """Get current pet balance formatted as ETH."""
        if self.pet_data:
            # Try to get balance from PetTokens first, then fallback to balance field
            raw_balance = self.pet_data.get("PetTokens", {}).get(
                "tokens", self.pet_data.get("balance", "0")
            )
            return format_wei_to_eth(raw_balance)
        return None

    def get_pet_hotel_tier(self) -> int:
        """Get current pet hotel tier."""
        if self.pet_data:
            return self.pet_data.get("currentHotelTier", 0)
        return 0

    def get_pet_hunger(self) -> int:
        """Get current pet hunger level."""
        stats = self.get_pet_stats()
        return stats.get("hunger", 0) if stats else 0

    def get_pet_health(self) -> int:
        """Get current pet health level."""
        stats = self.get_pet_stats()
        return stats.get("health", 0) if stats else 0

    def get_pet_energy(self) -> int:
        """Get current pet energy level."""
        stats = self.get_pet_stats()
        return stats.get("energy", 0) if stats else 0

    def get_pet_happiness(self) -> int:
        """Get current pet happiness level."""
        stats = self.get_pet_stats()
        return stats.get("happiness", 0) if stats else 0

    def get_pet_hygiene(self) -> int:
        """Get current pet hygiene level."""
        stats = self.get_pet_stats()
        return stats.get("hygiene", 0) if stats else 0

    def get_pet_status_summary(self) -> Dict[str, Any]:
        """Get a summary of current pet status."""
        if not self.pet_data:
            return {}

        return {
            "name": self.get_pet_name(),
            "id": self.get_pet_id(),
            "balance": self.get_pet_balance(),
            "hotel_tier": self.get_pet_hotel_tier(),
            "stats": {
                "hunger": self.get_pet_hunger(),
                "health": self.get_pet_health(),
                "energy": self.get_pet_energy(),
                "happiness": self.get_pet_happiness(),
                "hygiene": self.get_pet_hygiene(),
            },
        }

    def get_last_action_error(self) -> Optional[str]:
        """Return the last action error text captured from server responses."""
        return self._last_action_error

    def clear_last_action_error(self) -> None:
        """Clear the stored last action error."""
        self._last_action_error = None

    def get_last_auth_error(self) -> Optional[str]:
        """Return the most recent authentication error message, if any."""
        return self._last_auth_error

    def is_authenticated(self) -> bool:
        """Check if client is authenticated."""
        return self.authenticated

    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self.connection_established

    def is_jwt_expired(self) -> bool:
        """Check if JWT token has expired."""
        return self._jwt_expired

    def get_token_refresh_instructions(self) -> str:
        """Get instructions for refreshing the JWT token."""
        return """
🔑 JWT Token Refresh Instructions:

1. **For Privy Authentication:**
   - Go to your Privy dashboard or authentication flow
   - Generate a new access token
   - Update your PRIVY_TOKEN environment variable

2. **Common Token Sources:**
   - Privy Dashboard → Access Tokens
   - Your authentication provider's token endpoint
   - Mobile app authentication flow

3. **Environment Variable:**
   - Update PRIVY_TOKEN in your .env file
   - Restart the agent after updating the token

4. **Token Format:**
   - Ensure the token is valid and not expired
   - Remove any "Bearer " prefix if present
   - The token should be the raw JWT string
"""
