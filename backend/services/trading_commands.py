"""
Trading Commands Service - Handles order execution and trading logic
"""
import logging
import random
import sys
import os
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from database.connection import SessionLocal, get_session_for_mode
from database.models import (CRYPTO_COMMISSION_RATE, CRYPTO_MIN_COMMISSION,
                             Account, Position)
from services.ai_decision_service import (SUPPORTED_SYMBOLS,
                                          _get_portfolio_data,
                                          call_ai_for_decision,
                                          get_active_ai_accounts,
                                          save_ai_decision)
from services.asset_calculator import calc_positions_value
from services.market_data import get_last_price
from services.order_matching import check_and_execute_order, create_order
from sqlalchemy.orm import Session

# Add kraken module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
try:
    from kraken.trade import add_order as kraken_add_order, map_token
    KRAKEN_AVAILABLE = True
except ImportError:
    KRAKEN_AVAILABLE = False
    kraken_add_order = None
    map_token = None

logger = logging.getLogger(__name__)

AI_TRADING_SYMBOLS: List[str] = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]


def _map_symbol_to_kraken_pair(symbol: str) -> str:
    """Map internal symbol to Kraken trading pair using token_map.yaml"""
    if not KRAKEN_AVAILABLE:
        return f"{symbol}USD"
    return map_token(symbol.upper())


def _execute_real_trade(symbol: str, side: str, quantity: float, price: float) -> Tuple[bool, Optional[str]]:
    """
    Execute real trade on Kraken
    
    Returns:
        Tuple[bool, Optional[str]]: (success, error_message or kraken_txid)
    """
    if not KRAKEN_AVAILABLE:
        return False, "Kraken module not available"
    
    try:
        pair = _map_symbol_to_kraken_pair(symbol)
        order_type = side.lower()  # "buy" or "sell"
        ordertype = "market"
        
        # For market orders, we still need to provide a price reference
        # Kraken may use current market price, but price parameter is required
        result = kraken_add_order(
            pair=pair,
            type=order_type,
            ordertype=ordertype,
            volume=quantity,
            price=price,  # Reference price for market order
        )
        
        if result.get("error"):
            error_msg = result.get("error", ["Unknown error"])[0] if isinstance(result.get("error"), list) else str(result.get("error"))
            logger.error(f"Kraken API error: {error_msg}")
            return False, error_msg
        
        txid = result.get("result", {}).get("txid", [])
        if txid:
            txid_str = txid[0] if isinstance(txid, list) else str(txid)
            logger.info(f"Kraken order placed successfully: txid={txid_str}, pair={pair}, type={order_type}, volume={quantity}")
            return True, txid_str
        else:
            logger.warning(f"Kraken order response missing txid: {result}")
            return False, "Missing transaction ID in response"
            
    except Exception as e:
        logger.error(f"Failed to execute real trade on Kraken: {e}", exc_info=True)
        return False, str(e)


def _estimate_buy_cash_needed(price: float, quantity: float) -> Decimal:
    """Estimate cash required for a BUY including commission."""
    notional = Decimal(str(price)) * Decimal(str(quantity))
    commission = max(
        notional * Decimal(str(CRYPTO_COMMISSION_RATE)),
        Decimal(str(CRYPTO_MIN_COMMISSION)),
    )
    return notional + commission


def _get_market_prices(symbols: List[str]) -> Dict[str, float]:
    """Get latest prices for given symbols"""
    prices = {}
    for symbol in symbols:
        try:
            price = float(get_last_price(symbol, "CRYPTO"))
            if price > 0:
                prices[symbol] = price
        except Exception as err:
            logger.warning(f"Failed to get price for {symbol}: {err}")
    return prices


def _select_side(db: Session, account: Account, symbol: str, max_value: float) -> Optional[Tuple[str, int]]:
    """Select random trading side and quantity for legacy random trading"""
    market = "CRYPTO"
    try:
        price = float(get_last_price(symbol, market))
    except Exception as err:
        logger.warning("Cannot get price for %s: %s", symbol, err)
        return None

    if price <= 0:
        logger.debug("%s returned non-positive price %s", symbol, price)
        return None

    max_quantity_by_value = int(Decimal(str(max_value)) // Decimal(str(price)))
    position = (
        db.query(Position)
        .filter(Position.account_id == account.id, Position.symbol == symbol, Position.market == market)
        .first()
    )
    available_quantity = int(position.available_quantity) if position else 0

    choices = []

    if float(account.current_cash) >= price and max_quantity_by_value >= 1:
        choices.append(("BUY", max_quantity_by_value))

    if available_quantity > 0:
        max_sell_quantity = min(available_quantity, max_quantity_by_value if max_quantity_by_value >= 1 else available_quantity)
        if max_sell_quantity >= 1:
            choices.append(("SELL", max_sell_quantity))

    if not choices:
        return None

    side, max_qty = random.choice(choices)
    quantity = random.randint(1, max_qty)
    return side, quantity


def place_ai_driven_crypto_order(max_ratio: float = 0.2, account_ids: Optional[Iterable[int]] = None, trade_mode: str = "paper") -> None:
    """Place crypto order based on AI model decision.

    Args:
        max_ratio: maximum portion of portfolio to allocate per trade.
        account_ids: optional iterable of account IDs to process (defaults to all active accounts).
        trade_mode: specify 'real' for real trading, 'paper' for paper trading.
    """
    # Accounts are stored in paper database (for metadata)
    paper_db = SessionLocal()
    try:
        accounts = get_active_ai_accounts(paper_db)
        if not accounts:
            logger.debug("No available accounts, skipping AI trading")
            return

        if account_ids is not None:
            id_set = {int(acc_id) for acc_id in account_ids}
            accounts = [acc for acc in accounts if acc.id in id_set]
            if not accounts:
                logger.debug("No matching accounts for provided IDs: %s", account_ids)
                return

        if trade_mode not in ["real", "paper"]:
            logger.warning(f"Invalid trade_mode '{trade_mode}' for AI trading, defaulting to 'paper'.")
            trade_mode = "paper"

        # Get latest market prices once for all accounts
        prices = _get_market_prices(AI_TRADING_SYMBOLS)
        if not prices:
            logger.warning("Failed to fetch market prices, skipping AI trading")
            return

        # Iterate through all active accounts
        for account in accounts:
            # Get account's own trade_mode (each account can have different mode)
            account_trade_mode = account.trade_mode if account.trade_mode in ["real", "paper"] else "paper"
            if account_trade_mode != trade_mode:
                logger.info(f"Account {account.name} has trade_mode={account_trade_mode}, but function called with trade_mode={trade_mode}. Using account's trade_mode.")
            
            logger.info(f"Processing AI trading for account: {account.name} (mode: {account_trade_mode})")
            
            # Use database session for this account's trade_mode
            account_db = get_session_for_mode(account_trade_mode)
            try:
                # Get portfolio data for this account (from account's database)
                portfolio = _get_portfolio_data(account_db, account)
                
                if portfolio['total_assets'] <= 0:
                    logger.debug(f"Account {account.name} has non-positive total assets, skipping")
                    continue

                # Call AI for trading decision (uses paper_db for account metadata)
                decision = call_ai_for_decision(paper_db, account, portfolio, prices)
                if not decision or not isinstance(decision, dict):
                    logger.warning(f"Failed to get AI decision for {account.name}, skipping")
                    continue

                operation = decision.get("operation", "").lower() if decision.get("operation") else ""
                symbol = decision.get("symbol", "").upper() if decision.get("symbol") else ""
                target_portion = float(decision.get("target_portion_of_balance", 0)) if decision.get("target_portion_of_balance") is not None else 0
                reason = decision.get("reason", "No reason provided")

                logger.info(f"AI decision for {account.name}: {operation} {symbol} (portion: {target_portion:.2%}) - {reason}")

                # Validate decision
                if operation not in ["buy", "sell", "hold"]:
                    logger.warning(f"Invalid operation '{operation}' from AI for {account.name}, skipping")
                    # Save invalid decision for debugging (use account's database)
                    save_ai_decision(account_db, account, decision, portfolio, executed=False)
                    continue
                
                if operation == "hold":
                    logger.info(f"AI decided to HOLD for {account.name}")
                    # Save hold decision (use account's database)
                    save_ai_decision(account_db, account, decision, portfolio, executed=True)
                    continue

                if symbol not in SUPPORTED_SYMBOLS:
                    logger.warning(f"Invalid symbol '{symbol}' from AI for {account.name}, skipping")
                    # Save invalid decision for debugging (use account's database)
                    save_ai_decision(account_db, account, decision, portfolio, executed=False)
                    continue

                if target_portion <= 0 or target_portion > 1:
                    logger.warning(f"Invalid target_portion {target_portion} from AI for {account.name}, skipping")
                    # Save invalid decision for debugging (use account's database)
                    save_ai_decision(account_db, account, decision, portfolio, executed=False)
                    continue

                # Get current price
                price = prices.get(symbol)
                if not price or price <= 0:
                    logger.warning(f"Invalid price for {symbol} for {account.name}, skipping")
                    # Save decision with execution failure (use account's database)
                    save_ai_decision(account_db, account, decision, portfolio, executed=False)
                    continue

                # Calculate quantity based on operation
                if operation == "buy":
                    # Get account from account's database to get current cash
                    account_in_db = account_db.query(Account).filter(Account.id == account.id).first()
                    if not account_in_db:
                        logger.warning(f"Account {account.name} not found in {account_trade_mode} database, skipping")
                        save_ai_decision(account_db, account, decision, portfolio, executed=False)
                        continue
                    
                    # Calculate quantity based on available cash and target portion
                    available_cash = float(account_in_db.current_cash)
                    available_cash_dec = Decimal(str(account_in_db.current_cash))
                    order_value = available_cash * target_portion
                    # For crypto, support fractional quantities - use float instead of int
                    quantity = float(Decimal(str(order_value)) / Decimal(str(price)))
                    
                    # Round to reasonable precision (6 decimal places for crypto)
                    quantity = round(quantity, 6)
                    
                    if quantity <= 0:
                        logger.info(f"Calculated BUY quantity <= 0 for {symbol} for {account.name}, skipping")
                        # Save decision with execution failure
                        save_ai_decision(account_db, account, decision, portfolio, executed=False)
                        continue

                    cash_needed = _estimate_buy_cash_needed(price, quantity)
                    if available_cash_dec < cash_needed:
                        logger.info(
                            "Skipping BUY for %s due to insufficient cash after fees: need $%.2f, current cash $%.2f",
                            account.name,
                            float(cash_needed),
                            float(available_cash_dec),
                        )
                        save_ai_decision(account_db, account, decision, portfolio, executed=False)
                        continue
                    
                    side = "BUY"

                elif operation == "sell":
                    # Calculate quantity based on position and target portion (from account's database)
                    position = (
                        account_db.query(Position)
                        .filter(Position.account_id == account.id, Position.symbol == symbol, Position.market == "CRYPTO")
                        .first()
                    )
                    
                    if not position or float(position.available_quantity) <= 0:
                        logger.info(f"No position available to SELL for {symbol} for {account.name}, skipping")
                        # Save decision with execution failure
                        save_ai_decision(account_db, account, decision, portfolio, executed=False)
                        continue
                    
                    available_quantity = int(position.available_quantity)
                    quantity = max(1, int(available_quantity * target_portion))
                    
                    if quantity > available_quantity:
                        quantity = available_quantity
                    
                    side = "SELL"
                
                else:
                    continue

                # Create and execute order (in account's database)
                name = SUPPORTED_SYMBOLS[symbol]
                
                # Get account from account's database for order creation
                account_in_db = account_db.query(Account).filter(Account.id == account.id).first()
                if not account_in_db:
                    logger.warning(f"Account {account.name} not found in {account_trade_mode} database, skipping")
                    save_ai_decision(account_db, account, decision, portfolio, executed=False)
                    continue
                
                try:
                    order = create_order(
                        db=account_db,
                        account=account_in_db,
                        symbol=symbol,
                        name=name,
                        side=side,
                        order_type="MARKET",
                        price=None,
                        quantity=quantity,
                    )
                except ValueError as create_err:
                    message = str(create_err)
                    if "Insufficient cash" in message or "Insufficient positions" in message:
                        logger.info(
                            "Skipping order for %s (%s %s): %s",
                            account.name,
                            side,
                            symbol,
                            message,
                        )
                        account_db.rollback()
                        save_ai_decision(account_db, account, decision, portfolio, executed=False)
                        continue
                    # Unexpected validation error - re-raise
                    raise

                account_db.commit()
                account_db.refresh(order)

                # Execute trade based on account's trade_mode
                executed = False
                kraken_txid = None
                
                if account_trade_mode == "real":
                    # Execute real trade on Kraken
                    logger.info(f"Executing REAL trade for {account.name}: {side} {quantity} {symbol} @ {price}")
                    executed, kraken_txid = _execute_real_trade(symbol, side, quantity, price)
                    
                    if executed:
                        # After successful Kraken trade, update database order status
                        # Mark as executed in our system
                        order.status = "FILLED"
                        order.filled_quantity = float(quantity)
                        # Store Kraken transaction ID if we have a field for it
                        # For now, just log it
                        logger.info(
                            f"REAL trade executed on Kraken: account={account.name} {side} {symbol} "
                            f"quantity={quantity} txid={kraken_txid} order_no={order.order_no}"
                        )
                        account_db.commit()
                    else:
                        logger.warning(
                            f"REAL trade failed on Kraken: account={account.name} {side} {symbol} "
                            f"quantity={quantity} error={kraken_txid}"
                        )
                        # Mark order as failed or keep as pending based on your business logic
                        order.status = "FAILED"
                        account_db.commit()
                else:
                    # Execute paper trade (simulated)
                    logger.info(f"Executing PAPER trade for {account.name}: {side} {quantity} {symbol} @ {price}")
                    executed = check_and_execute_order(account_db, order)
                    if executed:
                        account_db.refresh(order)
                        logger.info(
                            f"AI PAPER order executed: account={account.name} {side} {symbol} {order.order_no} "
                            f"quantity={quantity} reason='{reason}'"
                        )
                    else:
                        logger.info(
                            f"AI PAPER order created but not executed: account={account.name} {side} {symbol} "
                            f"quantity={quantity} order_id={order.order_no} reason='{reason}'"
                        )

                # Save decision with final execution status (use account's database)
                save_ai_decision(account_db, account, decision, portfolio, executed=executed, order_id=order.id)

            except Exception as account_err:
                logger.error(f"AI-driven order placement failed for account {account.name}: {account_err}", exc_info=True)
                account_db.rollback()
                # Continue with next account even if one fails
            finally:
                account_db.close()

    except Exception as err:
        logger.error(f"AI-driven order placement failed: {err}", exc_info=True)
        paper_db.rollback()
    finally:
        paper_db.close()


def place_random_crypto_order(max_ratio: float = 0.2) -> None:
    """Legacy random order placement (kept for backward compatibility)"""
    db = SessionLocal()
    try:
        accounts = get_active_ai_accounts(db)
        if not accounts:
            logger.debug("No available accounts, skipping auto order placement")
            return
        
        # For legacy compatibility, just pick a random account from the list
        account = random.choice(accounts)

        positions_value = calc_positions_value(db, account.id)
        total_assets = positions_value + float(account.current_cash)

        if total_assets <= 0:
            logger.debug("Account %s total assets non-positive, skipping auto order placement", account.name)
            return

        max_order_value = total_assets * max_ratio
        if max_order_value <= 0:
            logger.debug("Account %s maximum order amount is 0, skipping", account.name)
            return

        symbol = random.choice(list(SUPPORTED_SYMBOLS.keys()))
        side_info = _select_side(db, account, symbol, max_order_value)
        if not side_info:
            logger.debug("Account %s has no executable direction for %s, skipping", account.name, symbol)
            return

        side, quantity = side_info
        name = SUPPORTED_SYMBOLS[symbol]

        order = create_order(
            db=db,
            account=account,
            symbol=symbol,
            name=name,
            side=side,
            order_type="MARKET",
            price=None,
            quantity=quantity,
        )

        db.commit()
        db.refresh(order)

        executed = check_and_execute_order(db, order)
        if executed:
            db.refresh(order)
            logger.info("Auto order executed: account=%s %s %s %s quantity=%s", account.name, side, symbol, order.order_no, quantity)
        else:
            logger.info("Auto order created: account=%s %s %s quantity=%s order_id=%s", account.name, side, symbol, quantity, order.order_no)

    except Exception as err:
        logger.error("Auto order placement failed: %s", err)
        db.rollback()
    finally:
        db.close()


AUTO_TRADE_JOB_ID = "auto_crypto_trade"
AI_TRADE_JOB_ID = "ai_crypto_trade"
