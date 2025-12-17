#!/usr/bin/env python3
"""
Olas SDK Entry Point for Pett Agent
Compliant with: https://stack.olas.network/olas-sdk/#step-1-build-the-agent-supporting-the-following-requirements
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

# Add the current directory to Python path for local imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables from .env file early
try:
    from dotenv import load_dotenv

    # Load .env file from the current directory
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        # Also try loading from parent directory
        load_dotenv()
except ImportError:
    # python-dotenv not available, continue without it
    pass

import typing_extensions_patch  # noqa: F401  # ensures Sentinel backport if needed
from eth_account import Account

from agent.olas_interface import OlasInterface
from agent.pett_agent import PettAgent

DEFAULT_AGENT_VERSION = "0.1.0"


def setup_olas_logging() -> logging.Logger:
    """Set up logging according to Olas SDK requirements.

    Format: [YYYY-MM-DD HH:MM:SS,mmm] [LOG_LEVEL] [agent] Your message
    """
    # Create logs directory if it doesn't exist
    os.makedirs("logs", exist_ok=True)

    # Configure logging with Olas required format
    log_format = "[%(asctime)s] [%(levelname)s] [agent] %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # Configure root logger
    logging.basicConfig(
        level=logging.DEBUG,
        format=log_format,
        datefmt=date_format,
        handlers=[
            # File handler for log.txt (required by Olas)
            # logging.FileHandler("log.txt", mode="a"),
            # Console handler for development
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Configure specific logger for our agent
    logger = logging.getLogger("pett_agent")
    logger.setLevel(logging.DEBUG)

    return logger


def _prepare_private_key_material(
    key_data: str, password: Optional[str], source: str
) -> Optional[str]:
    """Return a usable private key, decrypting keystores when a password is given."""
    key_data = key_data.strip()
    if not key_data:
        return None

    if password is None:
        return key_data

    try:
        decrypted_bytes = Account.decrypt(key_data, password)
    except Exception as exc:
        logging.error("Failed to decrypt ethereum private key from %s: %s", source, exc)
        return None

    return f"0x{decrypted_bytes.hex()}"


def read_ethereum_private_key(password: Optional[str] = None) -> Optional[str]:
    """Read the ethereum private key, supporting plaintext and encrypted keystores."""
    candidates = [
        Path("./ethereum_private_key.txt"),
        Path("../agent_key/ethereum_private_key.txt"),
    ]
    # Environment variable fallback
    env_key = os.environ.get("ETH_PRIVATE_KEY") or os.environ.get(
        "CONNECTION_CONFIGS_CONFIG_ETH_PRIVATE_KEY"
    )
    try:
        for key_file in candidates:
            try:
                if key_file.exists():
                    with open(key_file, "r", encoding="utf-8") as f:
                        key = f.read()
                        processed = _prepare_private_key_material(
                            key, password, str(key_file)
                        )
                        if processed:
                            return processed
            except Exception:
                continue
        if env_key and env_key.strip():
            # If password not provided, try to get it from environment
            if password is None:
                password = os.environ.get("ETH_PRIVATE_KEY_PASSWORD")
            processed = _prepare_private_key_material(
                env_key, password, "environment variable"
            )
            if processed:
                return processed
        logging.warning(
            "ethereum_private_key not found (checked ./ethereum_private_key.txt, "
            "../agent_key/ethereum_private_key.txt, and ETH_PRIVATE_KEY env)"
        )
        return None
    except Exception as e:
        logging.error(f"Failed to read ethereum private key: {e}")
        return None


def check_withdrawal_mode() -> bool:
    """Check if agent should run in withdrawal mode (Olas SDK requirement)."""
    return False


async def main(password: Optional[str] = None):
    """Main entry point for the Pett Agent."""
    logger = setup_olas_logging()
    logger.info("🚀 Starting Pett Agent with Olas SDK compliance")

    try:
        # Read Olas SDK required configurations
        ethereum_private_key = read_ethereum_private_key(password=password)
        withdrawal_mode = check_withdrawal_mode()

        # Log configuration
        logger.info(
            f"Ethereum private key: {'Found' if ethereum_private_key else 'Not found'}"
        )
        logger.info(f"Withdrawal mode: {withdrawal_mode}")

        # Initialize Olas interface layer
        olas_interface = OlasInterface(
            ethereum_private_key=ethereum_private_key,
            withdrawal_mode=withdrawal_mode,
            logger=logger,
        )

        # Initialize your Pett Agent with existing logic
        pett_agent = PettAgent(
            olas_interface=olas_interface, logger=logger, is_production=True
        )

        # Start the agent
        await pett_agent.run()

    except KeyboardInterrupt:
        logger.info("🛑 Agent shutdown requested by user")
    except Exception as e:
        logger.error(f"💥 Critical error in Pett Agent: {e}", exc_info=True)
        raise


def get_version() -> str:
    """Return the current agent version."""
    return os.environ.get("PETT_AGENT_VERSION", DEFAULT_AGENT_VERSION)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the agent runner."""
    parser = argparse.ArgumentParser(description="Run the Pett Agent.")
    parser.add_argument(
        "--password",
        type=str,
        help="Password to decrypt the Ethereum private key.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the Pett Agent version and exit.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    cli_args = parse_args()

    if cli_args.version:
        print(f"Pett Agent Runner {get_version()}")
        sys.exit(0)

    try:
        asyncio.run(main(password=cli_args.password))
    except KeyboardInterrupt:
        print("\n🛑 Agent stopped by user")
        sys.exit(0)
    except Exception as e:
        print(f"💥 Fatal error: {e}")
        sys.exit(1)
