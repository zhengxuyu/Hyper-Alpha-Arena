"""
Kraken Data Synchronization Service
Synchronizes account balance, positions, and orders from Kraken API
"""
import json
import logging
import sys
import os
from decimal import Decimal
from typing import Dict, List, Optional

from database.connection import SessionLocal
from database.models import Account, Position, Order
from sqlalchemy.orm import Session

# Add kraken module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
logger = logging.getLogger(__name__)

try:
    from kraken.account import Account as KrakenAccount, get_balance, get_open_orders, get_trade_balance
    from kraken.token_map import map_kraken_asset_to_internal
    KRAKEN_AVAILABLE = True
except ImportError:
    KRAKEN_AVAILABLE = False
    logger.warning("Kraken module not available, Kraken sync disabled")


def _parse_kraken_balance(balance_data: str) -> Dict[str, float]:
    """Parse Kraken balance response and convert to internal format"""
    try:
        data = json.loads(balance_data)
        if data.get("error"):
            logger.error(f"Kraken balance API error: {data.get('error')}")
            return {}
        
        result = data.get("result", {})
        balances = {}
        
        for kraken_asset, amount_str in result.items():
            # Map Kraken asset to internal symbol
            asset = map_kraken_asset_to_internal(kraken_asset)
            
            try:
                amount = float(amount_str)
                if amount > 0:
                    balances[asset] = amount
            except (ValueError, TypeError):
                continue
        
        return balances
    except Exception as e:
        logger.error(f"Failed to parse Kraken balance: {e}", exc_info=True)
        return {}


def _parse_kraken_open_orders(orders_data: str) -> List[Dict]:
    """Parse Kraken open orders response"""
    try:
        data = json.loads(orders_data)
        if data.get("error"):
            logger.error(f"Kraken open orders API error: {data.get('error')}")
            return []
        
        result = data.get("result", {})
        orders = result.get("open", {})
        order_list = []
        
        for txid, order_info in orders.items():
            desc = order_info.get("descr", {})
            pair = desc.get("pair", "")
            order_type = desc.get("type", "")  # "buy" or "sell"
            ordertype = desc.get("ordertype", "")  # "market", "limit", etc.
            price = float(desc.get("price", 0) or 0)
            volume = float(order_info.get("vol", 0))
            
            # Map Kraken pair to internal symbol
            # Kraken pairs are like "XBTUSD", "ETHUSD", etc.
            # Remove "USD" suffix and map the asset symbol
            asset_symbol = pair.replace("USD", "")
            symbol = map_kraken_asset_to_internal(asset_symbol)
            
            order_list.append({
                "txid": txid,
                "symbol": symbol,
                "side": "BUY" if order_type == "buy" else "SELL",
                "order_type": ordertype.upper(),
                "price": price,
                "quantity": volume,
                "status": "OPEN",
            })
        
        return order_list
    except Exception as e:
        logger.error(f"Failed to parse Kraken open orders: {e}", exc_info=True)
        return []


def _sync_balance_from_kraken(db: Session, account: Account) -> bool:
    """Sync account balance from Kraken"""
    if not KRAKEN_AVAILABLE:
        logger.warning("Kraken module not available, cannot sync balance")
        return False
    
    # Check if account has API keys configured
    if not account.api_key or not account.base_url:
        logger.warning(f"Account {account.name} does not have API keys configured, cannot sync from Kraken")
        return False
    
    try:
        # Use account's API keys instead of global config
        from kraken.kraken_request import request
        
        # Get balance using account's API keys (base_url stores PVI/private key)
        balance_response = request(
            method="POST",
            path="/0/private/Balance",
            public_key=account.api_key,
            private_key=account.base_url,  # PVI stored in base_url
            environment="https://api.kraken.com",
        )
        balance_data = balance_response.read().decode()
        balances = _parse_kraken_balance(balance_data)
        
        # Find USD balance (could be ZUSD or USD)
        usd_balance = 0.0
        for asset, amount in balances.items():
            if asset.upper() in ["USD", "ZUSD"]:
                usd_balance += amount
        
        # Also check trade balance which shows marginable funds
        try:
            from kraken.kraken_request import request as kraken_request
            trade_balance_response = kraken_request(
                method="POST",
                path="/0/private/TradeBalance",
                public_key=account.api_key,
                private_key=account.base_url,  # PVI stored in base_url
                environment="https://api.kraken.com",
            )
            trade_balance_data = trade_balance_response.read().decode()
            trade_data = json.loads(trade_balance_data)
            if not trade_data.get("error"):
                trade_result = trade_data.get("result", {})
                # Trade balance shows "eb" (equivalent balance) which is USD
                eb = trade_result.get("eb", "0")
                try:
                    usd_balance = max(usd_balance, float(eb))
                except (ValueError, TypeError):
                    pass
        except Exception as e:
            logger.warning(f"Failed to get trade balance, using regular balance: {e}")
        
        # Update account cash
        if usd_balance >= 0:
            account.current_cash = Decimal(str(usd_balance))
            account.frozen_cash = Decimal('0')  # Could be enhanced to track frozen amounts
            logger.info(f"Synced balance for account {account.name}: ${usd_balance:.2f}")
            return True
        else:
            logger.warning(f"Invalid balance from Kraken: ${usd_balance}")
            return False
            
    except Exception as e:
        logger.error(f"Failed to sync balance from Kraken for account {account.name}: {e}", exc_info=True)
        return False


def _sync_positions_from_kraken(db: Session, account: Account) -> bool:
    """
    Sync positions from Kraken.
    When switching to real trading mode, clear all paper trading positions.
    Real positions will be tracked from actual Kraken trades.
    """
    try:
        # Clear ALL existing paper trading positions for this account
        # Paper trading positions don't exist in real Kraken account
        deleted_count = db.query(Position).filter(Position.account_id == account.id).delete()
        logger.info(f"Cleared {deleted_count} paper trading positions for account {account.name} (switching to real mode)")
        
        # Real positions will be created from actual Kraken trades as they happen
        # For now, start with empty positions
        return True
        
    except Exception as e:
        logger.error(f"Failed to sync positions from Kraken for account {account.name}: {e}", exc_info=True)
        return False


def _sync_orders_from_kraken(db: Session, account: Account) -> bool:
    """Sync open orders from Kraken"""
    if not KRAKEN_AVAILABLE:
        logger.warning("Kraken module not available, cannot sync orders")
        return False
    
    # Check if account has API keys configured
    if not account.api_key or not account.base_url:
        logger.warning(f"Account {account.name} does not have API keys configured, cannot sync orders")
        return False
    
    try:
        # Use account's API keys instead of global config
        from kraken.kraken_request import request
        orders_response = request(
            method="POST",
            path="/0/private/OpenOrders",
            public_key=account.api_key,
            private_key=account.base_url,  # PVI stored in base_url
            environment="https://api.kraken.com",
        )
        orders_data = orders_response.read().decode()
        kraken_orders = _parse_kraken_open_orders(orders_data)
        
        # Clear ALL paper trading orders for this account (not just pending ones)
        # Cancel pending/partial orders
        pending_orders = db.query(Order).filter(
            Order.account_id == account.id,
            Order.status.in_(["PENDING", "PARTIAL"])
        ).all()
        for order in pending_orders:
            order.status = "CANCELLED"
        
        # Also cancel all other paper trading orders (those not already cancelled/filled)
        # When switching to real mode, all paper trading orders should be cleared
        paper_orders = db.query(Order).filter(
            Order.account_id == account.id,
            Order.status.notin_(["CANCELLED", "FILLED"])
        ).all()
        for order in paper_orders:
            if order not in pending_orders:  # Don't double-count
                order.status = "CANCELLED"
        
        total_cancelled = len(set(pending_orders + paper_orders))
        logger.info(f"Cancelled {total_cancelled} paper trading orders for account {account.name}")
        
        # Create new order records from Kraken
        for kraken_order in kraken_orders:
            # Check if order already exists (by some identifier)
            # For now, create new orders
            order = Order(
                account_id=account.id,
                symbol=kraken_order["symbol"],
                name=kraken_order["symbol"],  # Could map to full name
                side=kraken_order["side"],
                order_type=kraken_order["order_type"],
                price=Decimal(str(kraken_order["price"])) if kraken_order["price"] > 0 else None,
                quantity=Decimal(str(kraken_order["quantity"])),
                status="PENDING",
                market="CRYPTO",
            )
            db.add(order)
        
        logger.info(f"Synced {len(kraken_orders)} open orders from Kraken for account {account.name}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to sync orders from Kraken for account {account.name}: {e}", exc_info=True)
        return False


def sync_account_from_kraken(db: Session, account: Account) -> Dict[str, bool]:
    """
    Synchronize account data from Kraken API.
    Note: db should be from the account's trade_mode database (real trading DB).
    
    Returns:
        Dict with sync status for each component: {"balance": bool, "positions": bool, "orders": bool}
    """
    if account.trade_mode != "real":
        logger.info(f"Account {account.name} is not in real trading mode, skipping Kraken sync")
        return {"balance": False, "positions": False, "orders": False}
    
    results = {
        "balance": _sync_balance_from_kraken(db, account),
        "positions": _sync_positions_from_kraken(db, account),
        "orders": _sync_orders_from_kraken(db, account),
    }
    
    # Commit changes and refresh account object to ensure we see latest data
    db.commit()
    db.refresh(account, ['current_cash', 'frozen_cash', 'trade_mode'])
    
    logger.info(f"Sync completed for account {account.name}: balance={results.get('balance')}, positions={results.get('positions')}, orders={results.get('orders')}")
    
    return results

