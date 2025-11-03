
import json

from kraken.account import Account
from kraken.kraken_request import request


def get_server_time():
    account = Account()
    response = request(
        method="GET",
        path="/0/public/Time",
        environment=account.environment
    )
    return json.loads(response.read().decode())


def get_system_status():
    account = Account()
    response = request(
        method="GET",
        path="/0/public/SystemStatus",
        environment=account.environment
    )
    return json.loads(response.read().decode())


def get_asset_info(asset: str = "ETH"):
    account = Account()
    response = request(
        method="GET",
        path="/0/public/Assets?asset=" + asset,
        environment=account.environment
    )
    return json.loads(response.read().decode())


def get_ticker_information(pair: str = "XBTUSD"):
    account = Account()
    response = request(
        method="GET",
        path="/0/public/Ticker?pair=" + pair,
        environment=account.environment
    )
    return json.loads(response.read().decode())


def get_tradable_asset_pairs():
    account = Account()
    response = request(
        method="GET",
        path="/0/public/AssetPairs",
        environment=account.environment
    )
    return json.loads(response.read().decode())["result"].keys()


if __name__ == "__main__":
    print(get_ticker_information("XBTUSD"))