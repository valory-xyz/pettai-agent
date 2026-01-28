import asyncio
import base64
import hashlib
import json
import logging
import os
import platform
import random
import ssl
import stat
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import certifi
import websockets
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

from .action_recorder import ActionRecorder

try:
    from .constants import REQUIRED_ACTIONS_PER_EPOCH
except ImportError:
    # Fallback for when constants module is not available
    REQUIRED_ACTIONS_PER_EPOCH = 9

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
        session_token: Optional[str] = None,
        encryption_password: Optional[str] = None,
    ):
        self.websocket_url = websocket_url
        self.websocket: Optional[Any] = None
        self.authenticated = False
        self.pet_data: Optional[Dict[str, Any]] = None
        self.message_handlers: Dict[str, List[Callable]] = {}
        self.connection_established = False
        self.privy_token = (privy_token or os.getenv("PRIVY_TOKEN") or "").strip()
        self.session_token = (
            session_token or os.getenv("PETT_SESSION_TOKEN") or ""
        ).strip()
        # Password for encrypting/decrypting session tokens
        self._encryption_password = encryption_password or os.getenv(
            "SESSION_TOKEN_PASSWORD"
        )
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
        # Lock to prevent concurrent reconnection attempts
        self._reconnect_lock: asyncio.Lock = asyncio.Lock()
        # Flag to track if reconnection is in progress
        self._reconnecting: bool = False
        # Outgoing message telemetry recorder: (message, success, error)
        self._telemetry_recorder: Optional[
            Callable[[Dict[str, Any], bool, Optional[str]], None]
        ] = None
        # Enable/disable on-chain recordAction scheduling globally
        self._onchain_recording_enabled: bool = True
        # Pending nonce -> future mapping for correlating responses
        self._pending_nonces: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
        if not self.privy_token and not self.session_token:
            logger.warning(
                "No auth token provided during initialization; authentication will be disabled until a token is set."
            )
        self._action_recorder: Optional[ActionRecorder] = None
        # Last action error text captured from server responses
        self._last_action_error: Optional[str] = None
        # Persistent auth token storage for reconnection
        self._saved_auth_token: Optional[str] = None
        self._saved_auth_type: Optional[str] = None
        self._was_previously_authenticated: bool = False
        self._pending_auth_token: Optional[str] = None
        self._pending_auth_type: Optional[str] = None
        self._session_expires_at: Optional[int] = None
        self._session_store_path = self._resolve_session_store_path()
        if not self.session_token:
            stored_token, stored_expiry = self._load_persisted_session_token()
            if stored_token:
                # _load_persisted_session_token already checks expiry and clears expired tokens
                self.session_token = stored_token
                self._session_expires_at = stored_expiry
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

    def _get_action_recorder_diagnostics(self) -> Dict[str, Any]:
        """Get diagnostic information about why the action recorder might be disabled."""
        if not self._action_recorder:
            return {
                "recorder_exists": False,
                "reason": "action recorder not configured (recorder is None)",
                "missing_vars": [],
            }

        missing = []
        # Check if recorder is enabled
        if not self._action_recorder.is_enabled:
            # Check what's missing by inspecting the recorder's internal state
            try:
                if hasattr(self._action_recorder, "_config"):
                    config = self._action_recorder._config
                    if not (getattr(config, "private_key", "") or "").strip():
                        missing.append("private_key")
                    if not (getattr(config, "rpc_url", "") or "").strip():
                        missing.append("rpc_url")
                    if not (getattr(config, "contract_address", "") or "").strip():
                        missing.append("contract_address")
            except Exception:
                pass  # If we can't access config, continue with other checks

            # Check internal state
            try:
                if (
                    hasattr(self._action_recorder, "_w3")
                    and self._action_recorder._w3 is None
                ):
                    missing.append("Web3 provider not initialized")
                if (
                    hasattr(self._action_recorder, "_contract")
                    and self._action_recorder._contract is None
                ):
                    missing.append("contract not initialized")
                if (
                    hasattr(self._action_recorder, "_account")
                    and self._action_recorder._account is None
                ):
                    missing.append("account not initialized")
            except Exception:
                pass  # If we can't access internal state, continue

            # Also check public properties
            if not self._action_recorder.rpc_url:
                if "rpc_url" not in missing:
                    missing.append("rpc_url")
            if not self._action_recorder.contract_address:
                if "contract_address" not in missing:
                    missing.append("contract_address")

            reason = (
                f"action recorder disabled: missing or invalid {', '.join(missing)}"
                if missing
                else "action recorder disabled (unknown reason)"
            )
        else:
            reason = None

        return {
            "recorder_exists": True,
            "recorder_enabled": self._action_recorder.is_enabled,
            "missing_vars": missing if not self._action_recorder.is_enabled else [],
            "reason": reason,
            "account_address": self._action_recorder.account_address,
            "contract_address": self._action_recorder.contract_address,
            "rpc_url": self._action_recorder.rpc_url,
        }

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
                f"Already have {REQUIRED_ACTIONS_PER_EPOCH}+ verified on-chain txs (staking threshold met); "
                "skipping on-chain recording for %s",
                action_type,
            )
            return
        if not self._action_recorder:
            logger.info(
                "🧾 Skipping on-chain record for %s: action recorder not configured (recorder is None)",
                action_type,
            )
            return
        if not self._action_recorder.is_enabled:
            diag = self._get_action_recorder_diagnostics()
            reason = diag.get("reason", "action recorder disabled (unknown reason)")
            missing_vars = diag.get("missing_vars", [])
            logger.info(
                "🧾 Skipping on-chain record for %s: %s (missing variables: %s)",
                action_type,
                reason,
                ", ".join(missing_vars) if missing_vars else "none identified",
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
                f"⏭️ Already have {REQUIRED_ACTIONS_PER_EPOCH}+ verified on-chain txs (staking threshold met); "
                "skipping on-chain recording for %s",
                action_type,
            )
            return

        # Proceed with recording
        if not self._action_recorder:
            logger.info(
                "🧾 Skipping on-chain record for %s: action recorder not configured (recorder is None)",
                action_type,
            )
            return
        if not self._action_recorder.is_enabled:
            diag = self._get_action_recorder_diagnostics()
            reason = diag.get("reason", "action recorder disabled (unknown reason)")
            missing_vars = diag.get("missing_vars", [])
            logger.info(
                "🧾 Skipping on-chain record for %s: %s (missing variables: %s)",
                action_type,
                reason,
                ", ".join(missing_vars) if missing_vars else "none identified",
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
        """Build SSL context honoring CA overrides."""
        if not self.websocket_url or not self.websocket_url.startswith("wss://"):
            return None

        ca_file = (os.getenv("WEBSOCKET_CA_FILE") or "").strip()
        ca_path = (os.getenv("WEBSOCKET_CA_PATH") or "").strip()

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

    def set_session_token(
        self, session_token: str, *, expires_at: Optional[int] = None
    ) -> None:
        """Update the stored session token without reconnecting."""
        token = (session_token or "").strip()
        if not token:
            logger.warning(
                "⚠️ Attempted to set an empty session token - session auth will be disabled"
            )
            self.clear_session_token()
            return
        normalized_expiry = self._normalize_session_expiry(expires_at)
        # Check if the token is already expired before setting it
        if self._is_session_expired(normalized_expiry):
            logger.warning(
                "⚠️ Attempted to set an expired session token - session auth will be disabled"
            )
            self.clear_session_token()
            return
        self.session_token = token
        self._session_expires_at = normalized_expiry
        self._last_auth_error = None
        self._persist_session_token()

    def clear_session_token(self) -> None:
        """Clear the stored session token and expiry info."""
        self.session_token = ""
        self._session_expires_at = None
        if self._saved_auth_type == "session":
            self._saved_auth_token = None
            self._saved_auth_type = None
        self._delete_persisted_session_token()
        logger.info("Session token cleared")

    def clear_saved_auth_token(self) -> None:
        """Clear saved auth token and previous authentication state."""
        self._saved_auth_token = None
        self._saved_auth_type = None
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

    def _strip_bearer_prefix(self, token: str) -> str:
        """Return the token without a leading Bearer prefix."""
        trimmed = (token or "").strip()
        if trimmed.lower().startswith("bearer "):
            return trimmed[7:].strip()
        return trimmed

    def _infer_auth_type(self, token: str) -> Optional[str]:
        """Infer auth type from token format when possible."""
        trimmed = self._strip_bearer_prefix(token)
        if trimmed.lower().startswith("psess_"):
            return "session"
        return None

    def _normalize_session_expiry(self, expires_at: Optional[int]) -> Optional[int]:
        """Normalize an expiry timestamp to epoch milliseconds."""
        if expires_at is None:
            return None
        try:
            expiry = int(expires_at)
        except (TypeError, ValueError):
            return None
        if expiry < 10**12:
            return expiry * 1000
        return expiry

    def _is_session_expired(self, expires_at: Optional[int]) -> bool:
        """Check if a session token has expired based on its expiry timestamp."""
        if expires_at is None:
            return False  # No expiry info means we can't determine if expired
        try:
            # Convert expiry (milliseconds) to seconds for comparison
            expiry_seconds = expires_at / 1000.0
            current_seconds = time.time()
            return current_seconds >= expiry_seconds
        except (TypeError, ValueError):
            # Invalid expiry format - treat as expired to be safe
            return True

    def _resolve_session_store_path(self) -> Path:
        env_candidates = (
            "CONNECTION_CONFIGS_CONFIG_STORE_PATH",
            "CONNECTION_CONFIGS_STORE_PATH",
            "STORE_PATH",
        )
        for env_name in env_candidates:
            value = os.getenv(env_name)
            if value and value.strip():
                return Path(value).expanduser() / "pett_session_token.json"
        return Path("./persistent_data") / "pett_session_token.json"

    def _get_encryption_key(self) -> Optional[bytes]:
        """
        Derive encryption key from password.

        Uses PBKDF2 to derive a key from the provided password (similar to how
        the ethereum private key is encrypted). If no password is provided,
        returns None and tokens will be stored in plaintext.

        Returns:
            32-byte encryption key suitable for Fernet, or None if no password
        """
        if not self._encryption_password:
            return None

        # Derive key using PBKDF2 (same approach as eth keystore)
        derived_key = hashlib.pbkdf2_hmac(
            "sha256",
            self._encryption_password.encode("utf-8"),
            b"pett-session-encryption-salt",
            iterations=100000,
            dklen=32,
        )

        # Fernet requires base64-encoded key
        return base64.urlsafe_b64encode(derived_key)

    def _encrypt_token(self, token: str) -> Optional[str]:
        """
        Encrypt a token using Fernet symmetric encryption with password.

        Args:
            token: Plaintext token to encrypt

        Returns:
            Base64-encoded encrypted token, or None if no password available
        """
        try:
            key = self._get_encryption_key()
            if key is None:
                logger.warning(
                    "No encryption password provided - session token will be stored in plaintext. "
                    "Set SESSION_TOKEN_PASSWORD env var or pass encryption_password parameter."
                )
                return None

            fernet = Fernet(key)
            encrypted_bytes = fernet.encrypt(token.encode("utf-8"))
            return base64.b64encode(encrypted_bytes).decode("utf-8")
        except Exception as exc:
            logger.error("Failed to encrypt token: %s", exc)
            raise

    def _decrypt_token(self, encrypted_token: str) -> Optional[str]:
        """
        Decrypt an encrypted token with password.

        Args:
            encrypted_token: Base64-encoded encrypted token

        Returns:
            Decrypted plaintext token, or None if no password/decryption fails

        Raises:
            InvalidToken: If decryption fails with wrong password
        """
        try:
            key = self._get_encryption_key()
            if key is None:
                logger.error(
                    "Cannot decrypt session token: no encryption password provided. "
                    "Set SESSION_TOKEN_PASSWORD env var or pass encryption_password parameter."
                )
                return None

            fernet = Fernet(key)
            encrypted_bytes = base64.b64decode(encrypted_token.encode("utf-8"))
            decrypted_bytes = fernet.decrypt(encrypted_bytes)
            return decrypted_bytes.decode("utf-8")
        except InvalidToken:
            logger.error("Failed to decrypt token: wrong password or corrupted data")
            raise
        except Exception as exc:
            logger.error("Failed to decrypt token: %s", exc)
            raise

    def _load_persisted_session_token(self) -> Tuple[str, Optional[int]]:
        path = self._session_store_path
        if not path.exists():
            return "", None
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                return "", None

            encrypted_token = data.get("encryptedSessionToken")
            token = None

            # Try to load encrypted token first (new format)
            if encrypted_token and isinstance(encrypted_token, str):
                try:
                    decrypted = self._decrypt_token(encrypted_token)
                    if decrypted:
                        token = decrypted
                        logger.debug("Successfully loaded encrypted session token")
                    else:
                        logger.warning(
                            "Cannot decrypt session token: no password provided. "
                            "Falling back to plaintext if available."
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to decrypt session token (wrong password?): %s", exc
                    )
                    # Fall through to try legacy plaintext format

            # Fall back to plaintext token (legacy format) if decryption failed or no encrypted token
            if not token:
                token = data.get("sessionToken") or data.get("token")
                if token and isinstance(token, str):
                    if self._encryption_password:
                        logger.info(
                            "Loaded plaintext session token. "
                            "It will be re-saved in encrypted format on next update."
                        )
                    else:
                        logger.warning(
                            "⚠️  Loaded plaintext session token. "
                            "Set SESSION_TOKEN_PASSWORD to encrypt it."
                        )

            if not token or not isinstance(token, str):
                return "", None

            expires_at = self._normalize_session_expiry(data.get("sessionExpiresAt"))
            # Check if the token has expired and clear it if so
            if self._is_session_expired(expires_at):
                logger.info("Session token expired, clearing persisted token")
                self._delete_persisted_session_token()
                return "", None
            return token.strip(), expires_at
        except Exception as exc:
            logger.warning("Failed to load persisted session token: %s", exc)
            return "", None

    def _persist_session_token(self) -> None:
        token = (self.session_token or "").strip()
        if not token:
            return
        path = self._session_store_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            # Try to encrypt the token if password is available
            encrypted_token = self._encrypt_token(token)

            if encrypted_token:
                # Save encrypted token
                payload: Dict[str, Any] = {"encryptedSessionToken": encrypted_token}
            else:
                # No password - store in plaintext with warning
                logger.warning(
                    "⚠️  SESSION TOKEN STORED IN PLAINTEXT - "
                    "No encryption password provided! "
                    "Set SESSION_TOKEN_PASSWORD environment variable."
                )
                payload_plaintext: Dict[str, Any] = {"sessionToken": token}
                payload = payload_plaintext
            if self._session_expires_at:
                payload["sessionExpiresAt"] = self._session_expires_at

            # Write to a temporary file first, then atomically rename
            temp_path = path.with_suffix(".tmp")
            try:
                with temp_path.open("w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2, sort_keys=True)

                # Set restrictive permissions before moving the file
                if platform.system() != "Windows":
                    os.chmod(temp_path, 0o600)
                    # Verify permissions were actually set
                    file_stat = os.stat(temp_path)
                    current_mode = stat.filemode(file_stat.st_mode)
                    if current_mode != "-rw-------":
                        logger.error(
                            "Failed to set restrictive permissions on session token file: %s (expected -rw-------)",
                            current_mode,
                        )
                        # Delete the file and abort - don't leave improperly secured tokens
                        temp_path.unlink()
                        raise PermissionError(
                            f"Could not set restrictive permissions (got {current_mode})"
                        )
                else:
                    # On Windows, use platform-specific ACLs for security
                    try:
                        import win32security
                        import ntsecuritycon as con
                        import win32api

                        # Get current user
                        user, domain, _ = win32security.LookupAccountName(
                            "", win32api.GetUserName()
                        )

                        # Create a new security descriptor
                        sd = win32security.SECURITY_DESCRIPTOR()
                        sd.Initialize()

                        # Create a new DACL (Discretionary Access Control List)
                        dacl = win32security.ACL()
                        dacl.Initialize()

                        # Add ACE (Access Control Entry) for the owner with full control
                        dacl.AddAccessAllowedAce(
                            win32security.ACL_REVISION,
                            con.FILE_ALL_ACCESS,
                            user,
                        )

                        # Set the DACL to the security descriptor
                        sd.SetSecurityDescriptorDacl(1, dacl, 0)

                        # Apply security descriptor to the file
                        win32security.SetFileSecurity(
                            str(temp_path),
                            win32security.DACL_SECURITY_INFORMATION,
                            sd,
                        )
                        logger.debug("Set Windows ACL on session token file")
                    except ImportError:
                        logger.warning(
                            "pywin32 not available - cannot set Windows file ACLs. "
                            "Session token file may not be properly secured."
                        )
                    except Exception as win_exc:
                        logger.warning(
                            "Failed to set Windows ACLs on session token file: %s",
                            win_exc,
                        )

                # Atomically replace the old file
                temp_path.replace(path)
                logger.debug("Successfully persisted encrypted session token")

            finally:
                # Clean up temp file if it still exists
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except Exception:
                        pass

        except Exception as exc:
            logger.error("Failed to persist session token: %s", exc)
            raise

    def _delete_persisted_session_token(self) -> None:
        path = self._session_store_path
        try:
            if path.exists():
                path.unlink()
        except Exception as exc:
            logger.warning("Failed to delete persisted session token: %s", exc)

    def _has_any_auth_token(self) -> bool:
        """Check if any auth token is available for reconnect/auth."""
        return bool(self._saved_auth_token or self.session_token or self.privy_token)

    def _get_auth_candidates(self) -> List[Tuple[str, str, str]]:
        """Return ordered auth candidates as (auth_type, token, label)."""
        candidates: List[Tuple[str, str, str]] = []

        def add_candidate(auth_type: str, token: str, label: str) -> None:
            cleaned = (token or "").strip()
            if not cleaned:
                return
            for existing_type, existing_token, _ in candidates:
                if existing_type == auth_type and existing_token == cleaned:
                    return
            candidates.append((auth_type, cleaned, label))

        # Check and clear expired session token before building candidates
        if self.session_token and self._is_session_expired(self._session_expires_at):
            logger.info("Session token expired, clearing it")
            self.clear_session_token()

        saved_type = (self._saved_auth_type or "").strip().lower()
        if self._saved_auth_token and saved_type == "session":
            # Note: We don't have expiry info for saved tokens, but they'll fail on auth if expired
            add_candidate("session", self._saved_auth_token, "saved")

        if self.session_token:
            add_candidate("session", self.session_token, "session")

        # Always include Privy tokens as fallback, even when session tokens exist.
        # This allows recovery from expired/revoked session tokens.
        if self._saved_auth_token and saved_type == "privy":
            add_candidate("privy", self._saved_auth_token, "saved")
        elif self._saved_auth_token and not saved_type:
            add_candidate("privy", self._saved_auth_token, "saved-legacy")

        if self.privy_token:
            add_candidate("privy", self.privy_token, "privy")

        # Log available candidates for debugging
        if candidates:
            candidate_info = [f"{label}({auth_type})" for auth_type, _, label in candidates]
            logger.info(f"🔑 Available auth candidates (priority order): {', '.join(candidate_info)}")
        else:
            logger.warning("⚠️  No auth candidates available")

        return candidates

    def _is_jwt_expired_error(self, error_text: str) -> bool:
        """Check if the error string indicates a Privy JWT expiration."""
        lowered = (error_text or "").lower()
        return any(
            keyword in lowered
            for keyword in (
                "exp",
                "jwt_expired",
                "timestamp check failed",
                "jwt",
                "token expired",
                "expired jwt",
            )
        )

    def _is_session_token_invalid(self, error_text: str) -> bool:
        """Check if the error string indicates an invalid/expired session token.

        Uses flexible pattern matching to detect various authentication failure
        messages that may indicate session token issues, without requiring
        specific substring matches. This improves resilience to backend message changes.
        """
        if not error_text:
            return False

        lowered = error_text.lower()

        # Direct session token error indicators
        session_indicators = (
            "session token",
            "session_token",
            "session invalid",
            "session expired",
            "session authentication",
        )

        # Generic authentication failure indicators (when using session auth)
        auth_failure_indicators = (
            "unauthorized",
            "authentication failed",
            "auth failed",
            "invalid token",
            "token invalid",
            "token expired",
            "expired token",
            "jwt expired",
            "expired jwt",
            "invalid authentication",
            "authentication error",
            "401",  # HTTP 401 Unauthorized
        )

        # Check for session-specific errors
        has_session_indicator = any(
            indicator in lowered for indicator in session_indicators
        )
        if has_session_indicator:
            # If session-related, check for failure keywords
            failure_keywords = ("invalid", "expired", "failed", "error", "unauthorized")
            return any(keyword in lowered for keyword in failure_keywords)

        # Check for generic auth failures (when we know we're using session auth)
        # This catches cases like "Unauthorized" or "Invalid token" without "session" in the message
        has_auth_failure = any(
            indicator in lowered for indicator in auth_failure_indicators
        )
        if has_auth_failure:
            # Additional validation: exclude non-auth errors that might match
            excluded_patterns = (
                "rate limit",  # Rate limiting is not an auth failure
                "too many requests",  # Rate limiting
                "permission denied",  # Different from auth failure
            )
            # Only consider it a session error if it's not an excluded pattern
            return not any(excluded in lowered for excluded in excluded_patterns)

        return False

    async def authenticate(self, timeout: int = 10) -> bool:
        """Default authentication using available tokens with timeout.

        Tries session tokens first if available, then falls back to Privy tokens.
        This ensures authentication can succeed even if session tokens are expired/invalid.

        Returns:
            True if authentication succeeded with any available token, False otherwise.
        """
        candidates = self._get_auth_candidates()
        if not candidates:
            logger.warning("⚠️ No auth token available for authentication")
            return False

        for auth_type, token, label in candidates:
            if auth_type == "session":
                logger.info("🔐 Authenticating with session token (%s)", label)
                success = await self.authenticate_session(token, timeout)
            else:
                logger.info("🔐 Authenticating with privy token (%s)", label)
                success = await self.authenticate_privy(token, timeout)
            if success:
                return True

        return False

    async def authenticate_session(
        self, session_auth_token: str, timeout: int = 10
    ) -> bool:
        """Authenticate using session token with timeout and result waiting."""
        token = self._strip_bearer_prefix(session_auth_token)
        if not token:
            logger.error("Invalid session auth token provided")
            return False

        auth_hash = {"token": token}
        return await self._authenticate("session", auth_hash, token, timeout)

    async def authenticate_privy(
        self, privy_auth_token: str, timeout: int = 10
    ) -> bool:
        """Authenticate using Privy credentials with timeout and result waiting."""
        token = self._strip_bearer_prefix(privy_auth_token)
        if not token:
            logger.error("Invalid Privy auth token provided")
            return False

        auth_hash = {"hash": "Bearer " + token}
        return await self._authenticate("privy", auth_hash, token, timeout)

    async def _authenticate(
        self,
        auth_type: str,
        auth_hash: Dict[str, Any],
        token: str,
        timeout: int = 10,
    ) -> bool:
        """Send an AUTH request with the provided auth payload and wait for response."""
        try:
            # Create a future to wait for the auth result
            auth_future: asyncio.Future[bool] = asyncio.Future()

            # Store the future so we can resolve it in the message handler
            self.auth_future = auth_future
            self._pending_auth_token = token
            self._pending_auth_type = auth_type

            auth_message = {
                "type": "AUTH",
                "data": {
                    "params": {
                        "authHash": auth_hash,
                        "authType": auth_type,
                    }
                },
            }

            # Log the authentication attempt with detailed info
            logger.info(f"📤 Sending AUTH message with authType='{auth_type}' to server")

            # Send the auth message
            success = await self._send_message(auth_message)
            if not success:
                logger.error(f"❌ Failed to send AUTH message with authType='{auth_type}'")
                self._pending_auth_token = None
                self._pending_auth_type = None
                return False

            logger.debug(f"⏳ AUTH message sent (type='{auth_type}'), waiting for response...")

            # Wait for the auth result with timeout
            try:
                auth_result = await asyncio.wait_for(auth_future, timeout=timeout)
                logger.info(f"✅ AUTH response received (type='{auth_type}'): success={auth_result}")
                return auth_result
            except asyncio.TimeoutError:
                # Timeout on single attempt is not critical - caller will handle retries
                logger.debug(
                    f"⏱️ Authentication response not received within {timeout}s"
                )
                return False

        except Exception as e:
            logger.error(f"❌ Error during authentication: {e}")
            self._pending_auth_token = None
            self._pending_auth_type = None
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
        token = self._strip_bearer_prefix(privy_auth_token)

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
        """Connect to WebSocket and authenticate using available tokens with retry logic."""
        if not self._has_any_auth_token():
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
                candidates = self._get_auth_candidates()
                if not candidates:
                    logger.warning(
                        "⚠️ No auth token available (env or saved) - skipping authentication"
                    )
                    await self.disconnect()
                    return False

                for auth_type, token, label in candidates:
                    if auth_type == "session":
                        logger.info("🔐 Attempting session auth (%s)...", label)
                        auth_success = await self.authenticate_session(
                            token, timeout=auth_timeout
                        )
                    else:
                        logger.info("🔐 Attempting privy auth (%s)...", label)
                        auth_success = await self.authenticate_privy(
                            token, timeout=auth_timeout
                        )

                    if auth_success:
                        logger.info("✅ Connection and authentication successful!")
                        return True

                    error_text = (self._last_auth_error or "").lower()

                    if (
                        self._was_previously_authenticated
                        and token == self._saved_auth_token
                    ):
                        logger.warning(
                            "🔑 Saved auth token failed, clearing saved state"
                        )
                        self.clear_saved_auth_token()

                    if auth_type == "session" and self._is_session_token_invalid(
                        error_text
                    ):
                        logger.warning(
                            "🔑 Session token invalid or expired; please re-login via the UI Privy flow to mint a new session token"
                        )

                    if auth_type == "privy":
                        if self._is_jwt_expired_error(error_text):
                            self._jwt_expired = True
                            logger.critical(
                                "💀 JWT (Privy) token expired. Please re-login via the UI to get a new token — a new token is only sent when you log in again."
                            )
                            return False
                        if "user not found" in error_text:
                            logger.info(
                                "🛑 Stopping auth retries due to missing user; caller should register"
                            )
                            return False

                    if not self.connection_established:
                        break

                # All candidates failed for this attempt
                if attempt >= 3:
                    logger.warning(
                        f"❌ Authentication attempt {attempt + 1}/{max_retries} failed"
                    )
                else:
                    logger.info(
                        f"🔄 Authentication attempt {attempt + 1}/{max_retries} - retrying..."
                    )

                await self.disconnect()

                if attempt < max_retries - 1:
                    await asyncio.sleep(2**attempt)  # Exponential backoff
                    continue
                return False

            except Exception as e:
                logger.error(f"❌ Error in connection attempt {attempt + 1}: {e}")
                await self.disconnect()
                if attempt < max_retries - 1:
                    await asyncio.sleep(2**attempt)  # Exponential backoff
                    continue
                return False

        return False

    async def _ensure_connected(self, skip_lock_check: bool = False) -> bool:
        """Ensure WebSocket is connected and authenticated, reconnecting if needed.

        Args:
            skip_lock_check: If True, skip the lock check (used internally to avoid deadlock)
        """
        # Check if already reconnecting (outside lock to avoid blocking)
        if self._reconnecting and not skip_lock_check:
            # Wait a bit and check if connection is now established
            for _ in range(10):  # Wait up to 5 seconds
                await asyncio.sleep(0.5)
                if self.connection_established and self.authenticated:
                    return True
            return False

        # Quick check: if already connected and authenticated, return True
        if self.connection_established and self.authenticated:
            return True

        # Acquire lock only if we're not already inside a reconnection
        if not skip_lock_check:
            if self._reconnect_lock.locked():
                # Lock is held, wait for it to complete
                for _ in range(10):
                    await asyncio.sleep(0.5)
                    if self.connection_established and self.authenticated:
                        return True
                return False

        async with self._reconnect_lock:
            # Double-check after acquiring lock
            if self.connection_established and self.authenticated:
                return True

            if self._reconnecting:
                # Another coroutine is already reconnecting
                for _ in range(10):
                    await asyncio.sleep(0.5)
                    if self.connection_established and self.authenticated:
                        return True
                return False

            self._reconnecting = True
            try:
                logger.info("🔄 Ensuring WebSocket connection is active...")

                if not self._has_any_auth_token():
                    logger.warning("No auth token available for reconnection")
                    return False

                # Disconnect if there's a stale connection
                if self.websocket:
                    try:
                        # Temporarily mark as disconnected to avoid recursion
                        old_connected = self.connection_established
                        self.connection_established = False
                        await self.disconnect()
                    except Exception:
                        pass

                # Reconnect and authenticate (this may call _send_message, but we're protected by the lock)
                # Set connection_established to False before reconnecting to prevent recursion
                self.connection_established = False
                self.authenticated = False

                result = await self.connect_and_authenticate(
                    max_retries=3, auth_timeout=10
                )
                if result:
                    logger.info("✅ Successfully reconnected and re-authenticated")
                    return True
                else:
                    logger.warning("❌ Failed to reconnect and re-authenticate")
                    return False
            finally:
                self._reconnecting = False

    async def auth_ping(self, token: Optional[str] = None, timeout: int = 10) -> bool:
        """Send a lightweight AUTH to refresh pet data without restarting the client."""
        auth_type = None
        token_source = None

        if token:
            auth_token = token.strip()
            auth_type = self._infer_auth_type(auth_token)
            if not auth_type:
                if auth_token == self.session_token or (
                    auth_token == self._saved_auth_token
                    and self._saved_auth_type == "session"
                ):
                    auth_type = "session"
                else:
                    auth_type = "privy"
            token_source = "explicitly_provided"
            logger.info(f"🔐 auth_ping: Using {token_source} token of type '{auth_type}'")
        else:
            candidates = self._get_auth_candidates()
            if not candidates:
                logger.warning("auth_ping skipped: no auth token available")
                return False
            auth_type, auth_token, token_label = candidates[0]
            token_source = f"auto_selected_{token_label}"
            logger.info(f"🔐 auth_ping: Using {token_source} token of type '{auth_type}' (selected from {len(candidates)} candidates)")

        auth_token = (auth_token or "").strip()
        if not auth_token:
            logger.warning("auth_ping skipped: no auth token available")
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
                if auth_type == "session":
                    logger.info(f"➡️  auth_ping: Calling authenticate_session() with {token_source}")
                    return await self.authenticate_session(auth_token, timeout=timeout)
                logger.info(f"➡️  auth_ping: Calling authenticate_privy() with {token_source}")
                return await self.authenticate_privy(auth_token, timeout=timeout)
            except Exception as exc:
                logger.error("auth_ping error: %s", exc)
                return False

    async def _send_message(self, message: Dict[str, Any]) -> bool:
        """Send a message to the WebSocket server."""
        if not self.websocket or not self.connection_established:
            # If we're already reconnecting, wait for it to complete instead of starting a new one
            if self._reconnecting:
                logger.debug(
                    "Connection in progress, waiting for reconnection to complete..."
                )
                for _ in range(20):  # Wait up to 10 seconds
                    await asyncio.sleep(0.5)
                    if self.connection_established and self.authenticated:
                        # Connection is now established, proceed with sending
                        break
                else:
                    # Still not connected after waiting
                    logger.error(
                        "WebSocket still not connected after waiting for reconnection"
                    )
                    if self._telemetry_recorder:
                        try:
                            self._telemetry_recorder(
                                message,
                                False,
                                "WebSocket not connected after reconnection wait",
                            )
                        except Exception:
                            pass
                    return False
            else:
                logger.error("WebSocket not connected")
                # Try to reconnect if we have a token
                if self._has_any_auth_token():
                    logger.info("🔄 Attempting to reconnect before sending message...")
                    reconnected = await self._ensure_connected()
                    if not reconnected:
                        if self._telemetry_recorder:
                            try:
                                self._telemetry_recorder(
                                    message, False, "WebSocket not connected"
                                )
                            except Exception:
                                pass
                        return False
                else:
                    if self._telemetry_recorder:
                        try:
                            self._telemetry_recorder(
                                message, False, "WebSocket not connected"
                            )
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
        except (
            websockets.exceptions.ConnectionClosed,
            websockets.exceptions.InvalidState,
        ) as e:
            error_str = str(e)
            logger.error(f"WebSocket connection error: {e}")
            # Mark connection as dead
            self.connection_established = False
            self.authenticated = False

            # Check if it's a keepalive timeout (1011) or connection closed
            if (
                "1011" in error_str
                or "keepalive" in error_str.lower()
                or "ping timeout" in error_str.lower()
            ):
                logger.warning(
                    "🔄 Keepalive timeout detected - connection appears dead, will reconnect"
                )

            # Try to reconnect if we have a token and we're not already reconnecting
            if self._has_any_auth_token() and not self._reconnecting:
                logger.info("🔄 Attempting to reconnect after connection error...")
                reconnected = await self._ensure_connected()
                if reconnected:
                    # Retry sending the message after reconnection
                    try:
                        if "nonce" not in message:
                            message["nonce"] = self._generate_nonce()
                        message_json = json.dumps(message)
                        await self.websocket.send(message_json)
                        logger.info(
                            f"📤 Sent message type: {message['type']} after reconnection"
                        )
                        if message.get("type") != "AUTH":
                            logger.info(f"📤 Message content: {message_json}")
                        if self._telemetry_recorder:
                            try:
                                self._telemetry_recorder(message, True, None)
                            except Exception:
                                pass
                        return True
                    except Exception as retry_e:
                        logger.error(
                            f"Failed to send message after reconnection: {retry_e}"
                        )
            elif self._reconnecting:
                logger.debug(
                    "Reconnection already in progress, skipping duplicate attempt"
                )

            if self._telemetry_recorder:
                try:
                    self._telemetry_recorder(message, False, str(e))
                except Exception:
                    pass
            return False
        except Exception as e:
            error_str = str(e)
            logger.error(f"Failed to send message: {e}")
            # Check for connection-related errors in the exception message
            if (
                "1011" in error_str
                or "keepalive" in error_str.lower()
                or "ping timeout" in error_str.lower()
                or "connection" in error_str.lower()
            ):
                # Mark connection as dead
                self.connection_established = False
                self.authenticated = False

                # Try to reconnect if we have a token and we're not already reconnecting
                if self._has_any_auth_token() and not self._reconnecting:
                    logger.info(
                        "🔄 Connection error detected, attempting to reconnect..."
                    )
                    await self._ensure_connected()
                elif self._reconnecting:
                    logger.debug(
                        "Reconnection already in progress, skipping duplicate attempt"
                    )

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

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(
                f"⚠️ WebSocket connection closed during message listening: {e}"
            )
            self.connection_established = False
            self.authenticated = False
            # Try to reconnect automatically if we have a token
            if self._has_any_auth_token():
                logger.info(
                    "🔄 Connection closed in listener, will attempt reconnection on next message"
                )
        except Exception as e:
            error_str = str(e)
            logger.error(f"❌ Error in WebSocket message listener: {e}")
            self.connection_established = False
            self.authenticated = False
            # Check if it's a connection-related error
            if (
                "1011" in error_str
                or "keepalive" in error_str.lower()
                or "ping timeout" in error_str.lower()
            ):
                logger.warning(
                    "⚠️ Keepalive timeout in listener - connection appears dead"
                )
                if self._has_any_auth_token():
                    logger.info("🔄 Will attempt reconnection on next message")

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
            session_token = data.get("sessionToken")
            session_expires_at = data.get("sessionExpiresAt")
        else:
            # Direct structure: {'type': 'auth_result', 'success': False, 'error': '...'}
            success = message.get("success", False)
            error = message.get("error", "Unknown error")
            user_data = message.get("user", {})
            pet_data = message.get("pet", {})
            session_token = message.get("sessionToken")
            session_expires_at = message.get("sessionExpiresAt")

        if success:
            # Log which token type succeeded
            success_token_type = self._pending_auth_type or "unknown"
            logger.info(f"✅ Authentication succeeded with token type '{success_token_type}'")

            self.authenticated = True
            # Reset JWT expiration flag on successful auth
            self._jwt_expired = False
            self._last_auth_error = None  # Clear any previous errors
            # Save auth token for reconnection use
            if session_token:
                session_token_str = str(session_token).strip()
                self.set_session_token(session_token_str, expires_at=session_expires_at)
                self._saved_auth_token = self.session_token
                self._saved_auth_type = "session"
            elif self._pending_auth_token and self._pending_auth_type:
                self._saved_auth_token = self._pending_auth_token
                self._saved_auth_type = self._pending_auth_type
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
            # Log which token type failed
            failed_token_type = self._pending_auth_type or "unknown"
            logger.error(f"❌ Authentication failed with token type '{failed_token_type}': {error}")
            self.authenticated = False

            # Store the error for retry logic
            self._last_auth_error = str(error)

            # Clear saved auth token on authentication failure
            if self._was_previously_authenticated:
                logger.info(
                    "🔑 Clearing saved auth token due to authentication failure"
                )
                self.clear_saved_auth_token()

            error_text = str(error or "")
            if self._pending_auth_type == "privy" and self._is_jwt_expired_error(
                error_text
            ):
                self._jwt_expired = True
                logger.error(
                    "🔑 JWT (Privy) token has expired. Please re-login via the UI to get a new token."
                )
                logger.error(
                    "💡 Re-login via the Privy flow in the UI to obtain a new session; the agent will use it on next /api/login."
                )
                logger.critical(
                    "💀 JWT (Privy) token expired. Please re-login via the UI — do not wait for refresh; a new token is only sent when you log in again."
                )
            elif (
                self._pending_auth_type == "session"
                and self._is_session_token_invalid(error_text)
            ):
                logger.warning(
                    "🔑 Session token is invalid or expired. Clearing it to allow fallback to Privy authentication."
                )
                # Clear the invalid session token to allow Privy fallback in next auth attempt
                self.clear_session_token()
                logger.info(
                    "💡 Session token cleared. Next authentication attempt will use Privy token if available."
                )

        # Resolve the auth future if it exists
        if self.auth_future and not self.auth_future.done():
            self.auth_future.set_result(success)

        self._pending_auth_token = None
        self._pending_auth_type = None

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
                old_id = self.pet_data.get("id")
                new_id = pet_data.get("id")
                old_dead = self.pet_data.get("dead", False)
                new_dead = pet_data.get("dead", False)

                # If pet id changes, prefer new payload entirely (new pet)
                if old_id and new_id and old_id != new_id:
                    self.pet_data = pet_data
                    logger.info(
                        f"Pet Status updated (new pet ID: {old_id} -> {new_id})"
                    )
                else:
                    # Same pet ID - merge the data (this handles pet resets where ID stays same)
                    merged = self._merge_pet_data(self.pet_data, pet_data)
                    self.pet_data = merged

                    # Explicitly ensure dead status is updated from the new data
                    # This is important for pet resets where dead status changes from true to false
                    if "dead" in pet_data:
                        self.pet_data["dead"] = pet_data["dead"]
                        # Update new_dead to reflect the actual merged value
                        new_dead = self.pet_data.get("dead", False)

                    # Log dead status transitions for same pet (including resets)
                    if old_dead and not new_dead:
                        logger.info(
                            f"✨ Pet revived/reset! Dead status cleared: {old_dead} -> {new_dead} "
                            f"(Pet ID: {old_id or new_id})"
                        )
                    elif not old_dead and new_dead:
                        logger.warning(
                            f"💀 Pet died! Dead status changed: {old_dead} -> {new_dead} "
                            f"(Pet ID: {old_id or new_id})"
                        )
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

2. **For Session Authentication (Recommended):**
   - Set PETT_SESSION_TOKEN to your session token (e.g. psess_...)
   - Request a new session token from your backend if it was revoked or expired

3. **Common Token Sources:**
   - Privy Dashboard -> Access Tokens
   - Your authentication provider's token endpoint
   - Mobile app authentication flow

4. **Environment Variable:**
   - Update PRIVY_TOKEN in your .env file
   - Restart the agent after updating the token

5. **Token Format:**
   - Ensure the token is valid and not expired
   - Remove any "Bearer " prefix if present
   - The token should be the raw JWT string
"""
