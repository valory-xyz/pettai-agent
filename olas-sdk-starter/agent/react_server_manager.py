"""
React Development Server Manager
Manages React dev server lifecycle from Python
"""

import asyncio
import logging
import os
import subprocess
import signal
from pathlib import Path
from typing import Optional
import time
import aiohttp
import socket

logger = logging.getLogger(__name__)


class ReactServerManager:
    """Manages React development server as a subprocess."""

    def __init__(self, react_dir: str = "frontend", port: int = 8716):
        self.react_dir = Path(react_dir)
        self.port = port
        self.process: Optional[subprocess.Popen] = None
        self.is_running = False

    async def ensure_dependencies(self) -> bool:
        """Install npm/yarn dependencies if needed."""
        try:
            node_modules = self.react_dir / "node_modules"
            package_json = self.react_dir / "package.json"

            if not package_json.exists():
                logger.error(f"❌ No package.json found in {self.react_dir}")
                return False

            # Check if node_modules exists and is recent
            if node_modules.exists():
                logger.info("✅ node_modules already exists, skipping install")
                return True

            logger.info("📦 Installing npm dependencies...")

            # Try yarn first, fallback to npm
            install_cmd = None
            if self._command_exists("yarn"):
                install_cmd = ["yarn", "install"]
                logger.info("Using yarn for installation")
            elif self._command_exists("npm"):
                install_cmd = ["npm", "install"]
                logger.info("Using npm for installation")
            else:
                logger.error("❌ Neither yarn nor npm found in PATH")
                return False

            # Run installation
            process = await asyncio.create_subprocess_exec(
                *install_cmd,
                cwd=str(self.react_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                logger.info("✅ Dependencies installed successfully")
                return True
            else:
                logger.error(f"❌ Dependency installation failed: {stderr.decode()}")
                return False

        except Exception as e:
            logger.error(f"❌ Error installing dependencies: {e}")
            return False

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

    async def _wait_for_server_ready(self, max_wait: int = 60) -> bool:
        """Wait for React dev server to be ready and serving content."""
        logger.info(f"⏳ Waiting for React dev server to be ready (max {max_wait}s)...")
        start_time = time.time()

        while time.time() - start_time < max_wait:
            try:
                # Try to connect to the server
                timeout = aiohttp.ClientTimeout(total=2)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(f"http://localhost:{self.port}/") as resp:
                        if resp.status == 200:
                            elapsed = time.time() - start_time
                            logger.info(
                                f"✅ React dev server ready after {elapsed:.1f}s"
                            )
                            return True
            except (aiohttp.ClientError, asyncio.TimeoutError):
                # Server not ready yet, wait and retry
                pass

            await asyncio.sleep(1)

        logger.error(f"❌ React dev server did not become ready within {max_wait}s")
        return False

    async def start_dev_server(self) -> bool:
        """Start the React development server."""
        try:
            if self.is_running:
                logger.warning("⚠️ React dev server already running")
                return True

            # Ensure dependencies are installed
            if not await self.ensure_dependencies():
                return False

            # Choose an available port proactively to avoid interactive prompts
            chosen_port = self._select_available_port(self.port)
            if chosen_port != self.port:
                logger.info(
                    f"🔀 Port {self.port} is busy, switching React dev server to {chosen_port}"
                )
                self.port = chosen_port

            logger.info(f"🚀 Starting React dev server on port {self.port}...")

            # Determine start command
            start_cmd = None
            if (self.react_dir / "yarn.lock").exists():
                start_cmd = ["yarn", "start"]
            else:
                start_cmd = ["npm", "start"]

            # Set environment variables for React dev server
            env = os.environ.copy()
            env["PORT"] = str(self.port)
            env["HOST"] = "0.0.0.0"  # Bind to all interfaces for Docker access
            env["BROWSER"] = "none"  # Don't auto-open browser
            env["CI"] = "false"  # Disable CI mode warnings
            env["GENERATE_SOURCEMAP"] = "false"  # Suppress source map warnings

            # Start the process
            self.process = subprocess.Popen(
                start_cmd,
                cwd=str(self.react_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid if os.name != "nt" else None,
            )

            # Start background task to monitor output
            asyncio.create_task(self._monitor_output())

            # Wait for server to be ready and serving content
            if await self._wait_for_server_ready(max_wait=60):
                # Check if process is still running
                if self.process.poll() is None:
                    self.is_running = True
                    logger.info(
                        f"✅ React dev server started on http://localhost:{self.port}"
                    )
                    return True
                else:
                    logger.error("❌ React dev server process died")
                    return False
            else:
                logger.error("❌ React dev server failed to become ready")
                if self.process.poll() is None:
                    self.process.terminate()
                return False

        except Exception as e:
            logger.error(f"❌ Error starting React dev server: {e}")
            return False

    async def _monitor_output(self):
        """Monitor React dev server output."""
        try:
            if not self.process or not self.process.stdout:
                return

            while self.is_running and self.process.poll() is None:
                line = self.process.stdout.readline()
                if line:
                    decoded = line.decode().strip()
                    if decoded:
                        # Filter out verbose webpack logs
                        if any(
                            x in decoded.lower()
                            for x in ["compiled", "error", "warning", "ready"]
                        ):
                            logger.info(f"[React] {decoded}")
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.debug(f"Output monitoring stopped: {e}")

    async def stop_dev_server(self):
        """Stop the React development server."""
        try:
            if not self.is_running or not self.process:
                return

            logger.info("🛑 Stopping React dev server...")
            self.is_running = False

            # Send SIGTERM to process group
            if os.name != "nt":
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            else:
                self.process.terminate()

            # Wait for graceful shutdown
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("⚠️ Forcing React dev server shutdown...")
                if os.name != "nt":
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                else:
                    self.process.kill()

            logger.info("✅ React dev server stopped")

        except Exception as e:
            logger.error(f"❌ Error stopping React dev server: {e}")

    def get_status(self) -> dict:
        """Get current status of React dev server."""
        return {
            "running": self.is_running,
            "port": self.port,
            "url": f"http://localhost:{self.port}" if self.is_running else None,
            "process_alive": self.process.poll() is None if self.process else False,
        }

    def _port_is_in_use(self, port: int) -> bool:
        """Check if a TCP port on localhost is already in use."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.settimeout(0.5)
            result = s.connect_ex(("127.0.0.1", port))
            return result == 0

    def _try_bind_port(self, port: int) -> Optional[socket.socket]:
        """Atomically attempt to bind to a port. Returns bound socket if successful, None otherwise."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            s.listen(128)  # Put socket in listen state to fully reserve the port
            return s
        except OSError:
            # Port is in use or bind failed
            return None

    def _select_available_port(self, preferred: int) -> int:
        """Pick an available port, preferring the given one; if occupied, scan upward for next available.

        Uses atomic binding to avoid TOCTOU race conditions - ports are reserved by binding
        rather than just checking availability.
        """
        # Try to atomically bind to the preferred port
        bound_socket = self._try_bind_port(preferred)
        if bound_socket is not None:
            port = preferred
            bound_socket.close()  # Close immediately - minimal race window before React server starts
            logger.info(f"✅ Using available port {port}")
            return port

        # Preferred port is occupied, scan upward starting from the next port
        logger.info(
            f"🔍 Port {preferred} is occupied, scanning for next available port..."
        )
        start = preferred + 1
        # Ensure we don't go below a reasonable minimum
        if start < 3000:
            start = 3000

        # Scan a range for an open port (check up to 200 ports ahead)
        # Use atomic binding instead of checking first
        for candidate in range(start, start + 200):
            bound_socket = self._try_bind_port(candidate)
            if bound_socket is not None:
                port = candidate
                bound_socket.close()  # Close immediately - minimal race window before React server starts
                logger.info(f"✅ Using available port {port}")
                return port

        # Fallback to an ephemeral port assigned by OS if everything else fails
        logger.warning(
            f"⚠️ No port found in range {start}-{start + 199}, using OS-assigned ephemeral port"
        )
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen(128)
        ephemeral_port = s.getsockname()[1]
        s.close()  # Close immediately - minimal race window before React server starts
        logger.info(f"✅ Using available port {ephemeral_port}")
        return ephemeral_port
