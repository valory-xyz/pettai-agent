"""Emit additional PyInstaller CLI options for Pett Agent hidden imports."""

from __future__ import annotations

# LangChain and other libs lazily import modules (e.g. pydantic.deprecated.decorator);
# they must be listed explicitly so PyInstaller bundles them which is the case of pydantic.deprecated.decorator.


def main() -> None:
    """Print hidden-import flags for PyInstaller."""
    hidden_imports = [
        "agent.olas_interface",
        "agent.pett_agent",
        "agent.decision_engine",
        "agent.telegram_bot",
        "agent.pett_tools",
        "agent.pett_websocket_client",
        "agent.backend_chat_model",
        "agent.daily_action_tracker",
        "agent.staking_checkpoint",
        "agent.action_recorder",
        "agent.agent_performance",
        "agent.nonce_utils",
        "agent.react_server_manager",
        # pkg_resources vendored dependencies (required when setuptools loads)
        "jaraco",
        "jaraco.text",
        "jaraco.functools",
        "jaraco.collections",
        "jaraco.context",
        # jaraco.context imports backports.tarfile at runtime
        "backports",
        "backports.tarfile",
        # Pydantic runtime imports requested via import_string (see above)
        "pydantic.deprecated.decorator",
    ]
    flags = " ".join(f"--hidden-import {module}" for module in hidden_imports)
    print(flags)


if __name__ == "__main__":
    main()
