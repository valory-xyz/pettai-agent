"""
Staking checkpoint management for the Pett agent.

This module encapsulates the interaction with the staking proxy contract in
order to call the `checkpoint` function whenever the liveness period has
elapsed since the last checkpoint execution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, cast

from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ContractLogicError
from web3.middleware import ExtraDataToPOAMiddleware
from web3.types import TxParams

from .gas_limits import MAX_TRANSACTION_GAS
from .nonce_utils import get_shared_nonce_lock

DEFAULT_SAFE_ADDRESS = "0xdf5bae4216Dc278313712291c91D2DeAF2Cc9c1c"
DEFAULT_STATE_FILE = Path("data/staking_checkpoint_state.json")
DEFAULT_LIVENESS_PERIOD = 86_400  # 24 hours
SUBMISSION_COOLDOWN_SECONDS = 600  # 10 minutes to avoid duplicate submissions

# Gas strategy constants aligned with Safe.execTransaction tuning
DEFAULT_PRIORITY_FEE_PER_GAS = Web3.to_wei(5, "mwei")  # 0.005 gwei
MIN_PRIORITY_FEE_PER_GAS = Web3.to_wei(1, "mwei")  # 0.001 gwei floor
MAX_PRIORITY_FEE_PER_GAS = Web3.to_wei(50, "mwei")  # 0.05 gwei cap
MIN_FEE_BUFFER_PER_GAS = Web3.to_wei(5, "mwei")  # 0.005 gwei headroom
MAX_FEE_BUFFER_PER_GAS = Web3.to_wei(50, "mwei")  # 0.05 gwei cap
PRIORITY_FEE_OVERRIDE_ENV = "CHECKPOINT_PRIORITY_FEE_WEI"

# Minimal ABI fragment for the staking proxy contract.
STAKING_PROXY_ABI: list[Dict[str, Any]] = [
    {
        "inputs": [],
        "name": "checkpoint",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "serviceId", "type": "uint256"}],
        "name": "checkpointAndClaim",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "tsCheckpoint",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "livenessPeriod",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


@dataclass
class CheckpointConfig:
    """Configuration required to interact with the staking contract."""

    private_key: str
    rpc_url: str
    staking_contract_address: str
    safe_address: str = DEFAULT_SAFE_ADDRESS
    liveness_period: Optional[int] = None
    state_file: Optional[Path] = None
    # When True, do not broadcast checkpoint txs; only log what would be sent
    dry_run: bool = False
    staking_token_address: Optional[str] = None


class StakingCheckpointClient:
    """Encapsulates the staking checkpoint interaction logic."""

    def __init__(
        self,
        config: CheckpointConfig,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._logger = logger or logging.getLogger("staking_checkpoint")
        self._config = config
        self._w3: Optional[Web3] = None
        self._staking_contract: Optional[Contract] = None
        self._account: Optional[LocalAccount] = None
        self._private_key: Optional[str] = None
        self._safe_address = self._normalise_address(config.safe_address)
        self._state_file = self._resolve_state_file(config.state_file)
        self._cached_liveness_period: Optional[int] = config.liveness_period
        self._warned_missing_liveness = False
        self._nonce_lock = threading.Lock()
        self._nonce_cache: Optional[int] = None
        self._call_lock = threading.Lock()
        self._last_known_checkpoint_ts: Optional[int] = None
        self._last_checked_at: Optional[int] = None
        self._last_submitted_at: Optional[int] = None
        self._last_tx_hash: Optional[str] = None
        self._dry_run: bool = bool(config.dry_run)
        self._kpi_disabled_logged = False

        self._load_state()
        self._initialise()

    @property
    def is_enabled(self) -> bool:
        """Return True when the client is ready to submit transactions."""
        return bool(self._w3 and self._staking_contract and self._account)

    async def call_checkpoint_if_needed(self, force: bool = False) -> Optional[str]:
        """
        Call checkpoint when liveness period elapsed.

        Returns the transaction hash (hex string) when a checkpoint transaction
        was submitted, None otherwise.
        """
        if not self.is_enabled:
            self._logger.info(
                "Skipping staking checkpoint invocation: client not enabled"
            )
            return None

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._call_checkpoint_if_needed_sync, force
        )

    async def get_epoch_kpis(self, force_refresh: bool = False) -> Optional[Any]:
        """Return staking KPI snapshot for the current epoch (always None in checkpoint-only mode)."""
        if not self.is_enabled:
            self._logger.debug("Staking checkpoint client is not enabled")
            return None
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._get_epoch_kpis_sync, force_refresh
        )

    async def get_next_epoch_end_timestamp(self) -> Optional[int]:
        """Return the timestamp when the current staking epoch ends."""
        if not self.is_enabled:
            return None
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._get_next_reward_checkpoint_timestamp
        )

    def _call_checkpoint_if_needed_sync(self, force: bool = False) -> Optional[str]:
        """Synchronous implementation invoked from the executor."""
        if not self.is_enabled:
            return None

        if not self._call_lock.acquire(blocking=False):
            self._logger.info(
                "Skipping staking checkpoint (force=%s): another call in progress",
                force,
            )
            return None

        try:
            assert self._staking_contract is not None
            assert self._w3 is not None

            last_onchain = self._get_last_checkpoint_on_chain()
            current_ts = self._get_current_block_timestamp()
            liveness = self._get_liveness_period()
            next_epoch_end = self._get_next_reward_checkpoint_timestamp()
            should_execute = force
            skip_reason: Optional[str] = None

            if not should_execute:
                if self._recent_submission_in_progress(current_ts):
                    skip_reason = "recent submission still pending"
                elif liveness is None:
                    should_execute = True
                else:
                    elapsed = current_ts - last_onchain
                    should_execute = elapsed > liveness
                    if not should_execute:
                        remaining = max(liveness - elapsed, 0)
                        skip_reason = (
                            f"liveness period not reached yet (remaining {remaining}s)"
                        )

            if (
                should_execute
                and next_epoch_end is not None
                and current_ts < next_epoch_end
            ):
                remaining = max(next_epoch_end - current_ts, 0)
                eta = datetime.fromtimestamp(next_epoch_end).isoformat()
                skip_reason = (
                    f"epoch end not reached yet (remaining {remaining}s, ETA {eta})"
                )
                should_execute = False

            self._record_state(last_onchain, current_ts, self._last_tx_hash)

            if not should_execute:
                reason = skip_reason or "execution conditions not met"
                self._logger.info(
                    "Skipping staking checkpoint (force=%s): %s", force, reason
                )
                return None

            tx_hash = self._submit_checkpoint_transaction(current_ts)
            if tx_hash:
                self._logger.info(
                    "Staking checkpoint transaction submitted (force=%s): %s",
                    force,
                    tx_hash,
                )
                self._record_state(
                    last_onchain, current_ts, tx_hash, submission_ts=current_ts
                )
            return tx_hash
        finally:
            self._call_lock.release()

    def _get_epoch_kpis_sync(self, force_refresh: bool = False) -> Optional[Any]:
        """Blocking implementation of KPI retrieval (disabled in checkpoint-only mode)."""
        if not self.is_enabled:
            return None

        if not self._kpi_disabled_logged:
            self._logger.debug(
                "Staking KPIs unavailable: service-specific telemetry disabled in checkpoint-only mode"
            )
            self._kpi_disabled_logged = True

        return None

    def _recent_submission_in_progress(self, current_ts: int) -> bool:
        """Return True if a recent submission is still within cooldown."""
        if self._last_submitted_at is None:
            return False
        cooldown = SUBMISSION_COOLDOWN_SECONDS
        liveness = self._get_liveness_period()
        if liveness is not None and liveness > 0:
            cooldown = min(SUBMISSION_COOLDOWN_SECONDS, max(liveness // 2, 30))
        return (current_ts - self._last_submitted_at) < cooldown

    def _get_last_checkpoint_on_chain(self) -> int:
        """Fetch the last checkpoint timestamp from the contract."""
        assert self._staking_contract is not None
        try:
            value = self._staking_contract.functions.tsCheckpoint().call()
            last_ts = int(value or 0)
            self._last_known_checkpoint_ts = last_ts
            return last_ts
        except Exception as exc:
            self._logger.error("Failed to fetch on-chain checkpoint timestamp: %s", exc)
            # Fallback to cached value when available.
            if self._last_known_checkpoint_ts is not None:
                return self._last_known_checkpoint_ts
            raise

    def _get_current_block_timestamp(self) -> int:
        """Return the timestamp of the latest block."""
        assert self._w3 is not None
        try:
            latest_block = self._w3.eth.get_block("latest")
            timestamp = latest_block.get("timestamp")
            if timestamp is None:
                raise KeyError("timestamp")
            return int(timestamp)
        except Exception as exc:
            self._logger.debug(
                "Failed to fetch latest block timestamp; falling back to system time: %s",
                exc,
            )
            return int(time.time())

    def _get_liveness_period(self) -> Optional[int]:
        """Resolve the liveness period from config or contract."""
        if self._cached_liveness_period is not None:
            return self._cached_liveness_period

        contract_value: Optional[int] = None
        if self._staking_contract is not None:
            try:
                value = self._staking_contract.functions.livenessPeriod().call()
                contract_value = int(value)
            except Exception as exc:
                self._logger.debug(
                    "Failed to fetch livenessPeriod from staking contract: %s", exc
                )

        if contract_value is not None and contract_value > 0:
            self._cached_liveness_period = contract_value
            return self._cached_liveness_period

        if self._config.liveness_period is not None:
            self._cached_liveness_period = int(self._config.liveness_period)
            return self._cached_liveness_period

        if not self._warned_missing_liveness:
            self._logger.warning(
                "Liveness period unavailable; defaulting to %s seconds",
                DEFAULT_LIVENESS_PERIOD,
            )
            self._warned_missing_liveness = True

        self._cached_liveness_period = DEFAULT_LIVENESS_PERIOD
        return self._cached_liveness_period

    def _get_next_reward_checkpoint_timestamp(self) -> Optional[int]:
        """Fetch the timestamp for the next reward checkpoint."""
        if self._staking_contract is None:
            return None
        try:
            value = (
                self._staking_contract.functions.getNextRewardCheckpointTimestamp().call()
            )
            ts = int(value or 0)
            if ts <= 0:
                return None
            return ts
        except Exception as exc:
            self._logger.debug(
                "Failed to fetch next reward checkpoint timestamp: %s", exc
            )
            return None

    def _submit_checkpoint_transaction(self, current_ts: int) -> Optional[str]:
        """Build, sign, and submit the checkpoint transaction."""
        if (
            self._staking_contract is None
            or self._account is None
            or self._w3 is None
            or self._private_key is None
        ):
            return None

        contract = self._staking_contract
        w3 = self._w3

        max_attempts = 3
        method_label = "checkpoint"
        for attempt in range(max_attempts):
            try:
                with self._nonce_lock:
                    nonce = self._resolve_nonce()
                    tx_params: Dict[str, Any] = {
                        "from": self._account.address,
                        "nonce": nonce,
                    }

                    gas_limit = self._estimate_gas(tx_params)
                    if gas_limit:
                        tx_params["gas"] = gas_limit

                    self._apply_fee_parameters(tx_params)
                    tx_params["chainId"] = w3.eth.chain_id

                    self._logger.info(
                        "Submitting staking %s transaction via safe %s "
                        "(nonce=%s, timestamp=%s)",
                        method_label,
                        self._safe_address,
                        nonce,
                        current_ts,
                    )

                    checkpoint_fn = self._get_checkpoint_function()
                    txn = checkpoint_fn.build_transaction(cast(TxParams, tx_params))

                    if self._dry_run:
                        # Do not sign/send; just print what would be submitted
                        try:
                            self._logger.info(
                                "[DRY RUN] Would submit staking %s tx: %s",
                                method_label,
                                {
                                    "to": contract.address,
                                    "from": self._account.address,
                                    "nonce": tx_params.get("nonce"),
                                    "gas": tx_params.get("gas"),
                                    "chainId": tx_params.get("chainId"),
                                    "maxPriorityFeePerGas": tx_params.get(
                                        "maxPriorityFeePerGas"
                                    ),
                                    "maxFeePerGas": tx_params.get("maxFeePerGas"),
                                    "gasPrice": tx_params.get("gasPrice"),
                                },
                            )
                        except Exception:
                            pass
                        return None
                    signed = w3.eth.account.sign_transaction(
                        txn, private_key=self._private_key
                    )
                    raw_tx = getattr(signed, "rawTransaction", None) or getattr(
                        signed, "raw_transaction", None
                    )
                    if raw_tx is None:
                        raise AttributeError(
                            "Signed transaction missing raw payload for checkpoint call"
                        )
                    tx_hash = w3.eth.send_raw_transaction(raw_tx)
                    self._nonce_cache = nonce + 1
                    self._last_submitted_at = current_ts
                    self._last_tx_hash = tx_hash.hex()
                    return self._last_tx_hash
            except ValueError as exc:
                self._handle_value_error(exc)
                lowered = str(exc).lower()
                if "nonce too low" in lowered and attempt < max_attempts - 1:
                    time.sleep(0.25)
                    continue
                raise
            except ContractLogicError as exc:
                self._logger.warning(
                    "Staking contract rejected checkpoint transaction: %s", exc
                )
                self._nonce_cache = None
                return None
            except Exception:
                self._nonce_cache = None
                raise

        return None

    def _get_checkpoint_function(self) -> Any:
        """Return the contract function to invoke for the next checkpoint."""
        assert self._staking_contract is not None
        return self._staking_contract.functions.checkpoint()

    def _estimate_gas(self, tx_params: Dict[str, Any]) -> Optional[int]:
        """Estimate gas usage for the checkpoint transaction."""
        if self._staking_contract is None:
            return None
        try:
            checkpoint_fn = self._get_checkpoint_function()
            gas_estimate = checkpoint_fn.estimate_gas(cast(TxParams, tx_params))
            buffered = int(gas_estimate * 1.2)
            result = max(buffered, 200_000)
            if result > MAX_TRANSACTION_GAS:
                self._logger.warning(
                    "Staking checkpoint gas estimate %s exceeds Fusaka per-transaction limit %s; capping to limit",
                    result,
                    MAX_TRANSACTION_GAS,
                )
                result = MAX_TRANSACTION_GAS
            return result
        except Exception as exc:
            self._logger.debug("Gas estimation failed for staking checkpoint: %s", exc)
            return None

    def _apply_fee_parameters(self, tx_params: Dict[str, Any]) -> None:
        """Populate gas price / fee parameters depending on network support."""
        if self._w3 is None:
            raise RuntimeError("Web3 not initialised for checkpoint client")

        try:
            latest_block = self._w3.eth.get_block("latest")
        except Exception as exc:
            self._logger.debug(
                "Failed to fetch latest block for fee parameters: %s", exc
            )
            tx_params["gasPrice"] = self._w3.eth.gas_price
            return

        base_fee = latest_block.get("baseFeePerGas")
        if base_fee is None:
            tx_params["gasPrice"] = self._w3.eth.gas_price
            return

        base_fee_int = int(base_fee)
        priority_fee = int(self._suggest_priority_fee())
        min_buffer = int(MIN_FEE_BUFFER_PER_GAS)
        max_buffer = int(MAX_FEE_BUFFER_PER_GAS)

        if priority_fee <= 0:
            buffer = min_buffer
        else:
            buffer = max(min_buffer, min(priority_fee, max_buffer))

        max_fee = base_fee_int + priority_fee + buffer

        tx_params["maxPriorityFeePerGas"] = priority_fee
        tx_params["maxFeePerGas"] = max_fee

        self._logger.debug(
            "Checkpoint fee params: base=%s priority=%s buffer=%s max=%s",
            base_fee_int,
            priority_fee,
            buffer,
            max_fee,
        )

    def _suggest_priority_fee(self) -> int:
        """Return a conservative priority fee similar to Safe.execTransaction."""
        if self._w3 is None:
            return int(DEFAULT_PRIORITY_FEE_PER_GAS)

        priority_fee: Optional[int] = None

        override_raw = os.environ.get(PRIORITY_FEE_OVERRIDE_ENV)
        if override_raw:
            try:
                priority_fee = max(0, int(override_raw))
                self._logger.debug(
                    "Using checkpoint priority fee override (%s=%s)",
                    PRIORITY_FEE_OVERRIDE_ENV,
                    priority_fee,
                )
            except ValueError:
                self._logger.warning(
                    "Invalid %s value '%s'; ignoring",
                    PRIORITY_FEE_OVERRIDE_ENV,
                    override_raw,
                )

        if priority_fee is None:
            try:
                suggested = getattr(self._w3.eth, "max_priority_fee", None)
                if callable(suggested):
                    suggested = suggested()
                if suggested is not None:
                    priority_fee = int(suggested)
            except Exception as exc:
                self._logger.debug(
                    "Failed to obtain RPC priority fee suggestion: %s", exc
                )

        if priority_fee is None or priority_fee <= 0:
            priority_fee = int(DEFAULT_PRIORITY_FEE_PER_GAS)

        if 0 < priority_fee < int(MIN_PRIORITY_FEE_PER_GAS):
            priority_fee = int(MIN_PRIORITY_FEE_PER_GAS)
        elif priority_fee > int(MAX_PRIORITY_FEE_PER_GAS):
            priority_fee = int(MAX_PRIORITY_FEE_PER_GAS)

        return priority_fee

    def _resolve_nonce(self) -> int:
        """Return the next transaction nonce, caching between submissions."""
        if self._w3 is None or self._account is None:
            raise RuntimeError(
                "Nonce requested before checkpoint client initialisation"
            )
        if self._nonce_cache is None:
            self._nonce_cache = self._w3.eth.get_transaction_count(
                self._account.address, "pending"
            )
        return self._nonce_cache

    def _handle_value_error(self, error: ValueError) -> None:
        """Interpret provider errors to adjust nonce cache when relevant."""
        message = str(error)
        lowered = message.lower()
        if "nonce too low" in lowered:
            self._logger.debug("RPC reported nonce too low; clearing cached nonce")
            self._nonce_cache = None
        elif "replacement transaction underpriced" in lowered:
            self._logger.debug(
                "Replacement transaction underpriced; clearing cached nonce"
            )
            self._nonce_cache = None
        else:
            self._logger.warning("RPC error during checkpoint submission: %s", message)

    def _initialise(self) -> None:
        """Initialise web3 provider, account and staking contract."""
        private_key = (self._config.private_key or "").strip()
        if not private_key:
            self._logger.info(
                "Staking checkpoint disabled: ethereum private key unavailable"
            )
            return
        if not private_key.startswith("0x"):
            private_key = f"0x{private_key}"

        rpc_url = (self._config.rpc_url or "").strip()
        if not rpc_url:
            self._logger.info(
                "Staking checkpoint disabled: RPC endpoint not configured"
            )
            return

        try:
            w3 = Web3(Web3.HTTPProvider(rpc_url))
        except Exception as exc:
            self._logger.error(f"Failed to create Web3 provider for checkpoint: {exc}")
            return

        if not w3.is_connected():
            self._logger.warning(
                "Web3 provider could not connect; staking checkpoint disabled"
            )
            return

        self._inject_poa_middleware(w3)

        try:
            account = w3.eth.account.from_key(private_key)
        except ValueError as exc:
            self._logger.error(f"Invalid ethereum private key supplied: {exc}")
            return

        try:
            staking_contract = w3.eth.contract(
                address=Web3.to_checksum_address(self._config.staking_contract_address),
                abi=STAKING_PROXY_ABI,
            )
        except Exception as exc:
            self._logger.error(f"Failed to instantiate staking contract: {exc}")
            return

        self._w3 = w3
        self._staking_contract = staking_contract
        self._account = account
        self._private_key = private_key

        # Use a process-wide shared lock for this address to prevent nonce races
        try:
            self._nonce_lock = get_shared_nonce_lock(account.address)
        except Exception:
            pass

        addr_preview = f"{account.address[:6]}...{account.address[-4:]}"
        self._logger.info(
            "Staking checkpoint client initialised for agent %s (safe %s, contract %s)",
            addr_preview,
            self._safe_address,
            staking_contract.address,
        )

    def _inject_poa_middleware(self, w3: Web3) -> None:
        """Inject POA middleware when required (e.g., Base network)."""
        try:
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        except ValueError:
            pass
        except Exception as exc:
            self._logger.debug(
                f"Failed to inject POA middleware fallback for checkpoint client: {exc}"
            )

    def _record_state(
        self,
        last_checkpoint_ts: int,
        checked_at: int,
        tx_hash: Optional[str],
        submission_ts: Optional[int] = None,
    ) -> None:
        """Persist last checkpoint information for reuse across restarts."""
        self._last_known_checkpoint_ts = last_checkpoint_ts
        self._last_checked_at = checked_at
        if submission_ts is not None:
            self._last_submitted_at = submission_ts
        if tx_hash:
            self._last_tx_hash = tx_hash

        if self._state_file is None:
            return

        payload = {
            "last_checkpoint_ts": int(last_checkpoint_ts),
            "last_checked_at": int(checked_at),
            "last_submitted_at": (
                int(self._last_submitted_at)
                if self._last_submitted_at is not None
                else None
            ),
            "last_tx_hash": tx_hash,
        }

        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with self._state_file.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
        except Exception as exc:
            self._logger.debug(
                "Failed to persist staking checkpoint state to %s: %s",
                self._state_file,
                exc,
            )

    def _load_state(self) -> None:
        """Load previously persisted state if available."""
        if self._state_file is None:
            return
        if not self._state_file.exists():
            return
        try:
            with self._state_file.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            self._logger.debug(
                "Failed to load staking checkpoint state from %s: %s",
                self._state_file,
                exc,
            )
            return

        try:
            self._last_known_checkpoint_ts = (
                int(payload.get("last_checkpoint_ts"))
                if payload.get("last_checkpoint_ts") is not None
                else None
            )
            self._last_checked_at = (
                int(payload.get("last_checked_at"))
                if payload.get("last_checked_at") is not None
                else None
            )
            self._last_submitted_at = (
                int(payload.get("last_submitted_at"))
                if payload.get("last_submitted_at") is not None
                else None
            )
            tx_hash = payload.get("last_tx_hash")
            self._last_tx_hash = str(tx_hash) if tx_hash else None
        except (TypeError, ValueError) as exc:
            self._logger.debug(
                "Malformed staking checkpoint state payload ignored: %s", exc
            )

    def _resolve_state_file(self, state_file: Optional[Path]) -> Path:
        """Return the path to the state file, defaulting to ./data."""
        if state_file is None:
            return DEFAULT_STATE_FILE
        if isinstance(state_file, Path):
            return state_file
        return Path(state_file)

    def _normalise_address(self, address: Optional[str]) -> str:
        """Return a checksum-safe address when possible."""
        addr = (address or DEFAULT_SAFE_ADDRESS).strip()
        if not addr:
            return DEFAULT_SAFE_ADDRESS
        try:
            return Web3.to_checksum_address(addr)
        except Exception:
            return addr
