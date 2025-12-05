"""
Action recorder utility for emitting transactions to the Pett action repository.

The recorder reads the agent EOA private key, connects to the target contract and
exposes an async interface that can be scheduled from the rest of the agent.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
import os
import json
import re
from typing import Dict, Optional, Set, Any, Tuple, cast

from eth_account.signers.local import LocalAccount
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ContractLogicError
from web3.middleware import ExtraDataToPOAMiddleware
from web3.types import TxParams

from .gas_limits import MAX_TRANSACTION_GAS
from .nonce_utils import get_shared_nonce_lock

# Contract address provided by the user.
DEFAULT_ACTION_REPO_ADDRESS = "0x907afc85f3922cbdeb7b9ed806742b4ef998df31"


# ABI fragment for the recordAction interaction (provided by the user).
ACTION_REPO_ABI = [
    {
        "inputs": [
            {"internalType": "uint8", "name": "actionId", "type": "uint8"},
            {"internalType": "bytes32", "name": "nonce", "type": "bytes32"},
            {"internalType": "uint256", "name": "timestamp", "type": "uint256"},
            {"internalType": "uint8", "name": "v", "type": "uint8"},
            {"internalType": "bytes32", "name": "r", "type": "bytes32"},
            {"internalType": "bytes32", "name": "s", "type": "bytes32"},
        ],
        "name": "recordAction",
        "outputs": [
            {"internalType": "uint256", "name": "newActionCount", "type": "uint256"}
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "mainSigner",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# Minimal ABI for Gnosis Safe we interact with
SAFE_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "value", "type": "uint256"},
            {"internalType": "bytes", "name": "data", "type": "bytes"},
            {"internalType": "uint8", "name": "operation", "type": "uint8"},
            {"internalType": "uint256", "name": "safeTxGas", "type": "uint256"},
            {"internalType": "uint256", "name": "baseGas", "type": "uint256"},
            {"internalType": "uint256", "name": "gasPrice", "type": "uint256"},
            {"internalType": "address", "name": "gasToken", "type": "address"},
            {
                "internalType": "address payable",
                "name": "refundReceiver",
                "type": "address",
            },
            {"internalType": "bytes", "name": "signatures", "type": "bytes"},
        ],
        "name": "execTransaction",
        "outputs": [{"internalType": "bool", "name": "success", "type": "bool"}],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "value", "type": "uint256"},
            {"internalType": "bytes", "name": "data", "type": "bytes"},
            {"internalType": "uint8", "name": "operation", "type": "uint8"},
            {"internalType": "uint256", "name": "safeTxGas", "type": "uint256"},
            {"internalType": "uint256", "name": "baseGas", "type": "uint256"},
            {"internalType": "uint256", "name": "gasPrice", "type": "uint256"},
            {"internalType": "address", "name": "gasToken", "type": "address"},
            {"internalType": "address", "name": "refundReceiver", "type": "address"},
            {"internalType": "uint256", "name": "_nonce", "type": "uint256"},
        ],
        "name": "getTransactionHash",
        "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "nonce",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "getOwners",
        "outputs": [{"internalType": "address[]", "name": "", "type": "address[]"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "getThreshold",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


# Safe tx gas configuration constants inspired by Valory's implementation
MIN_GAS = 1
GAS_ADJUSTMENT = 50_000
ZERO_ADDRESS = "0x" + "0" * 40
# Increased safeTxGas to prevent "out of gas" errors in Safe.execTransaction
# safeTxGas is the gas reserved for the inner transaction execution
DEFAULT_SAFE_TX_GAS = 60_000
DEFAULT_BASE_GAS = 10_000
MIN_SAFE_TX_GAS_OVERRIDE = 30_000
MAX_SAFE_TX_GAS_OVERRIDE = 5_000_000
MIN_BASE_GAS_OVERRIDE = 1_000
MAX_BASE_GAS_OVERRIDE = 500_000
SAFE_EXECUTION_HEADROOM = 10_000
SAFE_GAS_ESTIMATE_BUFFER_MULTIPLIER = 1.5
SAFE_GAS_ESTIMATE_MIN_HEADROOM = 60_000
SAFE_INTRINSIC_DYNAMIC_MARGIN = 120_000
SAFE_MIN_FALLBACK_GAS = 450_000
SAFE_FALLBACK_ADDITIONAL_BUFFER = 200_000
TX_BASE_INTRINSIC_GAS = 21_000
CALLDATA_ZERO_BYTE_COST = 4
CALLDATA_NONZERO_BYTE_COST = 16
SAFE_INTRINSIC_GAS_BUFFER = 10_000
SAFE_INTRINSIC_FALLBACK_GAS = 70_000
SAFE_OWNER_REFRESH_INTERVAL_SECONDS = 300

# Delay between nonce retries to give pending transactions time to propagate.
NONCE_RETRY_DELAY_SECONDS = 0.75
# Safe nonce fetch retry configuration to avoid stale fallback usage.
SAFE_NONCE_MAX_ATTEMPTS = 3
SAFE_NONCE_FETCH_RETRY_DELAY_SECONDS = 0.5

# EIP-1559 fee defaults (values expressed in wei)
DEFAULT_PRIORITY_FEE_PER_GAS = Web3.to_wei(5, "mwei")  # 0.005 gwei
MIN_PRIORITY_FEE_PER_GAS = Web3.to_wei(1, "mwei")  # 0.001 gwei floor
MAX_PRIORITY_FEE_PER_GAS = Web3.to_wei(50, "mwei")  # 0.05 gwei cap
MIN_FEE_BUFFER_PER_GAS = Web3.to_wei(5, "mwei")  # 0.005 gwei headroom
MAX_FEE_BUFFER_PER_GAS = Web3.to_wei(50, "mwei")  # 0.05 gwei cap
PRIORITY_FEE_OVERRIDE_ENV = "ACTION_PRIORITY_FEE_WEI"


def _default_action_type_ids() -> Dict[str, int]:
    """Return the default mapping between Pett actions and numeric identifiers."""
    entries = [
        "CONSUMABLES_USE",
        "CONSUMABLES_BUY",
        "RUB",
        "SHOWER",
        "SLEEP",
        "THROWBALL",
        "ACCESSORY_USE",
        "ACCESSORY_BUY",
        "HOTEL_CHECK_IN",
        "HOTEL_CHECK_OUT",
        "HOTEL_BUY",
        "WITHDRAWAL_CREATE",
        "WITHDRAWAL_QUEUE",
        "WITHDRAWAL_JUMP",
        "WITHDRAWAL_USE",
        "TRANSFER",
        "DEPOSIT",
    ]
    # Enumerate sequential ids starting at 1.
    return {name: idx + 1 for idx, name in enumerate(entries)}


@dataclass
class RecorderConfig:
    """Runtime configuration for the recorder."""

    private_key: str
    rpc_url: str
    contract_address: str = DEFAULT_ACTION_REPO_ADDRESS


class ActionRecorder:
    """Encapsulates the on-chain interaction with the action repository contract."""

    def __init__(
        self,
        config: RecorderConfig,
        logger: Optional[logging.Logger] = None,
        action_type_ids: Optional[Dict[str, int]] = None,
    ) -> None:
        self._logger: logging.Logger = logger or logging.getLogger("action_recorder")
        self._config = config
        self._action_type_ids: Dict[str, int] = (
            action_type_ids or _default_action_type_ids()
        )
        self._w3: Optional[Web3] = None
        self._contract: Optional[Contract] = None
        self._account: Optional[LocalAccount] = None
        self._private_key: Optional[str] = None
        self._nonce_lock = threading.Lock()
        self._nonce_cache: Optional[int] = None
        self._safe_nonce_cache: Dict[str, int] = {}
        self._unknown_actions: Set[str] = set()
        self._enabled: bool = False
        self._safe_owner_snapshot: Optional[Set[str]] = None
        self._safe_owner_threshold: Optional[int] = None
        self._last_safe_owner_check: float = 0.0

        self._initialise()

    @property
    def contract_address(self) -> Optional[str]:
        """Return the configured contract address, if available."""
        try:
            if self._contract is not None:
                return self._contract.address  # type: ignore[attr-defined]
        except Exception:
            pass
        return getattr(self._config, "contract_address", None)

    @property
    def rpc_url(self) -> Optional[str]:
        """Return the configured RPC URL, if available."""
        return getattr(self._config, "rpc_url", None)

    @property
    def account_address(self) -> Optional[str]:
        """Return the agent account address, if available."""
        try:
            return self._account.address if self._account else None  # type: ignore[union-attr]
        except Exception:
            return None

    @property
    def is_enabled(self) -> bool:
        """Return True when the recorder is ready to emit transactions."""
        return self._enabled

    def _initialise(self) -> None:
        """Initialise Web3 provider, contract instance and signing account."""
        private_key = (self._config.private_key or "").strip()
        if not private_key:
            self._logger.warning(
                "ActionRecorder initialisation skipped: missing ethereum private key"
            )
            return
        if not private_key.startswith("0x"):
            private_key = f"0x{private_key}"

        rpc_url = (self._config.rpc_url or "").strip()
        if not rpc_url:
            self._logger.warning(
                "ActionRecorder initialisation skipped: missing RPC endpoint"
            )
            return

        try:
            w3 = Web3(Web3.HTTPProvider(rpc_url))
        except Exception as exc:
            self._logger.error(f"Failed to create Web3 provider: {exc}")
            return

        if not w3.is_connected():
            self._logger.warning(
                "Web3 provider could not connect to RPC endpoint; action recording disabled"
            )
            return

        # Inject POA middleware for chains such as Gnosis/Base.
        self._inject_poa_middleware(w3)

        try:
            account = w3.eth.account.from_key(private_key)
        except ValueError as exc:
            self._logger.error(f"Invalid ethereum private key supplied: {exc}")
            return

        try:
            checksum_address = Web3.to_checksum_address(self._config.contract_address)
            contract = w3.eth.contract(address=checksum_address, abi=ACTION_REPO_ABI)
        except Exception as exc:
            self._logger.error(f"Failed to instantiate action repo contract: {exc}")
            return

        # Optional: instantiate Gnosis Safe
        safe_contract: Optional[Contract] = None
        # Priority 1: single-address env var (required by user)
        safe_addr = (
            os.environ.get("CONNECTION_CONFIGS_CONFIG_SAFE_CONTRACT_ADDRESS") or ""
        ).strip()
        # Priority 2: JSON mapping env var
        if not safe_addr:
            try:
                mapping_json = os.environ.get(
                    "CONNECTION_CONFIGS_CONFIG_SAFE_CONTRACT_ADDRESSES"
                )
                if mapping_json and mapping_json.strip():
                    mapping = json.loads(mapping_json)
                    chain_key: Optional[str] = None
                    # Prefer chainId mapping
                    try:
                        chain_id = int(w3.eth.chain_id)  # type: ignore[attr-defined]
                    except Exception:
                        chain_id = None  # type: ignore[assignment]
                    id_to_name = {
                        1: "ethereum",
                        5: "goerli",
                        11155111: "sepolia",
                        100: "gnosis",
                        8453: "base",
                        84532: "base_sepolia",
                        137: "polygon",
                        56: "bsc",
                    }
                    candidates: list[str] = []
                    if chain_id is not None and chain_id in id_to_name:
                        candidates.append(id_to_name[chain_id])
                    if chain_id is not None:
                        candidates.append(str(chain_id))
                    # Heuristic by RPC URL
                    rpc_lower = (self._config.rpc_url or "").lower()
                    if "gnosis" in rpc_lower or "gno" in rpc_lower:
                        candidates.append("gnosis")
                    if "base" in rpc_lower:
                        candidates.append("base")
                    if "sepolia" in rpc_lower:
                        candidates.append("sepolia")
                    if "polygon" in rpc_lower:
                        candidates.append("polygon")
                    for key in candidates:
                        if (
                            isinstance(mapping, dict)
                            and key in mapping
                            and mapping.get(key)
                        ):
                            chain_key = key
                            break
                    if (
                        not chain_key
                        and isinstance(mapping, dict)
                        and len(mapping) == 1
                    ):
                        chain_key = next(iter(mapping.keys()))
                    if chain_key:
                        resolved = str(mapping.get(chain_key, "")).strip()
                        if resolved:
                            safe_addr = resolved
            except Exception as exc:
                self._logger.debug(
                    f"Failed to resolve Safe from JSON mapping env: {exc}"
                )

        if safe_addr:
            try:
                safe_checksum = Web3.to_checksum_address(safe_addr)
                safe_contract = w3.eth.contract(address=safe_checksum, abi=SAFE_ABI)
                # Extra diagnostics: confirm resolved Safe and chain id
                try:
                    self._logger.info(
                        f"Using Safe {safe_checksum} on chainId {w3.eth.chain_id}"
                    )
                except Exception:
                    pass
            except Exception as exc:
                self._logger.error(f"Failed to instantiate Gnosis Safe contract: {exc}")
                safe_contract = None
                raise exc
        else:
            self._logger.error(
                "AI Agent Safe address missing. Set CONNECTION_CONFIGS_CONFIG_SAFE_CONTRACT_ADDRESS or "
                "CONNECTION_CONFIGS_CONFIG_SAFE_CONTRACT_ADDRESSES (JSON)."
            )
            exit(1)

        # Optional: sanity-check Safe ownership/threshold
        try:
            if safe_contract is not None:
                owners_valid = self._refresh_safe_owner_status(
                    safe_contract=safe_contract,
                    account=account,
                    force=True,
                    context="initialisation",
                    log_snapshot=True,
                )
                if not owners_valid:
                    self._logger.error(
                        "Safe ownership/threshold validation failed during initialisation; "
                        "ActionRecorder disabled"
                    )
                    return
        except Exception as exc:
            self._logger.error(
                f"Failed to sanity-check Safe ownership/threshold: {exc}"
            )
            raise exc

        self._w3 = w3
        self._contract = contract
        self._safe_contract = safe_contract
        self._account = account
        self._private_key = private_key
        self._enabled = True

        # Use a process-wide shared lock for this address to prevent nonce races
        try:
            self._nonce_lock = get_shared_nonce_lock(account.address)
        except Exception:
            pass

        address_preview = f"{account.address[:6]}...{account.address[-4:]}"
        self._logger.info(
            f"ActionRecorder initialised for agent address {address_preview}"
        )

    async def record_action_verified(
        self, action_name: str, verification: Dict[str, Any]
    ) -> None:
        """Record a Pett action on-chain using server-provided signature verification.

        The verification dict is expected to have the following structure:
        {
          "hash": "0x...",  # optional informational
          "signature": {"v": 27|28, "r": "0x..", "s": "0x.."},
          "message": {
            "action": 3,                   # uint8 action id
            "actionName": "RUB",           # optional, informational
            "timestamp": "1761755842",     # string or int seconds
            "nonce": "0x..."               # bytes32 hex
          }
        }
        """
        if not self._enabled or not self._contract or not self._w3 or not self._account:
            return

        action_key = (action_name or "").upper()
        # Prefer local mapping for robustness; fall back to server-provided id if unknown
        action_id = self._action_type_ids.get(action_key)
        try:
            _msg_action = (verification.get("message", {}) or {}).get("action")
            server_action_id = int(_msg_action) if _msg_action is not None else None
        except Exception:
            server_action_id = None  # type: ignore[assignment]
        if action_id is None and server_action_id is not None:
            action_id = server_action_id

        if action_id is None:
            # Unknown action mapping; log and abort
            if action_key and action_key not in self._unknown_actions:
                self._unknown_actions.add(action_key)
                self._logger.debug(
                    f"No action id mapping defined for '{action_key}' (and no server id)"
                )
            return

        message = verification.get("message", {}) or {}
        signature = verification.get("signature", {}) or {}

        nonce_hex = str(message.get("nonce", "")).strip()
        timestamp_raw = message.get("timestamp")
        try:
            timestamp = int(timestamp_raw) if timestamp_raw is not None else 0
        except Exception:
            timestamp = 0
        v = int(signature.get("v", 0) or 0)
        r = str(signature.get("r", "")).strip()
        s = str(signature.get("s", "")).strip()

        if not (
            nonce_hex
            and timestamp > 0
            and v in (27, 28)
            and r.startswith("0x")
            and s.startswith("0x")
        ):
            self._logger.debug(
                f"Incomplete verification payload for {action_key}: nonce={bool(nonce_hex)} ts={timestamp} v={v}"
            )
            return

        # Log scheduling
        try:
            addr_preview = "unknown"
            if self._account and getattr(self._account, "address", None):
                addr = self._account.address
                addr_preview = f"{addr[:6]}...{addr[-4:]}"
            self._logger.info(
                f"On-chain recordAction queued: action={action_key} id={action_id} agent={addr_preview}"
            )
        except Exception:
            pass

        loop = asyncio.get_running_loop()
        try:
            assert action_id is not None
            inner_hash_hex = str(verification.get("hash", "") or "").strip()
            await loop.run_in_executor(
                None,
                self._record_action_verified_sync,
                action_key,
                int(action_id),
                nonce_hex,
                int(timestamp),
                int(v),
                r,
                s,
                inner_hash_hex,
            )
        except Exception as exc:
            self._logger.warning(
                f"Failed to submit verified recordAction for {action_key}: {exc}"
            )

    def _record_action_verified_sync(
        self,
        action_key: str,
        action_id: int,
        nonce_hex: str,
        timestamp: int,
        v: int,
        r: str,
        s: str,
        inner_hash_hex: str,
    ) -> None:
        """Execute the synchronous portion of verified recordAction."""
        if not self._enabled:
            return
        if self._contract is None:
            return
        if self._account is None:
            return
        if self._w3 is None:
            return
        if self._private_key is None:
            return

        contract = self._contract
        safe = self._safe_contract
        w3 = self._w3
        account = self._account
        private_key = self._private_key

        if safe is None:
            self._logger.warning(
                "Multisig not configured; cannot submit verified action"
            )
            return
        if not self._refresh_safe_owner_status(
            force=True,
            context=f"recordAction:{action_key}",
        ):
            self._logger.error(
                "Safe owner verification failed; aborting execTransaction"
            )
            return

        max_attempts = 50
        for attempt in range(max_attempts):
            try:
                with self._nonce_lock:
                    nonce = self._resolve_nonce()

                    # Validate inner recordAction signature signer against ActionRepo.mainSigner
                    derived_inner_hash = self._compute_record_action_hash(
                        action_id, nonce_hex, timestamp
                    )
                    if derived_inner_hash is None:
                        self._logger.error(
                            "Unable to derive recordAction hash; aborting execTransaction"
                        )
                        return

                    hash_to_verify = derived_inner_hash
                    if inner_hash_hex:
                        try:
                            provided_hash = HexBytes(inner_hash_hex)
                        except Exception as exc:
                            self._logger.error(
                                "Invalid supplied recordAction hash '%s': %s; aborting execTransaction",
                                inner_hash_hex,
                                exc,
                            )
                            return
                        if len(provided_hash) != 32:
                            self._logger.error(
                                "Inner verification hash length != 32 bytes; aborting execTransaction"
                            )
                            return
                        if provided_hash != derived_inner_hash:
                            self._logger.error(
                                "Provided recordAction hash %s does not match derived hash %s; aborting execTransaction",
                                provided_hash.hex(),
                                derived_inner_hash.hex(),
                            )
                            return
                        hash_to_verify = provided_hash

                    try:
                        from eth_keys.datatypes import Signature as EthSignature
                    except Exception as exc:
                        self._logger.error(
                            "eth_keys not available to recover inner signer: %s",
                            exc,
                        )
                        return

                    try:
                        r_int = (
                            int(str(r), 16) if str(r).startswith("0x") else int(r)
                        )
                        s_int = (
                            int(str(s), 16) if str(s).startswith("0x") else int(s)
                        )
                        v_raw = int(v)
                        # Normalize v to {0,1} for eth_keys: handle 27/28 and EIP-155 variants
                        if v_raw in (0, 1):
                            v_norm = v_raw
                        elif v_raw in (27, 28):
                            v_norm = v_raw - 27
                        else:
                            v_norm = (v_raw - 27) & 1
                        sig_obj = EthSignature(vrs=(v_norm, r_int, s_int))
                        recovered_inner = sig_obj.recover_public_key_from_msg_hash(
                            hash_to_verify
                        ).to_checksum_address()
                    except Exception as exc:
                        self._logger.error(
                            f"Failed to recover inner recordAction signer: {exc}"
                        )
                        return

                    try:
                        expected_main_signer = Web3.to_checksum_address(
                            contract.functions.mainSigner().call()
                        )
                    except Exception as exc:
                        self._logger.error(
                            f"Failed to load ActionRepo.mainSigner for verification: {exc}"
                        )
                        return

                    recovered_inner_cs = Web3.to_checksum_address(recovered_inner)
                    matches_main_signer = recovered_inner_cs == expected_main_signer
                    self._logger.info(
                        f"Inner recordAction signer: {recovered_inner_cs}; "
                        f"equals_mainSigner={matches_main_signer}; "
                        f"expected={expected_main_signer}"
                    )
                    if not matches_main_signer:
                        self._logger.error(
                            "Inner recordAction signer does not match ActionRepo.mainSigner"
                        )
                        self._logger.error("Aborting execTransaction")
                        return

                    # Build inner calldata for ActionRepo.recordAction (prefer direct encode)
                    try:
                        # Ensure strict types for bytes32 fields
                        nonce_b32 = HexBytes(nonce_hex)
                        r_b32 = HexBytes(r)
                        s_b32 = HexBytes(s)
                        v_u8 = int(v)
                        fn = contract.functions.recordAction(
                            int(action_id),
                            nonce_b32,
                            int(timestamp),
                            v_u8,
                            r_b32,
                            s_b32,
                        )
                        inner_data_hex = None
                        try:
                            inner_data_hex = fn._encode_transaction_data()
                        except Exception:
                            inner_txn = fn.build_transaction({"from": account.address})
                            inner_data_hex = inner_txn.get("data")
                        if not inner_data_hex or not str(inner_data_hex).strip():
                            raise ValueError(
                                "Failed to produce calldata for recordAction"
                            )
                        inner_data_bytes = self._to_bytes(inner_data_hex)
                    except Exception as exc:
                        self._logger.warning(f"Failed to encode inner calldata: {exc}")
                        return

                    # Safe params (mirrors Valory's get_raw_safe_transaction defaults)
                    to_addr = contract.address
                    value = 0
                    operation = 0

                    safe_address = None
                    try:
                        safe_address = cast(str, getattr(safe, "address", None))
                    except Exception:
                        safe_address = None

                    estimated_safe_tx_gas = self._estimate_safe_tx_gas(fn, safe_address)
                    if estimated_safe_tx_gas is not None:
                        safe_tx_gas = self._cap_transaction_gas(
                            estimated_safe_tx_gas, "Estimated safeTxGas"
                        )
                        try:
                            self._logger.info(
                                f"Estimated safeTxGas from recordAction: {safe_tx_gas}"
                            )
                        except Exception as exc:
                            self._logger.debug(
                                f"Failed to log estimated safeTxGas from recordAction: {exc}"
                            )
                            pass
                    else:
                        safe_tx_gas = DEFAULT_SAFE_TX_GAS
                        self._logger.debug(
                            "Falling back to DEFAULT_SAFE_TX_GAS due to missing estimate"
                        )
                    safe_tx_gas_override = os.environ.get("ACTION_SAFE_TX_GAS")
                    if safe_tx_gas_override:
                        try:
                            override_value = int(safe_tx_gas_override)
                            if override_value < MIN_SAFE_TX_GAS_OVERRIDE:
                                self._logger.warning(
                                    "ACTION_SAFE_TX_GAS override %s below minimum %s; raising to minimum",
                                    safe_tx_gas_override,
                                    MIN_SAFE_TX_GAS_OVERRIDE,
                                )
                                override_value = MIN_SAFE_TX_GAS_OVERRIDE
                            elif override_value > MAX_SAFE_TX_GAS_OVERRIDE:
                                self._logger.warning(
                                    "ACTION_SAFE_TX_GAS override %s above maximum %s; capping to maximum",
                                    safe_tx_gas_override,
                                    MAX_SAFE_TX_GAS_OVERRIDE,
                                )
                                override_value = MAX_SAFE_TX_GAS_OVERRIDE
                            if override_value > MAX_TRANSACTION_GAS:
                                self._logger.warning(
                                    "ACTION_SAFE_TX_GAS override %s exceeds Fusaka per-transaction limit %s; capping to limit",
                                    safe_tx_gas_override,
                                    MAX_TRANSACTION_GAS,
                                )
                                override_value = MAX_TRANSACTION_GAS
                            safe_tx_gas = override_value
                            self._logger.warning(
                                "Using ACTION_SAFE_TX_GAS override; applied safeTxGas=%s",
                                safe_tx_gas,
                            )
                        except ValueError:
                            self._logger.warning(
                                "Invalid ACTION_SAFE_TX_GAS override '%s'; keeping computed safeTxGas %s",
                                safe_tx_gas_override,
                                safe_tx_gas,
                            )
                    if (
                        estimated_safe_tx_gas is not None
                        and safe_tx_gas < estimated_safe_tx_gas
                    ):
                        self._logger.warning(
                            "Configured safeTxGas %s is below estimated inner execution requirement %s; bumping to estimate",
                            safe_tx_gas,
                            estimated_safe_tx_gas,
                        )
                        safe_tx_gas = self._cap_transaction_gas(
                            estimated_safe_tx_gas, "Estimated safeTxGas requirement"
                        )

                    base_gas = DEFAULT_BASE_GAS
                    base_gas_override = os.environ.get("ACTION_SAFE_BASE_GAS")
                    if base_gas_override:
                        try:
                            override_value = int(base_gas_override)
                            if override_value < MIN_BASE_GAS_OVERRIDE:
                                self._logger.warning(
                                    "ACTION_SAFE_BASE_GAS override %s below minimum %s; raising to minimum",
                                    base_gas_override,
                                    MIN_BASE_GAS_OVERRIDE,
                                )
                                override_value = MIN_BASE_GAS_OVERRIDE
                            elif override_value > MAX_BASE_GAS_OVERRIDE:
                                self._logger.warning(
                                    "ACTION_SAFE_BASE_GAS override %s above maximum %s; capping to maximum",
                                    base_gas_override,
                                    MAX_BASE_GAS_OVERRIDE,
                                )
                                override_value = MAX_BASE_GAS_OVERRIDE
                            if override_value > MAX_TRANSACTION_GAS:
                                self._logger.warning(
                                    "ACTION_SAFE_BASE_GAS override %s exceeds Fusaka per-transaction limit %s; capping to limit",
                                    base_gas_override,
                                    MAX_TRANSACTION_GAS,
                                )
                                override_value = MAX_TRANSACTION_GAS
                            base_gas = override_value
                            self._logger.warning(
                                "Using ACTION_SAFE_BASE_GAS override; applied baseGas=%s",
                                base_gas,
                            )
                        except ValueError:
                            self._logger.warning(
                                "Invalid ACTION_SAFE_BASE_GAS override '%s'; keeping configured baseGas %s",
                                base_gas_override,
                                base_gas,
                            )

                    safe_gas_price = 0
                    gas_token = ZERO_ADDRESS
                    refund_receiver = ZERO_ADDRESS

                    # Fetch Safe nonce with fallback
                    try:
                        safe_nonce, safe_nonce_is_fallback = self._get_safe_nonce_with_fallback(
                            safe
                        )
                    except RuntimeError as exc:
                        self._logger.error(
                            "Failed to resolve Safe nonce; aborting execTransaction: %s",
                            exc,
                        )
                        return
                    if safe_nonce_is_fallback:
                        self._logger.warning(
                            "Safe nonce fallback in effect; delaying execTransaction submission to avoid stale nonce"
                        )
                        return
                    try:
                        self._logger.info(f"Safe nonce: {safe_nonce}")
                    except Exception:
                        pass

                    # Compute Safe tx hash
                    try:
                        tx_hash_bytes = safe.functions.getTransactionHash(
                            to_addr,
                            value,
                            inner_data_bytes,
                            operation,
                            safe_tx_gas,
                            base_gas,
                            safe_gas_price,
                            gas_token,
                            refund_receiver,
                            safe_nonce,
                        ).call()
                        try:
                            tx_hash_hex = HexBytes(tx_hash_bytes).hex()
                            self._logger.info(f"Safe tx hash to sign: {tx_hash_hex}")
                        except Exception:
                            pass
                    except Exception as exc:
                        self._logger.warning(f"Failed to compute Safe tx hash: {exc}")
                        return

                    signatures: bytes = b""
                    # Sign for eth_sign flow (v -> v+4)
                    try:
                        from eth_account.messages import encode_defunct

                        msg = encode_defunct(primitive=tx_hash_bytes)
                        signed_msg = w3.eth.account.sign_message(
                            msg, private_key=private_key
                        )
                        sig_r = getattr(signed_msg, "r")
                        sig_s = getattr(signed_msg, "s")
                        # For eth_sign flow, adjust v so Safe treats it as contract-style signature
                        sig_v = int(getattr(signed_msg, "v")) + 4
                        signatures = (
                            sig_r.to_bytes(32, "big")
                            + sig_s.to_bytes(32, "big")
                            + bytes([sig_v])
                        )
                        # Extra diagnostics: recover signer and compare with owners/threshold
                        try:
                            recovered = w3.eth.account.recover_message(
                                msg, signature=signed_msg.signature
                            )
                            # get the address from the private key
                            recovered_address = w3.eth.account.from_key(
                                private_key
                            ).address
                            self._logger.info(
                                f"Recovered signer: {recovered_address} vs account: {account.address}"
                            )
                            if recovered_address != account.address:
                                self._logger.error(
                                    f"Recovered signer {recovered_address} does not match agent EOA {account.address}; "
                                    f"aborting execTransaction"
                                )
                                return
                            try:
                                owners_dbg = list(safe.functions.getOwners().call())
                            except Exception:
                                owners_dbg = []
                            try:
                                threshold_dbg = int(
                                    safe.functions.getThreshold().call()
                                )
                            except Exception:
                                threshold_dbg = None
                            owner_set = {
                                Web3.to_checksum_address(o) for o in owners_dbg
                            }
                            is_owner_recovered = (
                                Web3.to_checksum_address(recovered) in owner_set
                            )
                            self._logger.info(
                                f"Recovered signer: {recovered}; is_owner={is_owner_recovered}; "
                                f"threshold={threshold_dbg} safe={safe.address}"
                            )
                            # Abort early if signer doesn't match the agent EOA or is not a Safe owner
                            try:
                                recovered_cs = Web3.to_checksum_address(recovered)
                                account_cs = Web3.to_checksum_address(account.address)
                            except Exception:
                                recovered_cs = recovered
                                account_cs = account.address
                            if recovered_cs != account_cs:
                                self._logger.error(
                                    f"Recovered signer {recovered_cs} does not match agent EOA {account_cs}; "
                                    f"aborting execTransaction"
                                )
                                return
                            if not is_owner_recovered:
                                self._logger.error(
                                    f"Recovered signer {recovered_cs} is not an owner of the Safe; "
                                    f"aborting execTransaction"
                                )
                                return
                        except Exception as _exc:
                            self._logger.debug(f"Failed to recover signer: {_exc}")
                    except Exception as exc:
                        self._logger.warning(f"Failed to sign Safe transaction: {exc}")
                        return
                    if not signatures:
                        self._logger.error(
                            "Failed to assemble Safe signature payload; aborting execTransaction"
                        )
                        return

                    safe_exec_min = self._compute_safe_exec_min_gas(safe_tx_gas)
                    exec_intrinsic_gas = self._estimate_exec_intrinsic_gas(
                        safe,
                        to_addr,
                        value,
                        inner_data_bytes,
                        operation,
                        safe_tx_gas,
                        base_gas,
                        safe_gas_price,
                        gas_token,
                        refund_receiver,
                        signatures,
                    )
                    outer_requirement = (
                        safe_exec_min + base_gas + SAFE_EXECUTION_HEADROOM
                    )
                    if base_gas != 0 or safe_tx_gas != 0:
                        configured_gas = max(
                            base_gas + safe_tx_gas + GAS_ADJUSTMENT, outer_requirement
                        )
                        configured_gas += exec_intrinsic_gas
                    else:
                        configured_gas = max(exec_intrinsic_gas, MIN_GAS)
                    configured_gas = self._cap_transaction_gas(
                        configured_gas, "Configured Safe.execTransaction gas limit"
                    )
                    try:
                        self._logger.debug(
                            "Safe gas config: safeTxGas=%s baseGas=%s intrinsic=%s limit=%s",
                            safe_tx_gas,
                            base_gas,
                            exec_intrinsic_gas,
                            configured_gas,
                        )
                    except Exception:
                        pass

                    actual_nonce = w3.eth.get_transaction_count(
                        account.address, "pending"
                    )
                    if actual_nonce > nonce:
                        self._logger.debug(
                            "Local nonce cache (%s) behind chain nonce (%s); updating",
                            nonce,
                            actual_nonce,
                        )
                        nonce = actual_nonce
                        self._nonce_cache = nonce
                    elif actual_nonce < nonce:
                        self._logger.debug(
                            "Chain reports lower nonce (%s) than local (%s); keeping bumped nonce",
                            actual_nonce,
                            nonce,
                        )

                    tx_params: Dict[str, Any] = {
                        "from": account.address,
                        "nonce": nonce,
                        "value": 0,
                        "chainId": w3.eth.chain_id,
                    }
                    if configured_gas != MIN_GAS:
                        tx_params["gas"] = configured_gas

                    self._apply_fee_parameters(tx_params)

                    # Preflight simulation of execTransaction to catch GS026 before sending
                    try:
                        ok = safe.functions.execTransaction(
                            to_addr,
                            value,
                            inner_data_bytes,
                            operation,
                            safe_tx_gas,
                            base_gas,
                            safe_gas_price,
                            gas_token,
                            refund_receiver,
                            signatures,
                        ).call({"from": account.address})
                        self._logger.info(
                            f"Preflight execTransaction.call  => {account.address}"
                        )
                    except ContractLogicError as exc:
                        self._logger.debug(
                            f"Preflight execTransaction.call reverted: {exc}"
                        )
                    except Exception:
                        pass

                    self._logger.info(
                        "Submitting Safe.execTransaction for verified recordAction: "
                        f"action={action_key} id={action_id} from={account.address} nonce={nonce}"
                    )
                    self._logger.info(
                        (
                            f"Inner recordAction params: actionId={int(action_id)}, nonce={nonce_hex}, "
                            f"timestamp={int(timestamp)}, v={int(v)}, r={r}, s={s}"
                        )
                    )

                    transaction_builder = safe.functions.execTransaction(
                        to_addr,
                        value,
                        inner_data_bytes,
                        operation,
                        safe_tx_gas,
                        base_gas,
                        safe_gas_price,
                        gas_token,
                        refund_receiver,
                        signatures,
                    )
                    transaction_dict = transaction_builder.build_transaction(
                        cast(TxParams, tx_params)
                    )

                    estimate_params = dict(transaction_dict)
                    estimate_params.pop("gas", None)
                    gas_limit = self._estimate_gas_safe_exec(
                        safe,
                        to_addr,
                        value,
                        inner_data_bytes,
                        operation,
                        safe_tx_gas,
                        base_gas,
                        safe_gas_price,
                        gas_token,
                        refund_receiver,
                        signatures,
                        estimate_params,
                        intrinsic_gas_hint=exec_intrinsic_gas,
                    )

                    if gas_limit is not None:
                        if configured_gas != MIN_GAS:
                            gas_limit = max(gas_limit, configured_gas)
                        transaction_dict["gas"] = self._cap_transaction_gas(
                            gas_limit, "Safe.execTransaction gas limit (estimate)"
                        )
                    elif configured_gas != MIN_GAS:
                        transaction_dict["gas"] = self._cap_transaction_gas(
                            configured_gas, "Safe.execTransaction gas limit (configured)"
                        )
                        try:
                            self._logger.warning(
                                "Using configured Safe gas limit %s due to failed estimation",
                                configured_gas,
                            )
                        except Exception:
                            pass
                    else:
                        intrinsic_floor = max(
                            exec_intrinsic_gas + SAFE_FALLBACK_ADDITIONAL_BUFFER,
                            outer_requirement + exec_intrinsic_gas,
                            SAFE_MIN_FALLBACK_GAS,
                        )
                        transaction_dict["gas"] = self._cap_transaction_gas(
                            intrinsic_floor, "Safe.execTransaction gas limit (fallback)"
                        )
                        try:
                            self._logger.warning(
                                "Falling back to intrinsic-based Safe gas limit %s "
                                "(estimation unavailable, intrinsic=%s outer_req=%s)",
                                intrinsic_floor,
                                exec_intrinsic_gas,
                                outer_requirement,
                            )
                        except Exception:
                            pass

                    # Ensure no other transaction consumed the nonce while building this one.
                    refreshed_nonce: Optional[int]
                    try:
                        refreshed_nonce = w3.eth.get_transaction_count(
                            account.address, "pending"
                        )
                    except Exception as exc:
                        refreshed_nonce = None
                        self._logger.debug(
                            "Failed to refresh nonce prior to signing Safe.execTransaction: %s",
                            exc,
                        )
                    if refreshed_nonce is not None and refreshed_nonce > nonce:
                        self._logger.info(
                            "Pending nonce advanced from %s to %s during Safe.execTransaction construction; retrying",
                            nonce,
                            refreshed_nonce,
                        )
                        self._nonce_cache = refreshed_nonce
                        time.sleep(NONCE_RETRY_DELAY_SECONDS)
                        continue

                    signed = w3.eth.account.sign_transaction(
                        transaction_dict, private_key=private_key
                    )
                    raw_tx = getattr(signed, "rawTransaction", None) or getattr(
                        signed, "raw_transaction", None
                    )
                    if raw_tx is None:
                        raise AttributeError(
                            "SignedTransaction missing raw transaction payload"
                        )
                    sent_hash = w3.eth.send_raw_transaction(raw_tx)
                    self._nonce_cache = nonce + 1

                    self._logger.info(
                        f"Safe.execTransaction submitted: action={action_key} id={action_id} tx={sent_hash.hex()}"
                    )
                    return
            except ValueError as exc:
                self._handle_value_error(exc)
                try:
                    err0 = exc.args[0] if getattr(exc, "args", None) else None
                    if isinstance(err0, dict):
                        message = str(err0.get("message", str(exc)))
                    else:
                        message = str(exc)
                except Exception:
                    message = str(exc)

                lowered = message.lower()
                if "nonce too low" in lowered:
                    # Try to bump to the provider-suggested next nonce (or latest pending)
                    try:
                        hinted_next = self._parse_next_nonce_hint(message)
                    except Exception:
                        hinted_next = None

                    try:
                        latest_pending = w3.eth.get_transaction_count(
                            account.address, "pending"
                        )
                    except Exception:
                        latest_pending = None  # type: ignore[assignment]

                    candidate = (
                        hinted_next if hinted_next is not None else latest_pending
                    )
                    # Ensure we strictly increase over the last attempted nonce
                    try:
                        last_attempted = nonce
                    except Exception:
                        last_attempted = self._nonce_cache or 0
                    # Choose the max among hint/pending and last_attempted+1
                    if candidate is None:
                        next_nonce = int(last_attempted) + 1
                    else:
                        next_nonce = max(int(candidate), int(last_attempted) + 1)
                    if next_nonce is None:
                        # Fallback: increment local cache conservatively
                        next_nonce = (self._nonce_cache or 0) + 1
                    try:
                        self._logger.info(
                            f"Nonce too low; bumping tx nonce to {int(next_nonce)} and retrying"
                        )
                    except Exception:
                        pass
                    self._nonce_cache = int(next_nonce)
                    time.sleep(NONCE_RETRY_DELAY_SECONDS)
                    continue
                raise
            except Exception as exc:
                # Some providers may raise non-ValueError exceptions; still handle nonce-too-low robustly
                try:
                    message = str(exc)
                    lowered = message.lower()
                except Exception:
                    lowered = ""
                if "nonce too low" in lowered:
                    try:
                        hinted_next = self._parse_next_nonce_hint(message)
                    except Exception:
                        hinted_next = None
                    try:
                        latest_pending = w3.eth.get_transaction_count(
                            account.address, "pending"
                        )
                    except Exception:
                        latest_pending = None  # type: ignore[assignment]
                    try:
                        last_attempted = nonce
                    except Exception:
                        last_attempted = self._nonce_cache or 0
                    if hinted_next is None and latest_pending is None:
                        next_nonce = int(last_attempted) + 1
                    else:
                        cand = (
                            hinted_next if hinted_next is not None else latest_pending
                        )
                        next_nonce = max(int(cand), int(last_attempted) + 1)  # type: ignore[arg-type]
                    try:
                        self._logger.info(
                            f"Nonce too low (generic); bumping tx nonce to {int(next_nonce)} and retrying"
                        )
                    except Exception:
                        pass
                    self._nonce_cache = int(next_nonce)
                    time.sleep(NONCE_RETRY_DELAY_SECONDS)
                    continue
                raise
            except ContractLogicError as exc:
                self._logger.warning(
                    f"Contract rejected verified recordAction for {action_key}: {exc}"
                )
                self._nonce_cache = None
                return
            except Exception:
                self._nonce_cache = None
                raise

    def _refresh_safe_owner_status(
        self,
        safe_contract: Optional[Contract] = None,
        account: Optional[LocalAccount] = None,
        *,
        force: bool = False,
        context: str = "operation",
        log_snapshot: bool = False,
    ) -> bool:
        """Refresh Safe owner cache and ensure the agent EOA remains an owner."""
        safe = safe_contract or self._safe_contract
        acct = account or self._account
        if safe is None or acct is None:
            return False

        now = time.time()
        try:
            account_checksum = Web3.to_checksum_address(acct.address)
        except Exception:
            account_checksum = acct.address

        cached_threshold = self._safe_owner_threshold
        if (
            not force
            and self._last_safe_owner_check
            and now - self._last_safe_owner_check < SAFE_OWNER_REFRESH_INTERVAL_SECONDS
            and self._safe_owner_snapshot is not None
        ):
            if cached_threshold and cached_threshold > 1:
                self._logger.error(
                    "Safe threshold is %s but only one signature is provided; transaction (%s) would revert",
                    cached_threshold,
                    context,
                )
                return False
            if account_checksum in self._safe_owner_snapshot:
                return True
            self._logger.error(
                "Cached Safe owners indicate agent EOA is no longer an owner; aborting (%s)",
                context,
            )
            return False

        try:
            owners_raw = list(safe.functions.getOwners().call())
        except Exception as exc:
            self._logger.warning(
                "Failed to refresh Safe owners during %s check: %s", context, exc
            )
            if self._safe_owner_snapshot is None:
                return False
            return account_checksum in self._safe_owner_snapshot

        try:
            threshold = int(safe.functions.getThreshold().call())
        except Exception:
            threshold = None

        normalized_owner_set: Set[str] = set()
        for owner in owners_raw:
            try:
                normalized_owner_set.add(Web3.to_checksum_address(owner))
            except Exception:
                continue

        previous_snapshot = self._safe_owner_snapshot
        previous_threshold = self._safe_owner_threshold

        self._safe_owner_snapshot = normalized_owner_set
        self._safe_owner_threshold = threshold
        self._last_safe_owner_check = now

        if log_snapshot:
            self._logger.info(
                f"Safe owners (n={len(normalized_owner_set)}): {sorted(normalized_owner_set)}"
            )
            self._logger.info(
                f"Safe threshold: {threshold if threshold is not None else 'unknown'}"
            )

        if previous_snapshot is not None and normalized_owner_set != previous_snapshot:
            added = sorted(normalized_owner_set - previous_snapshot)
            removed = sorted(previous_snapshot - normalized_owner_set)
            self._logger.warning(
                "Safe owner set changed during %s check; added=%s removed=%s",
                context,
                added or ["<none>"],
                removed or ["<none>"],
            )

        if (
            previous_threshold is not None
            and threshold is not None
            and threshold != previous_threshold
        ):
            self._logger.warning(
                "Safe threshold changed from %s to %s during %s check",
                previous_threshold,
                threshold,
                context,
            )

        if threshold and threshold > 1:
            self._logger.error(
                "Safe threshold is %s but only one signature is provided; transaction will revert (%s)",
                threshold,
                context,
            )
            return False

        is_owner = account_checksum in normalized_owner_set
        if not is_owner:
            self._logger.error(
                "Agent EOA is NOT an owner of the AI Agent Safe; execTransaction will revert (GS026)"
            )
        return is_owner

    def _compute_record_action_hash(
        self, action_id: int, nonce_hex: str, timestamp: int
    ) -> Optional[HexBytes]:
        """Derive the recordAction hash used for signature verification."""
        try:
            nonce_bytes = HexBytes(nonce_hex)
        except Exception as exc:
            self._logger.error(
                f"Invalid nonce '{nonce_hex}' for recordAction hash: {exc}"
            )
            return None
        if len(nonce_bytes) != 32:
            self._logger.error(
                "Nonce for recordAction hash must be 32 bytes; aborting verification"
            )
            return None
        try:
            return Web3.solidity_keccak(
                ["uint8", "bytes32", "uint256"],
                [int(action_id), nonce_bytes, int(timestamp)],
            )
        except Exception as exc:
            self._logger.error(
                f"Failed to compute recordAction hash for verification: {exc}"
            )
            return None

    def _resolve_nonce(self) -> int:
        """Return the next transaction nonce, caching between submissions."""
        if self._w3 is None or self._account is None:
            raise RuntimeError("Nonce requested before recorder initialisation")
        if self._nonce_cache is None:
            self._nonce_cache = self._w3.eth.get_transaction_count(
                self._account.address, "pending"
            )
        return self._nonce_cache

    def _get_safe_nonce_with_fallback(self, safe: Contract) -> Tuple[int, bool]:
        """Fetch the Safe nonce with retries; return fallback flag when cache is used."""
        cache_key = "__default__"
        safe_address = None
        try:
            safe_address = cast(str, getattr(safe, "address", None))
        except Exception:
            safe_address = None
        if safe_address:
            try:
                cache_key = Web3.to_checksum_address(safe_address)
            except Exception:
                cache_key = safe_address.lower()

        last_saved = self._safe_nonce_cache.get(cache_key)
        safe_nonce: Optional[int] = None
        last_exc: Optional[Exception] = None
        for attempt in range(1, SAFE_NONCE_MAX_ATTEMPTS + 1):
            try:
                safe_nonce = int(safe.functions.nonce().call())
                break
            except Exception as exc:
                last_exc = exc
                try:
                    self._logger.warning(
                        "Failed to fetch Safe nonce (attempt %s/%s): %s",
                        attempt,
                        SAFE_NONCE_MAX_ATTEMPTS,
                        exc,
                    )
                except Exception:
                    pass
                if attempt < SAFE_NONCE_MAX_ATTEMPTS:
                    time.sleep(SAFE_NONCE_FETCH_RETRY_DELAY_SECONDS)

        if safe_nonce is not None:
            self._safe_nonce_cache[cache_key] = safe_nonce
            return safe_nonce, False

        fallback_nonce: Optional[int] = None
        if last_saved is not None:
            fallback_nonce = max(last_saved + 1, 1)
            try:
                self._logger.warning(
                    "Using fallback Safe nonce %s (last saved %s) after %s attempts",
                    fallback_nonce,
                    last_saved,
                    SAFE_NONCE_MAX_ATTEMPTS,
                )
            except Exception:
                pass
            self._safe_nonce_cache[cache_key] = fallback_nonce
            return fallback_nonce, True

        raise RuntimeError(
            f"Unable to fetch Safe nonce after {SAFE_NONCE_MAX_ATTEMPTS} attempts: {last_exc}"
        )

    def _estimate_gas_safe_exec(
        self,
        safe: Contract,
        to_addr: str,
        value: int,
        inner_data: bytes,
        operation: int,
        safe_tx_gas: int,
        base_gas: int,
        safe_gas_price: int,
        gas_token: str,
        refund_receiver: str,
        signatures: bytes,
        tx_params: Dict[str, Any],
        intrinsic_gas_hint: Optional[int] = None,
    ) -> Optional[int]:
        """Estimate gas for Safe.execTransaction with a conservative buffer."""
        try:
            gas_estimate = safe.functions.execTransaction(
                to_addr,
                value,
                inner_data,
                operation,
                safe_tx_gas,
                base_gas,
                safe_gas_price,
                gas_token,
                refund_receiver,
                signatures,
            ).estimate_gas(cast(TxParams, tx_params))
        except Exception as exc:
            self._logger.debug(f"Gas estimation failed for Safe.execTransaction: {exc}")
            return None

        intrinsic_gas = intrinsic_gas_hint
        if intrinsic_gas is None or intrinsic_gas <= 0:
            intrinsic_gas = self._estimate_exec_intrinsic_gas(
                safe,
                to_addr,
                value,
                inner_data,
                operation,
                safe_tx_gas,
                base_gas,
                safe_gas_price,
                gas_token,
                refund_receiver,
                signatures,
            )
        # Increase buffer conservatively and enforce a dynamic floor
        buffered = max(
            int(gas_estimate * SAFE_GAS_ESTIMATE_BUFFER_MULTIPLIER),
            gas_estimate + SAFE_GAS_ESTIMATE_MIN_HEADROOM,
        )
        safe_exec_min = self._compute_safe_exec_min_gas(safe_tx_gas)
        required_with_headroom = safe_exec_min + base_gas + SAFE_EXECUTION_HEADROOM
        intrinsic_floor = intrinsic_gas + SAFE_INTRINSIC_DYNAMIC_MARGIN
        minimum_limit = max(required_with_headroom + intrinsic_gas, intrinsic_floor)
        if buffered < minimum_limit:
            try:
                self._logger.warning(
                    "Safe gas estimate %s below dynamic minimum %s "
                    "(intrinsic=%s calldata=%s bytes); raising to minimum",
                    buffered,
                    minimum_limit,
                    intrinsic_gas,
                    len(inner_data),
                )
            except Exception:
                pass
        return self._cap_transaction_gas(
            max(buffered, minimum_limit), "Estimated Safe.execTransaction gas limit"
        )

    def _cap_transaction_gas(self, gas_value: int, context: str) -> int:
        """Cap gas values to Fusaka per-transaction limit with logging."""
        gas_int = int(gas_value)
        if gas_int <= MAX_TRANSACTION_GAS:
            return gas_int
        try:
            self._logger.warning(
                "%s (%s) exceeds Fusaka per-transaction gas limit (%s); capping to limit",
                context,
                gas_int,
                MAX_TRANSACTION_GAS,
            )
        except Exception:
            pass
        return MAX_TRANSACTION_GAS

    def _estimate_safe_tx_gas(
        self,
        record_action_fn: Any,
        safe_address: Optional[str],
    ) -> Optional[int]:
        """Estimate inner recordAction gas to derive safeTxGas."""
        if safe_address is None:
            return None

        try:
            gas_estimate = record_action_fn.estimate_gas({"from": safe_address})
        except ContractLogicError as exc:
            self._logger.debug(
                f"recordAction gas estimation reverted (ContractLogicError): {exc}"
            )
            return None
        except ValueError as exc:
            self._logger.debug(
                f"recordAction gas estimation failed (ValueError): {exc}"
            )
            return None
        except Exception as exc:
            self._logger.debug(f"recordAction gas estimation failed: {exc}")
            return None

        # Cushion the estimate to reduce the risk of underestimation.
        buffered = max(int(gas_estimate * 1.2), gas_estimate + 20_000)
        return self._cap_transaction_gas(
            max(buffered, MIN_GAS), "recordAction gas estimate"
        )

    def _compute_safe_exec_min_gas(self, safe_tx_gas: int) -> int:
        """Return the minimum gas Safe.execTransaction expects to remain."""
        safe_tx_gas = max(0, int(safe_tx_gas))
        scaled = (safe_tx_gas * 64 + 62) // 63  # ceil(safe_tx_gas * 64 / 63)
        requirement = max(scaled, safe_tx_gas + 2_500) + 500
        return requirement

    def _estimate_exec_intrinsic_gas(
        self,
        safe: Contract,
        to_addr: str,
        value: int,
        inner_data: bytes,
        operation: int,
        safe_tx_gas: int,
        base_gas: int,
        safe_gas_price: int,
        gas_token: str,
        refund_receiver: str,
        signatures: bytes,
    ) -> int:
        """Estimate intrinsic gas consumed before Safe.execTransaction code runs."""
        try:
            call_data_bytes = self._build_safe_exec_calldata(
                safe,
                to_addr,
                value,
                inner_data,
                operation,
                safe_tx_gas,
                base_gas,
                safe_gas_price,
                gas_token,
                refund_receiver,
                signatures,
            )
            zero_bytes = call_data_bytes.count(0)
            non_zero_bytes = len(call_data_bytes) - zero_bytes
            intrinsic = (
                TX_BASE_INTRINSIC_GAS
                + zero_bytes * CALLDATA_ZERO_BYTE_COST
                + non_zero_bytes * CALLDATA_NONZERO_BYTE_COST
            )
            return intrinsic + SAFE_INTRINSIC_GAS_BUFFER
        except Exception as exc:
            self._logger.debug(
                "Failed to estimate Safe.execTransaction intrinsic gas: %s", exc
            )
            return SAFE_INTRINSIC_FALLBACK_GAS

    def _build_safe_exec_calldata(
        self,
        safe: Contract,
        to_addr: str,
        value: int,
        inner_data: bytes,
        operation: int,
        safe_tx_gas: int,
        base_gas: int,
        safe_gas_price: int,
        gas_token: str,
        refund_receiver: str,
        signatures: bytes,
    ) -> bytes:
        """Return raw calldata for Safe.execTransaction, handling ABI quirks."""
        fn = safe.functions.execTransaction(
            to_addr,
            value,
            inner_data,
            operation,
            safe_tx_gas,
            base_gas,
            safe_gas_price,
            gas_token,
            refund_receiver,
            signatures,
        )
        try:
            encoded = fn._encode_transaction_data()
            if encoded:
                return self._to_bytes(encoded)
        except Exception:
            pass

        builder_from = getattr(self._account, "address", ZERO_ADDRESS)
        fallback_tx = fn.build_transaction({"from": builder_from})
        encoded = fallback_tx.get("data")
        if not encoded:
            raise ValueError("Failed to build Safe.execTransaction calldata")
        return self._to_bytes(encoded)

    def _suggest_priority_fee(self) -> int:
        """Return a conservative priority fee value in wei."""
        if self._w3 is None:
            return DEFAULT_PRIORITY_FEE_PER_GAS

        priority_fee: Optional[int] = None

        override_raw = os.environ.get(PRIORITY_FEE_OVERRIDE_ENV)
        if override_raw:
            try:
                priority_fee = max(0, int(override_raw))
                self._logger.debug(
                    "Using priority fee override (%s=%s)",
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
                    f"Failed to obtain RPC priority fee suggestion: {exc}"
                )

        if priority_fee is None or priority_fee <= 0:
            priority_fee = DEFAULT_PRIORITY_FEE_PER_GAS

        if 0 < priority_fee < MIN_PRIORITY_FEE_PER_GAS:
            priority_fee = MIN_PRIORITY_FEE_PER_GAS
        elif priority_fee > MAX_PRIORITY_FEE_PER_GAS:
            priority_fee = MAX_PRIORITY_FEE_PER_GAS

        return priority_fee

    def _apply_fee_parameters(self, tx_params: Dict[str, Any]) -> None:
        """Populate the fee parameters according to the network capabilities."""
        if self._w3 is None:
            raise RuntimeError(
                "Fee parameters requested before recorder initialisation"
            )
        try:
            latest_block = self._w3.eth.get_block("latest")
        except Exception as exc:
            self._logger.debug(f"Failed to fetch latest block for fee data: {exc}")
            gas_price = self._w3.eth.gas_price
            tx_params["gasPrice"] = gas_price
            self._logger.debug("Fallback to legacy gas price: %s", gas_price)
            return

        base_fee = latest_block.get("baseFeePerGas")
        if base_fee is None:
            gas_price = self._w3.eth.gas_price
            tx_params["gasPrice"] = gas_price
            self._logger.debug(
                "Legacy network without base fee; gasPrice=%s", gas_price
            )
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
            "Fee params: base=%s priority=%s buffer=%s max=%s",
            base_fee_int,
            priority_fee,
            buffer,
            max_fee,
        )

    def _handle_value_error(self, error: ValueError) -> None:
        """Parse provider ValueErrors and adjust nonce cache when relevant."""
        message = str(error)
        lowered = message.lower()
        if "nonce too low" in lowered:
            self._logger.debug("RPC reported nonce too low; clearing local nonce cache")
            self._nonce_cache = None
        elif "replacement transaction underpriced" in lowered:
            self._logger.debug("Replacement transaction underpriced; bumping fee")
            self._nonce_cache = None
        elif "intrinsic gas too low" in lowered or (
            "insufficient" in lowered and "gas" in lowered
        ):
            self._logger.error(
                "Safe transaction rejected due to insufficient gas; consider adjusting overrides or buffers. "
                f"Message: {message}"
            )
            self._nonce_cache = None
        else:
            self._logger.warning(f"RPC error during recordAction: {message}")

    def _parse_next_nonce_hint(self, message: str) -> Optional[int]:
        """Extract the provider-suggested next nonce from an error message, if present."""
        try:
            # Common patterns across geth/networks
            m = re.search(r"next\s*nonce[^0-9]*(\d+)", message, re.IGNORECASE)
            if m:
                return int(m.group(1))
            m = re.search(r"expected(?:\s*nonce)?[^0-9]*(\d+)", message, re.IGNORECASE)
            if m:
                return int(m.group(1))
            m = re.search(r"expected\s*:\s*(\d+)", message, re.IGNORECASE)
            if m:
                return int(m.group(1))
        except Exception:
            pass
        return None

    def _inject_poa_middleware(self, w3: Web3) -> None:
        """Inject a POA-compatible middleware when available."""
        try:
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        except ValueError:
            pass
        except Exception as exc:
            self._logger.debug(
                f"Failed to inject extra-data POA middleware fallback: {exc}"
            )

    def _to_bytes(self, data: Any) -> bytes:
        """Normalize hex string or HexBytes to raw bytes."""
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        try:
            s = str(data)
            if s.startswith("0x"):
                return bytes.fromhex(s[2:])
            return bytes.fromhex(s)
        except Exception:
            return b""
