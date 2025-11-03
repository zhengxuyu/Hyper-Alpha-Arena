"""
Kraken Data Synchronization Service
Synchronizes account balance, positions, and orders from Kraken API
"""
import json
import logging
import sys
import os
import threading
import time
import urllib.error
from decimal import Decimal
from typing import Dict, List, Optional

from database.models import Account

# Add kraken module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
logger = logging.getLogger(__name__)

# Thread-safe cache for balance and positions
_cache_lock = threading.Lock()
_balance_positions_cache: Dict[str, tuple] = {}
_balance_positions_last_call_time: Dict[str, float] = {}

# Global rate limiter for all Kraken API calls (shared across all functions)
# Ensures minimum 10-second interval between ANY Kraken API call
_global_kraken_last_call_time: float = 0.0
_global_kraken_lock = threading.Lock()

try:
    from kraken.account import get_balance, get_open_orders, get_trade_balance, get_closed_orders
    from kraken.token_map import map_kraken_asset_to_internal, map_token
    from kraken.trade import add_order as kraken_add_order, cancel_order as kraken_cancel_order
    KRAKEN_AVAILABLE = True
except ImportError:
    KRAKEN_AVAILABLE = False
    logger.warning("Kraken module not available, Kraken sync disabled")
    get_closed_orders = None
    map_token = None
    kraken_add_order = None
    kraken_cancel_order = None


def get_kraken_balance_only(kraken_api_key: str, kraken_private_key: str) -> Optional[Decimal]:
    """
    Query Kraken balance using kraken.account.get_balance with custom API keys.
    Returns the USD balance as Decimal, or None if query fails.
    Includes rate limiting protection to avoid API errors.
    """
    if not KRAKEN_AVAILABLE:
        logger.warning("Kraken module not available, cannot query balance")
        return None
    
    if not kraken_api_key or not kraken_private_key:
        logger.warning("Kraken API keys not configured, cannot query balance")
        return None
    
    try:
        # Use global rate limiter for all Kraken API calls (ensures 10-second interval)
        from services.trading_commands import RATE_LIMIT_INTERVAL_SECONDS
        
        # Use global keyword to ensure we're modifying the module-level variable
        global _global_kraken_last_call_time
        
        current_time = time.time()
        min_interval = RATE_LIMIT_INTERVAL_SECONDS  # Use constant
        
        # Apply global rate limiting (thread-safe)
        with _global_kraken_lock:
            time_since_last_call = current_time - _global_kraken_last_call_time
            
            if time_since_last_call < min_interval:
                sleep_time = min_interval - time_since_last_call
                logger.info(f"Rate limiting: sleeping {sleep_time:.2f}s before Kraken balance API call (min interval: {min_interval}s)")
                _global_kraken_lock.release()
                try:
                    time.sleep(sleep_time)
                finally:
                    _global_kraken_lock.acquire()
                current_time = time.time()
            
            _global_kraken_last_call_time = current_time
        
        # Use kraken.account.get_balance with custom keys
        balance_data = get_balance(api_key=kraken_api_key, private_key=kraken_private_key)
        
        # Skip get_trade_balance to reduce API calls (can add it back if needed with more delay)
        # This reduces the number of API calls per balance query
        trade_balance_data = None
        
        # Parse balance using existing parser
        parsed_balances = _parse_kraken_balance(balance_data)
        
        # Find USD balance from parsed balances
        usd_balance = parsed_balances.get("USD", parsed_balances.get("ZUSD", 0.0))
        
        # Try to get from trade balance (more accurate)
        if trade_balance_data:
            try:
                trade_data = json.loads(trade_balance_data)
                if not trade_data.get("error"):
                    trade_result = trade_data.get("result", {})
                    eb = trade_result.get("eb", "0")
                    try:
                        eb_float = float(eb)
                        if eb_float > 0:
                            usd_balance = eb_float
                    except (ValueError, TypeError):
                        pass
            except Exception:
                pass
        
        if usd_balance >= 0:
            logger.debug(f"Queryed Kraken balance: ${usd_balance:.2f}")
            return Decimal(str(usd_balance))
        else:
            return None
            
    except urllib.error.HTTPError as e:
        if e.code == 403:
            logger.error(f"Kraken API authentication failed (403 Forbidden). Please check if the API key and private key are correct and have proper permissions. Error: {e}")
        else:
            logger.error(f"Kraken API HTTP error {e.code}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Failed to query balance from Kraken: {e}", exc_info=True)
        return None


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


def _get_kraken_balance_and_positions(account: Account) -> tuple[Optional[Decimal], List[Dict]]:
    """
    Internal function to fetch balance and positions from Kraken in a single API call.
    Returns tuple of (balance: Optional[Decimal], positions: List[Dict]).
    This reduces API calls by fetching both in one request.
    Uses thread-safe caching to avoid duplicate API calls within a short time window.
    """
    if not KRAKEN_AVAILABLE:
        logger.warning("Kraken module not available, cannot get balance and positions")
        return None, []
    
    if not account.kraken_api_key or not account.kraken_private_key:
        logger.debug(f"Account {account.name} (ID: {account.id}) does not have Kraken API keys configured")
        return None, []
    
    # Cache mechanism: store recent results per account to avoid duplicate calls
    # Use hash for cache key to avoid conflicts
    import hashlib
    from services.trading_commands import CACHE_TTL_SECONDS, RATE_LIMIT_INTERVAL_SECONDS
    api_key_hash = hashlib.md5(account.kraken_api_key.encode()).hexdigest()[:8]
    cache_key = f"{account.id}_{api_key_hash}"
    cache_ttl = CACHE_TTL_SECONDS  # Use constant from trading_commands
    
    current_time = time.time()
    
    # Thread-safe cache check
    with _cache_lock:
        if cache_key in _balance_positions_cache:
            cached_balance, cached_positions, cached_time = _balance_positions_cache[cache_key]
            if current_time - cached_time < cache_ttl:
                logger.debug(f"Using cached balance and positions for account {account.id}")
                return cached_balance, cached_positions
        
        # Apply global rate limiting: minimum interval between ANY Kraken API call (10 seconds)
        # Release cache lock before acquiring rate limiter lock to avoid deadlock
        _cache_lock.release()
        try:
            # Use global keyword to ensure we're modifying the module-level variable
            global _global_kraken_last_call_time
            with _global_kraken_lock:
                time_since_last_call = current_time - _global_kraken_last_call_time
                min_interval = RATE_LIMIT_INTERVAL_SECONDS  # Use constant from trading_commands
                
                if time_since_last_call < min_interval:
                    sleep_time = min_interval - time_since_last_call
                    logger.info(f"Rate limiting: sleeping {sleep_time:.2f}s before Kraken balance API call (min interval: {min_interval}s)")
                    _global_kraken_lock.release()
                    try:
                        time.sleep(sleep_time)
                    finally:
                        _global_kraken_lock.acquire()
                    current_time = time.time()
                
                _global_kraken_last_call_time = current_time
        finally:
            _cache_lock.acquire()
    
    try:
        # Single API call to get balance (which contains all asset balances)
        balance_data = get_balance(api_key=account.kraken_api_key, private_key=account.kraken_private_key)
        
        # Parse balance data
        parsed_balances = _parse_kraken_balance(balance_data)
        
        # Extract USD balance
        usd_balance = parsed_balances.get("USD", parsed_balances.get("ZUSD", 0.0))
        balance = Decimal(str(usd_balance)) if usd_balance >= 0 else None
        
        # Extract positions from balance (all non-USD assets)
        positions = []
        for asset, amount in parsed_balances.items():
            if asset.upper() not in ["USD", "ZUSD"] and amount > 0:
                positions.append({
                    "symbol": asset,
                    "quantity": Decimal(str(amount)),
                    "available_quantity": Decimal(str(amount)),
                    "avg_cost": Decimal('0'),  # Would need trade history to calculate
                })
        
        # Thread-safe cache update
        with _cache_lock:
            _balance_positions_cache[cache_key] = (balance, positions, time.time())
        
        logger.debug(f"Fetched Kraken balance: ${usd_balance:.2f}, positions: {len(positions)}")
        return balance, positions
        
    except urllib.error.HTTPError as e:
        if e.code == 403:
            logger.error(f"Kraken API authentication failed (403 Forbidden) for account {account.name}. Please check if the API key and private key are correct and have proper permissions. Error: {e}")
        else:
            logger.error(f"Kraken API HTTP error {e.code} for account {account.name}: {e}", exc_info=True)
        return None, []
    except Exception as e:
        logger.error(f"Failed to get balance and positions from Kraken for account {account.name}: {e}", exc_info=True)
        return None, []


def get_kraken_balance_and_positions(account: Account) -> tuple[Optional[Decimal], List[Dict]]:
    """
    Get both balance and positions from Kraken in a single API call.
    Returns tuple of (balance: Optional[Decimal], positions: List[Dict]).
    Use this function when you need both balance and positions to minimize API calls.
    """
    return _get_kraken_balance_and_positions(account)


def get_kraken_balance_real_time(account: Account) -> Optional[Decimal]:
    """
    Get account balance from Kraken in real-time.
    Returns the USD balance as Decimal, or None if query fails.
    Uses shared API call with positions to reduce rate limit issues.
    """
    balance, _ = _get_kraken_balance_and_positions(account)
    return balance


def get_kraken_positions_real_time(account: Account) -> List[Dict]:
    """
    Get open positions from Kraken in real-time.
    Positions are extracted from balance data (non-USD assets).
    Uses shared API call with balance to reduce rate limit issues.
    """
    _, positions = _get_kraken_balance_and_positions(account)
    return positions


def get_kraken_open_orders_real_time(account: Account) -> List[Dict]:
    """
    Get open orders from Kraken in real-time.
    Uses kraken.account.get_open_orders() with account's API keys.
    """
    if not KRAKEN_AVAILABLE:
        logger.warning("Kraken module not available, cannot get orders")
        return []
    
    if not account.kraken_api_key or not account.kraken_private_key:
        logger.debug(f"Account {account.name} does not have Kraken API keys configured")
        return []
    
    try:
        # Use global rate limiter for all Kraken API calls (ensures 10-second interval)
        from services.trading_commands import RATE_LIMIT_INTERVAL_SECONDS
        
        # Use global keyword to ensure we're modifying the module-level variable
        global _global_kraken_last_call_time
        
        current_time = time.time()
        min_interval = RATE_LIMIT_INTERVAL_SECONDS  # Use constant
        
        # Apply global rate limiting (thread-safe)
        with _global_kraken_lock:
            time_since_last_call = current_time - _global_kraken_last_call_time
            
            if time_since_last_call < min_interval:
                sleep_time = min_interval - time_since_last_call
                logger.info(f"Rate limiting (orders): sleeping {sleep_time:.2f}s before Kraken API call (min interval: {min_interval}s)")
                _global_kraken_lock.release()
                try:
                    time.sleep(sleep_time)
                finally:
                    _global_kraken_lock.acquire()
                current_time = time.time()
            
            _global_kraken_last_call_time = current_time
        
        # Use kraken.account.get_open_orders with account's API keys
        orders_data = get_open_orders(api_key=account.kraken_api_key, private_key=account.kraken_private_key)
        return _parse_kraken_open_orders(orders_data)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            logger.error(f"Kraken API authentication failed (403 Forbidden) for account {account.name}. Please check if the API key and private key are correct and have proper permissions. Error: {e}")
        else:
            logger.error(f"Kraken API HTTP error {e.code} for account {account.name}: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Failed to get open orders from Kraken for account {account.name}: {e}", exc_info=True)
        return []


def get_kraken_closed_orders_real_time(account: Account, limit: int = 100) -> List[Dict]:
    """
    Get closed/completed orders from Kraken in real-time.
    Uses kraken.account.get_closed_orders() with account's API keys.
    Returns list of completed trade dicts.
    """
    if not KRAKEN_AVAILABLE:
        logger.warning("Kraken module not available, cannot get closed orders")
        return []
    
    if not account.kraken_api_key or not account.kraken_private_key:
        logger.debug(f"Account {account.name} does not have Kraken API keys configured")
        return []
    
    try:
        if not get_closed_orders:
            logger.warning("get_closed_orders not available")
            return []
        
        # Use global rate limiter for all Kraken API calls (ensures 10-second interval)
        from services.trading_commands import RATE_LIMIT_INTERVAL_SECONDS
        
        # Use global keyword to ensure we're modifying the module-level variable
        global _global_kraken_last_call_time
        
        current_time = time.time()
        min_interval = RATE_LIMIT_INTERVAL_SECONDS  # Use constant
        
        # Apply global rate limiting (thread-safe)
        with _global_kraken_lock:
            time_since_last_call = current_time - _global_kraken_last_call_time
            
            if time_since_last_call < min_interval:
                sleep_time = min_interval - time_since_last_call
                logger.info(f"Rate limiting (closed orders): sleeping {sleep_time:.2f}s before Kraken API call (min interval: {min_interval}s)")
                _global_kraken_lock.release()
                try:
                    time.sleep(sleep_time)
                finally:
                    _global_kraken_lock.acquire()
                current_time = time.time()
            
            _global_kraken_last_call_time = current_time
        
        # Use kraken.account.get_closed_orders with account's API keys
        orders_data = get_closed_orders(api_key=account.kraken_api_key, private_key=account.kraken_private_key, limit=limit)
        
        try:
            data = json.loads(orders_data)
            if data.get("error"):
                logger.error(f"Kraken closed orders API error: {data.get('error')}")
                return []
            
            result = data.get("result", {})
            closed_orders = result.get("closed", {})
            order_list = []
            
            for txid, order_info in list(closed_orders.items())[:limit]:
                desc = order_info.get("descr", {})
                pair = desc.get("pair", "")
                order_type = desc.get("type", "")
                price = float(desc.get("price", 0) or 0)
                volume = float(order_info.get("vol_exec", order_info.get("vol", 0)))
                cost = float(order_info.get("cost", 0))
                fee = float(order_info.get("fee", 0))
                
                # Map Kraken pair to internal symbol
                asset_symbol = pair.replace("USD", "")
                symbol = map_kraken_asset_to_internal(asset_symbol)
                
                order_list.append({
                    "txid": txid,
                    "symbol": symbol,
                    "side": "BUY" if order_type == "buy" else "SELL",
                    "price": price,
                    "quantity": volume,
                    "cost": cost,
                    "fee": fee,
                    "status": "FILLED",
                    "close_time": order_info.get("closetm", 0),
                })
            
            # Sort by close time descending (most recent first)
            order_list.sort(key=lambda x: x.get("close_time", 0), reverse=True)
            return order_list
        except Exception as e:
            logger.error(f"Failed to parse Kraken closed orders: {e}", exc_info=True)
            return []
    except urllib.error.HTTPError as e:
        if e.code == 403:
            logger.error(f"Kraken API authentication failed (403 Forbidden) for account {account.name}. Please check if the API key and private key are correct and have proper permissions. Error: {e}")
        else:
            logger.error(f"Kraken API HTTP error {e.code} for account {account.name}: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Failed to get closed orders from Kraken for account {account.name}: {e}", exc_info=True)
        return []


def map_symbol_to_kraken_pair(symbol: str) -> str:
    """
    Map internal symbol to Kraken trading pair.
    
    Args:
        symbol: Internal trading symbol (e.g., "BTC", "ETH")
    
    Returns:
        Kraken trading pair (e.g., "XBTUSD", "ETHUSD")
    """
    if not KRAKEN_AVAILABLE or not map_token:
        # Fallback: simple USD pair
        return f"{symbol}USD"
    return map_token(symbol.upper())


def execute_kraken_order(
    api_key: str,
    private_key: str,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    ordertype: str = "market"
) -> tuple[bool, Optional[str], Optional[Dict]]:
    """
    Execute an order on Kraken.
    
    Args:
        api_key: Kraken API public key
        private_key: Kraken API private key
        symbol: Trading symbol (e.g., "BTC", "ETH")
        side: Order side ("BUY" or "SELL")
        quantity: Order quantity
        price: Order price (reference price for market orders)
        ordertype: Order type ("market", "limit", etc.)
    
    Returns:
        Tuple of (success: bool, error_message_or_txid: Optional[str], result: Optional[Dict])
    """
    if not KRAKEN_AVAILABLE or not kraken_add_order:
        return False, "Kraken module not available", None
    
    if not api_key or not private_key:
        return False, "Kraken API keys not configured", None
    
    try:
        # Use global rate limiter for all Kraken API calls (ensures 10-second interval)
        from services.trading_commands import RATE_LIMIT_INTERVAL_SECONDS
        
        # Use global keyword to ensure we're modifying the module-level variable
        global _global_kraken_last_call_time
        
        current_time = time.time()
        min_interval = RATE_LIMIT_INTERVAL_SECONDS  # Use constant
        
        # Apply global rate limiting (thread-safe)
        with _global_kraken_lock:
            time_since_last_call = current_time - _global_kraken_last_call_time
            
            if time_since_last_call < min_interval:
                sleep_time = min_interval - time_since_last_call
                logger.info(f"Rate limiting (execute order): sleeping {sleep_time:.2f}s before Kraken API call (min interval: {min_interval}s)")
                _global_kraken_lock.release()
                try:
                    time.sleep(sleep_time)
                finally:
                    _global_kraken_lock.acquire()
                current_time = time.time()
            
            _global_kraken_last_call_time = current_time
        
        pair = map_symbol_to_kraken_pair(symbol)
        order_type = side.lower()  # "buy" or "sell"
        
        logger.info(f"Placing Kraken order: {side} {quantity} {symbol} @ {price} (pair={pair}, ordertype={ordertype})")
        
        result = kraken_add_order(
            api_key=api_key,
            private_key=private_key,
            pair=pair,
            type=order_type,
            ordertype=ordertype,
            volume=quantity,
            price=price,
        )
        
        if result.get("error"):
            error_msg = result.get("error", ["Unknown error"])
            if isinstance(error_msg, list):
                error_msg = error_msg[0] if error_msg else "Unknown error"
            logger.error(f"Kraken API error: {error_msg}")
            return False, str(error_msg), result
        
        txid = result.get("result", {}).get("txid", [])
        if txid:
            txid_str = txid[0] if isinstance(txid, list) else str(txid)
            logger.info(f"Kraken order placed successfully: txid={txid_str}, pair={pair}, type={order_type}, volume={quantity}")
            return True, txid_str, result
        else:
            logger.warning(f"Kraken order response missing txid: {result}")
            return False, "Missing transaction ID in response", result
            
    except urllib.error.HTTPError as e:
        error_msg = f"HTTP error {e.code}: {e}"
        logger.error(f"Failed to execute Kraken order: {error_msg}", exc_info=True)
        return False, error_msg, None
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to execute Kraken order: {error_msg}", exc_info=True)
        return False, error_msg, None


def cancel_kraken_order(api_key: str, private_key: str, txid: str) -> tuple[bool, Optional[str], Optional[Dict]]:
    """
    Cancel an order on Kraken.
    
    Args:
        api_key: Kraken API public key
        private_key: Kraken API private key
        txid: Transaction ID of the order to cancel
    
    Returns:
        Tuple of (success: bool, error_message: Optional[str], result: Optional[Dict])
    """
    if not KRAKEN_AVAILABLE or not kraken_cancel_order:
        return False, "Kraken module not available", None
    
    if not api_key or not private_key:
        return False, "Kraken API keys not configured", None
    
    try:
        # Use global rate limiter for all Kraken API calls (ensures 10-second interval)
        from services.trading_commands import RATE_LIMIT_INTERVAL_SECONDS
        
        # Use global keyword to ensure we're modifying the module-level variable
        global _global_kraken_last_call_time
        
        current_time = time.time()
        min_interval = RATE_LIMIT_INTERVAL_SECONDS  # Use constant
        
        # Apply global rate limiting (thread-safe)
        with _global_kraken_lock:
            time_since_last_call = current_time - _global_kraken_last_call_time
            
            if time_since_last_call < min_interval:
                sleep_time = min_interval - time_since_last_call
                logger.info(f"Rate limiting (cancel order): sleeping {sleep_time:.2f}s before Kraken API call (min interval: {min_interval}s)")
                _global_kraken_lock.release()
                try:
                    time.sleep(sleep_time)
                finally:
                    _global_kraken_lock.acquire()
                current_time = time.time()
            
            _global_kraken_last_call_time = current_time
        
        logger.info(f"Cancelling Kraken order: txid={txid}")
        
        result = kraken_cancel_order(
            api_key=api_key,
            private_key=private_key,
            txid=txid,
        )
        
        if result.get("error"):
            error_msg = result.get("error", ["Unknown error"])
            if isinstance(error_msg, list):
                error_msg = error_msg[0] if error_msg else "Unknown error"
            logger.error(f"Kraken API error: {error_msg}")
            return False, str(error_msg), result
        
        logger.info(f"Kraken order cancelled successfully: txid={txid}")
        return True, None, result
        
    except urllib.error.HTTPError as e:
        error_msg = f"HTTP error {e.code}: {e}"
        logger.error(f"Failed to cancel Kraken order: {error_msg}", exc_info=True)
        return False, error_msg, None
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to cancel Kraken order: {error_msg}", exc_info=True)
        return False, error_msg, None


def sync_account_from_kraken(db, account: Account) -> Dict[str, bool]:
    """
    DEPRECATED: This function is kept for backward compatibility but does nothing.
    All trading data is now fetched in real-time from Kraken API.
    No database storage needed.
    
    Returns:
        Dict with sync status (always True, since we just query Kraken)
    """
    logger.debug(f"[KRAKEN_SYNC] sync_account_from_kraken called for account {account.id} - no-op (using real-time queries)")
    # Just verify we can query from Kraken (no database storage)
    balance = get_kraken_balance_real_time(account)
    positions = get_kraken_positions_real_time(account)
    orders = get_kraken_open_orders_real_time(account)
    
    return {
        "balance": balance is not None,
        "positions": len(positions) >= 0,  # Always True if query succeeds
        "orders": len(orders) >= 0,  # Always True if query succeeds
    }

