#!/usr/bin/env python3
"""
Test script for enhanced Pett Agent UI with WebSocket and Pet status
"""

import asyncio
import sys
import os
from pathlib import Path

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def test_enhanced_health_check():
    """Test the enhanced health check endpoint with WebSocket and Pet info."""
    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:8716/healthcheck") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print("✅ Enhanced health check passed:")
                    print(f"   Status: {data.get('status')}")
                    print(
                        f"   Seconds since transition: {data.get('seconds_since_last_transition')}"
                    )
                    print(f"   Is transitioning: {data.get('is_transitioning_fast')}")

                    # Check WebSocket info
                    websocket_info = data.get("websocket", {})
                    print(f"   WebSocket URL: {websocket_info.get('url')}")
                    print(f"   WebSocket Connected: {websocket_info.get('connected')}")
                    print(
                        f"   WebSocket Authenticated: {websocket_info.get('authenticated')}"
                    )
                    print(
                        f"   Last Activity: {websocket_info.get('last_activity_seconds_ago')}s ago"
                    )

                    # Check Pet info
                    pet_info = data.get("pet", {})
                    print(f"   Pet Connected: {pet_info.get('connected')}")
                    print(f"   Pet Status: {pet_info.get('status')}")

                    return True
                else:
                    print(f"❌ Health check failed with status {resp.status}")
                    return False
    except Exception as e:
        print(f"❌ Health check error: {e}")
        return False


async def test_enhanced_ui():
    """Test the enhanced agent UI endpoint."""
    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:8716/") as resp:
                if resp.status == 200:
                    content = await resp.text()

                    # Check for WebSocket info in HTML
                    if "WebSocket Connection" in content and "🔌" in content:
                        print("✅ Enhanced UI shows WebSocket connection info")
                    else:
                        print("⚠️ WebSocket info not found in UI")

                    # Check for Pet info in HTML
                    if "Pet Connection" in content and "🐾" in content:
                        print("✅ Enhanced UI shows Pet connection info")
                    else:
                        print("⚠️ Pet info not found in UI")

                    # Check for status indicators
                    if "🟢" in content or "🔴" in content:
                        print("✅ Enhanced UI shows status indicators")
                    else:
                        print("⚠️ Status indicators not found in UI")

                    return True
                else:
                    print(f"❌ Agent UI failed with status {resp.status}")
                    return False
    except Exception as e:
        print(f"❌ Agent UI error: {e}")
        return False


async def main():
    """Run enhanced UI tests."""
    print("🧪 Testing Enhanced Pett Agent UI with WebSocket & Pet Status\n")

    # Wait a moment for agent to start
    print("⏳ Waiting for agent to start...")
    await asyncio.sleep(3)

    # Test enhanced health check
    health_ok = await test_enhanced_health_check()
    print()

    # Test enhanced UI
    ui_ok = await test_enhanced_ui()
    print()

    # Summary
    if health_ok and ui_ok:
        print("🎉 Enhanced UI tests passed! WebSocket and Pet status are now visible.")
        print("🌐 Visit http://localhost:8716/ to see the enhanced dashboard")
        return True
    else:
        print("❌ Some enhanced UI tests failed.")
        return False


if __name__ == "__main__":
    try:
        result = asyncio.run(main())
        sys.exit(0 if result else 1)
    except KeyboardInterrupt:
        print("\n🛑 Tests interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"💥 Test error: {e}")
        sys.exit(1)
