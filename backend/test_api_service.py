#!/usr/bin/env python3
"""Test script for API service verification."""
import sys
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.api_service import (
    APIService,
    NetworkError,
    ExchangeError,
)

# API credentials from terminal
BYBIT_API_KEY = "l0lTMRRmhGrDdOemhp"
BYBIT_API_SECRET = "dObil7ZUKkX2Rcs1FVVARDlCaw7UKzfKevi0"
BINANCE_API_KEY = "tBIClmIB5LYVOwifAsMpHDnOEWi7rQgBJxJmf7yyv2mBM4y78aIJmaUTcAqPHE6M"
BINANCE_API_SECRET = "3xHqNeBM4BAwglJQCdNhnudoZv1Nh99ezUm1x7nEGla2lnUfspq3YIjq2MMdehS2"


def test_binance():
    """Test Binance API verification."""
    print("\n" + "="*60)
    print("Testing Binance API Verification")
    print("="*60)
    
    service = APIService()
    
    try:
        service.verify_api(
            exchange='BINANCE',
            api_key=BINANCE_API_KEY,
            api_secret=BINANCE_API_SECRET,
            passphrase=None
        )
        print("✓ Binance API verification: SUCCESS")
        print(f"  API Key: {BINANCE_API_KEY[:20]}...")
        return True
        
    except NetworkError as e:
        print(f"✗ Binance API verification: FAILED - Network Error")
        print(f"  Error: {e.message}")
        return False
    except ExchangeError as e:
        print(f"✗ Binance API verification: FAILED - Exchange Error")
        print(f"  Error: {e.message}")
        if e.error_code is not None:
            print(f"  Error Code: {e.error_code}")
        return False
    except Exception as e:
        print(f"✗ Binance API verification: UNEXPECTED ERROR")
        print(f"  Error: {str(e)}")
        print(f"  Type: {type(e).__name__}")
        return False


def test_bybit():
    """Test Bybit API verification."""
    print("\n" + "="*60)
    print("Testing Bybit API Verification")
    print("="*60)
    
    service = APIService()
    
    try:
        service.verify_api(
            exchange='BYBIT',
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
            passphrase=None
        )
        print("✓ Bybit API verification: SUCCESS")
        print(f"  API Key: {BYBIT_API_KEY[:20]}...")
        return True
        
    except NetworkError as e:
        print(f"✗ Bybit API verification: FAILED - Network Error")
        print(f"  Error: {e.message}")
        return False
    except ExchangeError as e:
        print(f"✗ Bybit API verification: FAILED - Exchange Error")
        print(f"  Error: {e.message}")
        if e.error_code is not None:
            print(f"  Error Code: {e.error_code}")
        return False
    except Exception as e:
        print(f"✗ Bybit API verification: UNEXPECTED ERROR")
        print(f"  Error: {str(e)}")
        print(f"  Type: {type(e).__name__}")
        return False


def test_direct_methods():
    """Test direct verification methods."""
    print("\n" + "="*60)
    print("Testing Direct Verification Methods")
    print("="*60)
    
    service = APIService()
    
    # Test Binance direct method
    print("\n1. Testing verify_binance_api() directly:")
    try:
        service.verify_binance_api(BINANCE_API_KEY, BINANCE_API_SECRET)
        print(f"   Result: ✓ SUCCESS")
    except NetworkError as e:
        print(f"   Result: ✗ NetworkError - {e.message}")
    except ExchangeError as e:
        error_info = f"{e.message}"
        if e.error_code is not None:
            error_info += f" (code: {e.error_code})"
        print(f"   Result: ✗ ExchangeError - {error_info}")
    except Exception as e:
        print(f"   Result: ✗ UNEXPECTED ERROR - {str(e)}")
    
    # Test Bybit direct method
    print("\n2. Testing verify_bybit_api() directly:")
    try:
        service.verify_bybit_api(BYBIT_API_KEY, BYBIT_API_SECRET)
        print(f"   Result: ✓ SUCCESS")
    except NetworkError as e:
        print(f"   Result: ✗ NetworkError - {e.message}")
    except ExchangeError as e:
        error_info = f"{e.message}"
        if e.error_code is not None:
            error_info += f" (code: {e.error_code})"
        print(f"   Result: ✗ ExchangeError - {error_info}")
    except Exception as e:
        print(f"   Result: ✗ UNEXPECTED ERROR - {str(e)}")


def test_invalid_exchange():
    """Test invalid exchange handling."""
    print("\n" + "="*60)
    print("Testing Invalid Exchange Handling")
    print("="*60)
    
    service = APIService()
    
    try:
        service.verify_api(
            exchange='INVALID',  # type: ignore
            api_key="test",
            api_secret="test"
        )
        print("✗ Should have raised ValueError for invalid exchange")
        return False
    except ValueError as e:
        print(f"✓ Correctly raised ValueError: {str(e)}")
        return True
    except Exception as e:
        print(f"✗ Unexpected error: {str(e)}")
        return False


def test_invalid_credentials():
    """Test exception handling with invalid credentials."""
    print("\n" + "="*60)
    print("Testing Exception Handling with Invalid Credentials")
    print("="*60)
    
    service = APIService()
    
    # Test Binance with invalid credentials
    print("\n1. Testing Binance with invalid API key:")
    try:
        service.verify_binance_api("invalid_key", "invalid_secret")
        print("   ✗ Should have raised ExchangeError")
    except NetworkError as e:
        print(f"   ✓ Raised NetworkError: {e.message}")
    except ExchangeError as e:
        error_info = f"{e.message}"
        if e.error_code is not None:
            error_info += f" (code: {e.error_code})"
        print(f"   ✓ Correctly raised ExchangeError: {error_info}")
    except Exception as e:
        print(f"   ? Raised unexpected exception: {type(e).__name__}: {str(e)}")
    
    # Test Bybit with invalid credentials
    print("\n2. Testing Bybit with invalid API key:")
    try:
        service.verify_bybit_api("invalid_key", "invalid_secret")
        print("   ✗ Should have raised ExchangeError")
    except NetworkError as e:
        print(f"   ✓ Raised NetworkError: {e.message}")
    except ExchangeError as e:
        error_info = f"{e.message}"
        if e.error_code is not None:
            error_info += f" (code: {e.error_code})"
        print(f"   ✓ Correctly raised ExchangeError: {error_info}")
    except Exception as e:
        print(f"   ? Raised unexpected exception: {type(e).__name__}: {str(e)}")
    
    return True


def test_binance_balance():
    """Test Binance balance retrieval."""
    print("\n" + "="*60)
    print("Testing Binance Balance Retrieval")
    print("="*60)
    
    service = APIService()
    
    try:
        balance = service.get_balance(
            exchange='BINANCE',
            api_key=BINANCE_API_KEY,
            api_secret=BINANCE_API_SECRET,
            passphrase=None
        )
        print("✓ Binance balance retrieval: SUCCESS")
        print(f"  Found {len(balance)} wallet(s)")
        for wallet in balance:
            wallet_type = wallet.get('wallet', 'UNKNOWN')
            balances = wallet.get('balance', [])
            print(f"  - {wallet_type}: {len(balances)} token(s)")
            # Show first 3 tokens as sample
            for token_balance in balances[:3]:
                token = token_balance.get('token', '')
                total = token_balance.get('total', '0')
                print(f"    • {token}: {total}")
            if len(balances) > 3:
                print(f"    ... and {len(balances) - 3} more")
        return True
        
    except NetworkError as e:
        print(f"✗ Binance balance retrieval: FAILED - Network Error")
        print(f"  Error: {e.message}")
        return False
    except ExchangeError as e:
        print(f"✗ Binance balance retrieval: FAILED - Exchange Error")
        print(f"  Error: {e.message}")
        if e.error_code is not None:
            print(f"  Error Code: {e.error_code}")
        return False
    except Exception as e:
        print(f"✗ Binance balance retrieval: UNEXPECTED ERROR")
        print(f"  Error: {str(e)}")
        print(f"  Type: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        return False


def test_bybit_balance():
    """Test Bybit balance retrieval."""
    print("\n" + "="*60)
    print("Testing Bybit Balance Retrieval")
    print("="*60)
    
    service = APIService()
    
    try:
        balance = service.get_balance(
            exchange='BYBIT',
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET,
            passphrase=None
        )
        print("✓ Bybit balance retrieval: SUCCESS")
        print(f"  Found {len(balance)} wallet(s)")
        for wallet in balance:
            wallet_type = wallet.get('wallet', 'UNKNOWN')
            balances = wallet.get('balance', [])
            print(f"  - {wallet_type}: {len(balances)} token(s)")
            # Show first 3 tokens as sample
            for token_balance in balances[:3]:
                token = token_balance.get('token', '')
                total = token_balance.get('total', '0')
                print(f"    • {token}: {total}")
            if len(balances) > 3:
                print(f"    ... and {len(balances) - 3} more")
        return True
        
    except NetworkError as e:
        print(f"✗ Bybit balance retrieval: FAILED - Network Error")
        print(f"  Error: {e.message}")
        return False
    except ExchangeError as e:
        print(f"✗ Bybit balance retrieval: FAILED - Exchange Error")
        print(f"  Error: {e.message}")
        if e.error_code is not None:
            print(f"  Error Code: {e.error_code}")
        return False
    except Exception as e:
        print(f"✗ Bybit balance retrieval: UNEXPECTED ERROR")
        print(f"  Error: {str(e)}")
        print(f"  Type: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        return False


def test_balance_format():
    """Test that balance format is correct."""
    print("\n" + "="*60)
    print("Testing Balance Format Validation")
    print("="*60)
    
    service = APIService()
    
    # Test Binance format
    print("\n1. Testing Binance balance format:")
    try:
        balance = service.get_balance(
            exchange='BINANCE',
            api_key=BINANCE_API_KEY,
            api_secret=BINANCE_API_SECRET
        )
        
        # Validate format
        if not isinstance(balance, list):
            print("   ✗ Balance is not a list")
            return False
        
        for wallet in balance:
            if 'wallet' not in wallet or 'balance' not in wallet:
                print("   ✗ Wallet missing 'wallet' or 'balance' key")
                return False
            
            wallet_type = wallet['wallet']
            if wallet_type not in ['SPOT', 'MARGIN', 'FUTURES', 'UNIFIED', 'FUNDING']:
                print(f"   ✗ Invalid wallet type: {wallet_type}")
                return False
            
            if not isinstance(wallet['balance'], list):
                print("   ✗ Balance is not a list")
                return False
            
            for token_balance in wallet['balance']:
                required_keys = ['token', 'total', 'frozen', 'available']
                for key in required_keys:
                    if key not in token_balance:
                        print(f"   ✗ Token balance missing '{key}' key")
                        return False
                    if not isinstance(token_balance[key], str):
                        print(f"   ✗ Token balance '{key}' is not a string")
                        return False
        
        print("   ✓ Binance balance format is correct")
        return True
        
    except Exception as e:
        print(f"   ✗ Error validating format: {str(e)}")
        return False
    
    # Test Bybit format
    print("\n2. Testing Bybit balance format:")
    try:
        balance = service.get_balance(
            exchange='BYBIT',
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET
        )
        
        # Validate format (same as Binance)
        if not isinstance(balance, list):
            print("   ✗ Balance is not a list")
            return False
        
        for wallet in balance:
            if 'wallet' not in wallet or 'balance' not in wallet:
                print("   ✗ Wallet missing 'wallet' or 'balance' key")
                return False
            
            wallet_type = wallet['wallet']
            if wallet_type not in ['SPOT', 'MARGIN', 'FUTURES', 'UNIFIED', 'FUNDING']:
                print(f"   ✗ Invalid wallet type: {wallet_type}")
                return False
            
            if not isinstance(wallet['balance'], list):
                print("   ✗ Balance is not a list")
                return False
            
            for token_balance in wallet['balance']:
                required_keys = ['token', 'total', 'frozen', 'available']
                for key in required_keys:
                    if key not in token_balance:
                        print(f"   ✗ Token balance missing '{key}' key")
                        return False
                    if not isinstance(token_balance[key], str):
                        print(f"   ✗ Token balance '{key}' is not a string")
                        return False
        
        print("   ✓ Bybit balance format is correct")
        return True
        
    except Exception as e:
        print(f"   ✗ Error validating format: {str(e)}")
        return False


if __name__ == "__main__":
    print("\n" + "="*60)
    print("API Service Test Suite")
    print("="*60)
    
    results = []
    
    # Test Binance
    results.append(("Binance", test_binance()))
    
    # Test Bybit
    results.append(("Bybit", test_bybit()))
    
    # Test direct methods
    test_direct_methods()
    
    # Test error handling
    results.append(("Invalid Exchange", test_invalid_exchange()))
    
    # Test exception handling
    test_invalid_credentials()
    
    # Test balance retrieval
    results.append(("Binance Balance", test_binance_balance()))
    results.append(("Bybit Balance", test_bybit_balance()))
    results.append(("Balance Format", test_balance_format()))
    
    # Summary
    print("\n" + "="*60)
    print("Test Summary")
    print("="*60)
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{name:20s}: {status}")
    
    all_passed = all(result for _, result in results)
    print("\n" + "="*60)
    if all_passed:
        print("All tests PASSED!")
    else:
        print("Some tests FAILED!")
    print("="*60 + "\n")
    
    sys.exit(0 if all_passed else 1)

