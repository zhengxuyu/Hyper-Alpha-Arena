"""
Kraken Broker Implementation
Concrete implementation of BrokerInterface for Kraken exchange
"""
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from database.models import Account

from .broker_interface import BrokerInterface
from .kraken_sync import (
    cancel_kraken_order,
    execute_kraken_order,
    get_kraken_balance_and_positions,
    get_kraken_balance_real_time,
    get_kraken_closed_orders_real_time,
    get_kraken_open_orders_real_time,
    get_kraken_positions_real_time,
    map_symbol_to_kraken_pair,
)


class KrakenBroker(BrokerInterface):
    """Kraken broker implementation"""
    
    def get_balance(self, account: Account) -> Optional[Decimal]:
        """Get account balance from Kraken"""
        return get_kraken_balance_real_time(account)
    
    def get_positions(self, account: Account) -> List[Dict]:
        """Get open positions from Kraken"""
        return get_kraken_positions_real_time(account)
    
    def get_balance_and_positions(self, account: Account) -> Tuple[Optional[Decimal], List[Dict]]:
        """Get both balance and positions from Kraken in a single API call"""
        return get_kraken_balance_and_positions(account)
    
    def get_open_orders(self, account: Account) -> List[Dict]:
        """Get open orders from Kraken"""
        return get_kraken_open_orders_real_time(account)
    
    def get_closed_orders(self, account: Account, limit: int = 100) -> List[Dict]:
        """Get closed orders from Kraken"""
        return get_kraken_closed_orders_real_time(account, limit)
    
    def execute_order(
        self,
        account: Account,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        ordertype: str = "market"
    ) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """Execute an order on Kraken"""
        if not account.kraken_api_key or not account.kraken_private_key:
            return False, "Kraken API keys not configured", None
        
        return execute_kraken_order(
            api_key=account.kraken_api_key,
            private_key=account.kraken_private_key,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            ordertype=ordertype
        )
    
    def cancel_order(self, account: Account, order_id: str) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """Cancel an order on Kraken"""
        if not account.kraken_api_key or not account.kraken_private_key:
            return False, "Kraken API keys not configured", None
        
        return cancel_kraken_order(
            api_key=account.kraken_api_key,
            private_key=account.kraken_private_key,
            txid=order_id
        )
    
    def map_symbol_to_pair(self, symbol: str) -> str:
        """Map internal symbol to Kraken trading pair"""
        return map_symbol_to_kraken_pair(symbol)
    
    def get_broker_name(self) -> str:
        """Get broker name"""
        return "Kraken"

