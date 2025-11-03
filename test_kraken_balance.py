#!/usr/bin/env python3
"""
Test script for Kraken balance API
Tests the nonce generation and balance retrieval functionality

Usage:
    cd backend && python3 ../test_kraken_balance.py
"""

import sys
import os

# Add current directory and parent directory to path
backend_dir = os.path.join(os.path.dirname(__file__), 'backend')
if os.path.exists(backend_dir):
    sys.path.insert(0, backend_dir)
sys.path.insert(0, os.path.dirname(__file__))

try:
    from decimal import Decimal
    from database.connection import SessionLocal
    from database.models import Account
    from services.kraken_sync import get_kraken_balance_real_time, get_kraken_balance_and_positions
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("\nPlease run this script from the backend directory:")
    print("  cd backend && python3 ../test_kraken_balance.py")
    sys.exit(1)

def test_kraken_balance():
    """Test Kraken balance retrieval"""
    print("=" * 60)
    print("Testing Kraken Balance API")
    print("=" * 60)
    
    # Get database session
    db = SessionLocal()
    try:
        # Get first active account with Kraken API keys
        account = db.query(Account).filter(
            Account.is_active == "true",
            Account.kraken_api_key.isnot(None),
            Account.kraken_private_key.isnot(None)
        ).first()
        
        if not account:
            print("❌ No active account with Kraken API keys found")
            print("\nPlease configure Kraken API keys for an account:")
            print("1. Go to the account settings")
            print("2. Add Kraken API Key and Private Key")
            print("3. Ensure the account is active")
            return False
        
        print(f"\n✓ Found account: {account.name} (ID: {account.id})")
        print(f"  API Key: {account.kraken_api_key[:10]}...{account.kraken_api_key[-4:]}")
        print(f"  Private Key: {'*' * 20}...{account.kraken_private_key[-4:]}")
        
        # Test 1: Get balance only
        print("\n" + "-" * 60)
        print("Test 1: Get balance only")
        print("-" * 60)
        try:
            balance = get_kraken_balance_real_time(account)
            if balance is not None:
                print(f"✓ Balance retrieved successfully: ${balance:,.2f}")
            else:
                print("⚠ Balance is None (account may have no balance or API error)")
        except Exception as e:
            print(f"❌ Error getting balance: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # Test 2: Get balance and positions together
        print("\n" + "-" * 60)
        print("Test 2: Get balance and positions together")
        print("-" * 60)
        try:
            balance, positions = get_kraken_balance_and_positions(account)
            if balance is not None:
                print(f"✓ Balance: ${balance:,.2f}")
            else:
                print("⚠ Balance is None")
            
            if positions:
                print(f"✓ Found {len(positions)} positions:")
                for pos in positions:
                    print(f"  - {pos.get('symbol', 'N/A')}: {pos.get('quantity', 0):.8f} "
                          f"(avg_cost: ${pos.get('avg_cost', 0):.2f})")
            else:
                print("⚠ No positions found")
        except Exception as e:
            print(f"❌ Error getting balance and positions: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # Test 3: Test nonce generation (multiple rapid calls)
        print("\n" + "-" * 60)
        print("Test 3: Test nonce uniqueness (rapid calls)")
        print("-" * 60)
        try:
            from kraken.kraken_request import get_nonce
            nonces = []
            for i in range(5):
                nonce = get_nonce()
                nonces.append(nonce)
                print(f"  Call {i+1}: nonce = {nonce}")
            
            # Check uniqueness
            if len(nonces) == len(set(nonces)):
                print("✓ All nonces are unique")
            else:
                print("❌ Found duplicate nonces!")
                return False
            
            # Check monotonicity
            is_increasing = all(int(nonces[i]) < int(nonces[i+1]) for i in range(len(nonces)-1))
            if is_increasing:
                print("✓ All nonces are strictly increasing")
            else:
                print("❌ Nonces are not strictly increasing!")
                return False
        except Exception as e:
            print(f"❌ Error testing nonce generation: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        print("\n" + "=" * 60)
        print("✓ All tests passed!")
        print("=" * 60)
        return True
        
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()

if __name__ == "__main__":
    success = test_kraken_balance()
    sys.exit(0 if success else 1)

