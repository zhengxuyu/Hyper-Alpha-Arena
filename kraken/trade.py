import json

from kraken.account import Account, get_open_orders
from kraken.kraken_request import request
from kraken.market import get_ticker_information
from kraken.token_map import map_token


def add_order(pair: str, type: str, ordertype: str, volume: float, price: float):
    account = Account()
    pair = map_token(pair)
    body = {
        "pair": pair,
        "type": type,
        "ordertype": ordertype,
        "volume": volume,
        "price": price,
        "timeinforce": "GTD",
        "expiretm": "+5"

    }
    response = request(
        method="POST",
        path="/0/private/AddOrder",
        body=body,
        public_key=account.api_key,
        private_key=account.pvi,
        environment=account.environment
    )
    return json.loads(response.read().decode())


def cancel_order(txid: str):
    account = Account()
    body = {"txid": txid}
    response = request(
        method="POST",
        path="/0/private/CancelOrder",
        body=body,
        public_key=account.api_key,
        private_key=account.pvi,
        environment=account.environment
    )
    return json.loads(response.read().decode())


if __name__ == "__main__":
    # Get current ask price for XBTUSD
    ticker_info = get_ticker_information("XBTUSD")
    ask_price = ticker_info['result']['XXBTZUSD']['a'][0]
    print(f"Current ask price for XBTUSD: {ask_price}")

    print("Open Orders:")
    print(get_open_orders())

    print("\nCreating a new BTCUSD order:")
    result = add_order(pair="BTCUSD", type="buy", ordertype="limit", volume=1, price=ask_price)
    print(result)

    # print("\nCancelling the order:")
    # result = cancel_order(txid="1234567890")
    # print(result)