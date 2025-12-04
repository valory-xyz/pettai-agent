"""Utility script invoked by `make check-agent-runner`."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from typing import Tuple


def _read_config() -> Tuple[str, int, str]:
    """Read command configuration from the environment."""
    command = os.environ.get("CHECK_COMMAND", "./dist/agent_runner_bin --version")
    timeout = int(os.environ.get("CHECK_TIMEOUT", "30"))
    search = os.environ.get("CHECK_SEARCH_STRING", "Pett Agent Runner")
    return command, timeout, search


def main() -> None:
    """Run the configured command and ensure it prints the search string."""
    command, timeout, search = _read_config()
    print(f"Running validation command: {command}")
    args = shlex.split(command, posix=(os.name != "nt"))
    print(args)
    process = subprocess.Popen(  # noqa: S603,S607 (controlled input)
        args,
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, _ = process.communicate()
        print(stdout)
        raise SystemExit(f"Command timed out after {timeout}s") from None

    if process.returncode != 0:
        print(stdout)
        raise SystemExit(process.returncode)

    if search not in stdout:
        print(stdout)
        raise SystemExit(f"Did not find '{search}' in command output")

    print("Agent runner binary validation succeeded.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        sys.exit(exc.code)
