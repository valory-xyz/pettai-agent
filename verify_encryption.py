#!/usr/bin/env python3
"""
Simple verification script for session token encryption.
Tests the basic encryption/decryption functionality without requiring pytest.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Add olas-sdk-starter to path
sys.path.insert(0, str(Path(__file__).parent / "olas-sdk-starter"))

from agent.pett_websocket_client import PettWebSocketClient


def test_encryption_decryption():
    """Test basic encryption and decryption."""
    print("Test 1: Basic encryption/decryption...")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["STORE_PATH"] = tmpdir
        client = PettWebSocketClient(
            websocket_url="wss://test.example.com",
            session_token="test_token_12345"
        )
        
        original_token = "my_secret_session_token_xyz123"
        encrypted = client._encrypt_token(original_token)
        decrypted = client._decrypt_token(encrypted)
        
        assert encrypted != original_token, "Token should be encrypted"
        assert decrypted == original_token, "Decrypted token should match original"
        print("  ✓ Encryption/decryption works correctly")


def test_persistence():
    """Test token persistence with encryption."""
    print("\nTest 2: Encrypted token persistence...")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["STORE_PATH"] = tmpdir
        
        # Create client and persist token
        client1 = PettWebSocketClient(
            websocket_url="wss://test.example.com",
            session_token="persistent_test_token"
        )
        client1._persist_session_token()
        
        # Check file contents
        token_file = client1._session_store_path
        with open(token_file, "r") as f:
            data = json.load(f)
        
        assert "encryptedSessionToken" in data, "Should have encrypted field"
        assert "sessionToken" not in data, "Should not have plaintext field"
        assert data["encryptedSessionToken"] != "persistent_test_token", "Should be encrypted"
        print("  ✓ Token persisted in encrypted format")
        
        # Create new client and load token
        client2 = PettWebSocketClient(websocket_url="wss://test.example.com")
        
        assert client2.session_token == "persistent_test_token", "Token should be loaded"
        print("  ✓ Encrypted token loaded successfully")


def test_legacy_format():
    """Test loading legacy plaintext tokens."""
    print("\nTest 3: Legacy plaintext token compatibility...")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["STORE_PATH"] = tmpdir
        
        # Create a legacy plaintext token file
        client = PettWebSocketClient(websocket_url="wss://test.example.com")
        token_file = client._session_store_path
        token_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(token_file, "w") as f:
            json.dump({"sessionToken": "legacy_plaintext_token"}, f)
        
        # Load with new client
        loaded_token, _ = client._load_persisted_session_token()
        
        assert loaded_token == "legacy_plaintext_token", "Legacy token should be loaded"
        print("  ✓ Legacy plaintext token loaded successfully")


def test_auth_candidates():
    """Test authentication candidate ordering with fallback."""
    print("\nTest 4: Authentication candidate ordering...")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["STORE_PATH"] = tmpdir
        
        # Test with both session and Privy tokens
        client = PettWebSocketClient(
            websocket_url="wss://test.example.com",
            session_token="session_token_123",
            privy_token="privy_token_456"
        )
        
        candidates = client._get_auth_candidates()
        
        assert len(candidates) >= 2, "Should have both tokens"
        
        token_types = [auth_type for auth_type, _, _ in candidates]
        assert "session" in token_types, "Should include session token"
        assert "privy" in token_types, "Should include Privy token"
        
        # Session should be first
        assert candidates[0][0] == "session", "Session should be tried first"
        print("  ✓ Session token prioritized, Privy as fallback")
        
        # Test with only Privy token
        client2 = PettWebSocketClient(
            websocket_url="wss://test.example.com",
            privy_token="privy_only_token"
        )
        
        candidates2 = client2._get_auth_candidates()
        assert len(candidates2) >= 1, "Should have Privy token"
        assert candidates2[0][0] == "privy", "Should use Privy when no session"
        print("  ✓ Privy used when no session token available")


def test_key_determinism():
    """Test that encryption key is deterministic."""
    print("\nTest 5: Encryption key determinism...")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["STORE_PATH"] = tmpdir
        
        client = PettWebSocketClient(websocket_url="wss://test.example.com")
        
        key1 = client._get_encryption_key()
        key2 = client._get_encryption_key()
        
        assert key1 == key2, "Encryption key should be deterministic"
        assert len(key1) == 44, "Key should be 44 bytes (base64-encoded 32 bytes)"
        print("  ✓ Encryption key is deterministic and correct length")


def main():
    """Run all verification tests."""
    print("=" * 60)
    print("Session Token Encryption Verification")
    print("=" * 60)
    
    try:
        test_encryption_decryption()
        test_persistence()
        test_legacy_format()
        test_auth_candidates()
        test_key_determinism()
        
        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
        return 0
        
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

