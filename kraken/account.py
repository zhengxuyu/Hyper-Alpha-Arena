# Get all asset balances.

from kraken.auth import get_auth
from kraken.kraken_request import request


class Account:
    def __init__(self):
        self.api_key, self.pvi = get_auth()
        self.environment = "https://api.kraken.com"
    
def get_balance():
    account = Account()
    response = request(
        method="POST",
        path="/0/private/Balance",
        public_key=account.api_key,
        private_key=account.pvi,
        environment=account.environment,
    )
    return response.read().decode()


def get_trade_balance():
    account = Account()
    response = request(
        method="POST",
        path="/0/private/TradeBalance",
        public_key=account.api_key,
        private_key=account.pvi,
        environment=account.environment,
    )
    return response.read().decode()


def get_open_orders():
    account = Account()
    response = request(
        method="POST",
        path="/0/private/OpenOrders",
        public_key=account.api_key,
        private_key=account.pvi,
        environment=account.environment,
    )
    return response.read().decode()


def get_closed_orders():
    account = Account()
    response = request(
        method="POST",
        path="/0/private/ClosedOrders",
        public_key=account.api_key,
        private_key=account.pvi,
        environment=account.environment,
    )
    return response.read().decode()