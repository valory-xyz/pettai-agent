#!/usr/bin/env python3
"""
Test script to demonstrate the enhanced UI with actual pet data.
"""

import sys
import os
import asyncio
from datetime import datetime

sys.path.append(os.path.join(os.path.dirname(__file__), "agent"))

from olas_interface import OlasInterface
import logging


async def test_pet_data_ui():
    """Test the enhanced UI with sample pet data."""

    print("🧪 Testing enhanced UI with pet data...")

    logger = logging.getLogger("test_pet_data_ui")
    ethereum_private_key = os.environ.get("ETH_PRIVATE_KEY")
    if not ethereum_private_key:
        logger.warning(
            "ETH_PRIVATE_KEY not set; using demo placeholder which cannot sign real txs."
        )
        ethereum_private_key = "demo_eth_private_key"

    # Create Olas interface
    olas = OlasInterface(
        ethereum_private_key=ethereum_private_key,
        withdrawal_mode=False,
        logger=logger,
    )

    # Update with sample pet data
    sample_pet_data = {
        "name": "Fluffy",
        "id": "pet_12345",
        "PetTokens": {"tokens": "1500000000000000000"},  # 1.5 ETH in wei
        "currentHotelTier": 3,
        "dead": False,
        "sleeping": True,
        "stats": {"health": 85, "happiness": 92, "hunger": 15},
    }

    print("📊 Updating with sample pet data...")
    olas.update_pet_data(sample_pet_data)
    olas.update_pet_status(connected=True, status="Active")
    olas.update_websocket_status(connected=True, authenticated=True)

    # Start web server
    print("🌐 Starting web server...")
    await olas.start_web_server(port=8717)  # Use different port to avoid conflicts

    print("✅ Enhanced UI is now running!")
    print("🎛️  Open your browser to: http://localhost:8717/")
    print("🏥 Health check: http://localhost:8717/healthcheck")
    print("\n📋 Pet data displayed:")
    print(f"   Name: {olas.pet_name}")
    print(f"   ID: {olas.pet_id}")
    print(f"   Balance: {olas.pet_balance} $AIP")
    print(f"   Hotel Tier: {olas.pet_hotel_tier}")
    print(f"   Dead: {olas.pet_dead}")
    print(f"   Sleeping: {olas.pet_sleeping}")
    print(f"   Connected: {olas.pet_connected}")

    print("\n⏳ Server running... Press Ctrl+C to stop")

    try:
        # Keep server running
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Stopping server...")
        await olas.stop_web_server()
        print("✅ Server stopped!")


if __name__ == "__main__":
    asyncio.run(test_pet_data_ui())
