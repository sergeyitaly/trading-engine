import asyncio
import json
import os
from typing import Dict, List, Optional, Any, Tuple
from decimal import Decimal
import ccxt.async_support as ccxt
from ccxt.base.errors import (
    InsufficientFunds,
    NetworkError,
    RequestTimeout,
    ExchangeError,
    OrderNotFound,
)
from pydantic import BaseModel, validator, Field
from datetime import datetime, timedelta
import aiohttp
import async_timeout
import logging
from enum import Enum
from app.config import load_config

# Common log format
log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Root logger configuration
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    handlers=[
        logging.FileHandler("trading_engine.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

# App logger
logger = logging.getLogger(__name__)
# API logger (separate file)
api_logger = logging.getLogger("ccxt")
api_logger.setLevel(logging.INFO)

api_handler = logging.FileHandler("api_calls.log", encoding="utf-8")
api_handler.setFormatter(logging.Formatter(log_format))
api_logger.addHandler(api_handler)

# Global session for connection pooling
SESSION = None


def get_session():
    """Get or create aiohttp session with connection pooling"""
    global SESSION
    if SESSION is None or SESSION.closed:
        timeout = aiohttp.ClientTimeout(
            total=10, connect=5, sock_connect=5, sock_read=5
        )
        SESSION = aiohttp.ClientSession(
            timeout=timeout,
            connector=aiohttp.TCPConnector(limit=100, limit_per_host=20),
        )
    return SESSION


class OrderType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"
    STOP = "stop"


class OrderStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELED = "canceled"


class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"


class TPOrderConfig(BaseModel):
    price_percent: float = Field(
        ..., gt=0, description="TP price percentage from entry"
    )
    quantity_percent: float = Field(
        ..., gt=0, le=100, description="Percentage of position to close"
    )


class LimitOrdersConfig(BaseModel):
    range_percent: float = Field(
        ..., gt=0, description="Range percentage for averaging orders"
    )
    orders_count: int = Field(
        ..., gt=0, le=20, description="Number of averaging orders"
    )
    amount_per_order: Optional[float] = None


class TradingConfig:
    def __init__(
        self,
        account: str,
        symbol: str,
        side: str,
        market_order_amount: float,
        stop_loss_percent: float,
        trailing_sl_offset_percent: float,
        limit_orders_amount: float,
        leverage: int,
        move_sl_to_breakeven: bool,
        tp_orders: List[Dict[str, float]],
        limit_orders: Dict[str, Any],
        api_timeout: int = 30,
        max_retries: int = 3,
        # Account-specific credentials (optional)
        bybit_testnet_api_key: Optional[str] = None,
        bybit_testnet_api_secret: Optional[str] = None,
        bybit_mainnet_api_key: Optional[str] = None,
        bybit_mainnet_api_secret: Optional[str] = None,
        gate_testnet_api_key: Optional[str] = None,
        gate_testnet_api_secret: Optional[str] = None,
        gate_mainnet_api_key: Optional[str] = None,
        gate_mainnet_api_secret: Optional[str] = None,
    ):

        self.account = account
        self.symbol = symbol
        self.side = side
        self.market_order_amount = market_order_amount
        self.stop_loss_percent = stop_loss_percent
        self.trailing_sl_offset_percent = trailing_sl_offset_percent
        self.limit_orders_amount = limit_orders_amount
        self.leverage = leverage
        self.move_sl_to_breakeven = move_sl_to_breakeven
        self.tp_orders = tp_orders
        self.limit_orders = limit_orders
        self.api_timeout = api_timeout
        self.max_retries = max_retries

        # Account-specific credentials
        self.bybit_testnet_api_key = bybit_testnet_api_key
        self.bybit_testnet_api_secret = bybit_testnet_api_secret
        self.bybit_mainnet_api_key = bybit_mainnet_api_key
        self.bybit_mainnet_api_secret = bybit_mainnet_api_secret
        self.gate_testnet_api_key = gate_testnet_api_key
        self.gate_testnet_api_secret = gate_testnet_api_secret
        self.gate_mainnet_api_key = gate_mainnet_api_key
        self.gate_mainnet_api_secret = gate_mainnet_api_secret

    @classmethod
    def from_file(cls, filename: str) -> "TradingConfig":
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradingConfig":
        return cls(**data)

    @validator("tp_orders")
    def validate_tp_orders(cls, v):
        total_percent = sum(tp.quantity_percent for tp in v)
        if total_percent > 100:
            raise ValueError("Total TP quantity percentage cannot exceed 100%")
        return v


class FastExchange:
    """Wrapper for CCXT exchange with optimized timeouts and connection pooling"""

    def __init__(
        self, exchange: ccxt.Exchange, max_retries: int = 3, timeout: int = 10
    ):
        self.exchange = exchange
        self.exchange.timeout = timeout * 1000  # Convert to milliseconds
        self.exchange.enableRateLimit = True
        self.session = get_session()
        self.max_retries = max_retries
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        """Close exchange connection"""
        if hasattr(self.exchange, "close") and self.exchange is not None:
            await self.exchange.close()

    async def safe_request(self, method_name, *args, **kwargs):
        """Make API request with retry logic and timeout handling"""
        # Check if the method exists
        if not hasattr(self.exchange, method_name):
            raise AttributeError(
                f"'{self.exchange.name}' object has no attribute '{method_name}'"
            )

        # Get the attribute
        attr = getattr(self.exchange, method_name)

        if callable(attr):
            # It's a method, call it with retry logic
            for retry in range(self.max_retries):
                try:
                    async with async_timeout.timeout(self.timeout):
                        result = await attr(*args, **kwargs)
                        return result
                except (asyncio.TimeoutError, RequestTimeout, NetworkError) as e:
                    if retry == self.max_retries - 1:
                        logger.error(
                            f"Final timeout/network error in {method_name} after {self.max_retries} retries: {e}"
                        )
                        raise
                    logger.warning(
                        f"Timeout/network error in {method_name}, retry {retry + 1}/{self.max_retries}: {e}"
                    )
                    await asyncio.sleep(1 * (retry + 1))  # Exponential backoff
                except ExchangeError as e:
                    logger.error(f"Exchange error in {method_name}: {e}")
                    raise
                except Exception as e:
                    logger.error(f"Unexpected error in {method_name}: {e}")
                    raise
        else:
            # It's a property, just return it
            return attr

    def __getattr__(self, name):
        """Delegate method calls to the underlying exchange with timeout handling"""

        async def wrapper(*args, **kwargs):
            return await self.safe_request(name, *args, **kwargs)

        return wrapper


class PositionManager:
    def __init__(self, exchange: FastExchange, config: TradingConfig):
        self.exchange = exchange
        self.config = config
        self.position = None
        self.open_orders: List[Dict[str, Any]] = []
        self.average_entry_price = None
        self.entry_time = None
        self._monitor_task = None
        self._running = False
        self.market_info = None
        self.initial_market_order_id = None

    async def initialize(self):
        """Initialize the position manager"""
        await self.load_market_info()
        await self.set_leverage()

    async def load_market_info(self):
        """Load market information for the symbol"""
        try:
            # Load all markets first
            await self.exchange.load_markets()

            # Try to set leverage (but don't fail if it doesn't work)
            try:
                await self.set_leverage()
            except Exception as leverage_error:
                logger.warning(
                    f"Could not set leverage (proceeding anyway): {leverage_error}"
                )

            # Debug: log all available symbols to see what format Bybit uses
            all_symbols = list(self.exchange.exchange.markets.keys())
            logger.info(f"Available symbols (first 10): {all_symbols[:10]}")

            # Try to find the market with different symbol formats
            symbol_variations = [
                self.config.symbol,  # Original format (e.g., "BTCUSDT")
                f"{self.config.symbol[:-4]}/{self.config.symbol[-4:]}:{self.config.symbol[-4:]}",  # BTC/USDT:USDT
                f"{self.config.symbol[:-4]}/{self.config.symbol[-4:]}",  # BTC/USDT
            ]

            for symbol_var in symbol_variations:
                if symbol_var in self.exchange.exchange.markets:
                    self.market_info = self.exchange.exchange.markets[symbol_var]
                    logger.info(f"Found market using symbol: {symbol_var}")
                    break
            else:
                # If not found, try case-insensitive search
                config_symbol_lower = self.config.symbol.lower()
                for (
                    market_symbol,
                    market_info,
                ) in self.exchange.exchange.markets.items():
                    if market_symbol.lower() == config_symbol_lower:
                        self.market_info = market_info
                        logger.info(
                            f"Found market using case-insensitive match: {market_symbol}"
                        )
                        break
                else:
                    raise ValueError(
                        f"Market {self.config.symbol} not found. Available symbols: {len(all_symbols)} total"
                    )

            logger.info(f"Market info loaded: {self.market_info['symbol']}")
            logger.info(
                f"Market precision: amount={self.market_info['precision']['amount']}, price={self.market_info['precision']['price']}"
            )

        except Exception as e:
            logger.error(f"Error loading market info: {e}")
            raise

    async def calculate_contract_size(
        self, amount_usdt: float, price: Optional[float] = None
    ) -> float:
        """Calculate contract size based on amount in USDT"""
        if not price:
            ticker = await self.exchange.safe_request(
                "fetch_ticker", self.config.symbol
            )
            price = ticker.get("last", 0)
        
        # Add comprehensive None and key checking
        if (self.market_info is None or 
            not isinstance(self.market_info, dict) or
            "type" not in self.market_info or 
            "settle" not in self.market_info or
            "precision" not in self.market_info or
            "amount" not in self.market_info["precision"] or
            "limits" not in self.market_info or
            "amount" not in self.market_info["limits"]
        ):
            logger.error("Market info missing required data")
            return 0.0

        if self.market_info["type"] == "swap" and self.market_info["settle"] == "USDT":
            # USDT perpetual contracts
            contract_size = amount_usdt / price
        else:
            # Coin-margined contracts or other types
            contract_size = amount_usdt * price

        # Apply precision limits with safety checks
        precision = self.market_info["precision"]["amount"]
        contract_size = float(round(contract_size / precision) * precision)

        # Get minimum amount with safety check
        min_amount = self.market_info["limits"]["amount"].get("min", 0)
        return max(contract_size, min_amount)

    async def open_market_position(self):
        """Open initial market position"""
        try:
            symbol = self.config.symbol
            amount = self.config.market_order_amount
            side = "buy" if self.config.side == PositionSide.LONG else "sell"

            logger.info(f"Opening market {side} position for {amount} USDT on {symbol}")

            # Calculate contract size
            ticker = await self.exchange.safe_request("fetch_ticker", symbol)
            current_price = ticker["last"]
            logger.info(f"Current price for {symbol}: ${current_price}")

            contract_size = await self.calculate_contract_size(amount, current_price)
            logger.info(f"Calculated contract size: {contract_size:.6f}")

            # Place market order
            order = await self.exchange.safe_request(
                "create_market_order", symbol, side, contract_size
            )

            self.initial_market_order_id = order["id"]
            logger.info(f"Market order executed: {order['id']} - {order['status']}")

            # Verify order execution
            await asyncio.sleep(1)  # Wait for order to process
            verified = await self.verify_order_execution(order["id"])

            if verified:
                # Get position details
                await self.update_position_info()

                # Place TP orders
                await self.place_tp_orders()

                # Place limit orders for averaging
                await self.place_averaging_orders()

                return True
            else:
                logger.error("Order execution verification failed")
                return False

        except InsufficientFunds as e:
            logger.error(f"Insufficient funds error: {e}")
            logger.error(f"Required: ${amount} USDT, Leverage: {self.config.leverage}x")
            return False
        except Exception as e:
            logger.error(f"Unexpected error opening market position: {e}")
            return False

    async def verify_order_execution(self, order_id: str) -> bool:
        """Verify that an order was executed successfully"""
        try:
            order_status = await self.exchange.safe_request(
                "fetch_order", order_id, self.config.symbol
            )

            if (
                order_status["status"] == OrderStatus.CLOSED
                and float(order_status["filled"]) > 0
            ):
                logger.info(
                    f"Order {order_id} successfully filled: {order_status['filled']}"
                )
                return True
            else:
                logger.warning(
                    f"Order {order_id} not fully filled. Status: {order_status['status']}, Filled: {order_status['filled']}"
                )
                return False

        except Exception as e:
            logger.error(f"Error verifying order execution: {e}")
            return False

    async def update_position_info(self):
        """Update position information from exchange"""
        try:
            positions = await self.exchange.safe_request(
                "fetch_positions", [self.config.symbol]
            )
            
            # Add proper None check
            if positions is None:
                logger.warning("No positions data returned")
                return False

            for position in positions:
                # Add comprehensive None and type checking
                if (position is not None and 
                    isinstance(position, dict) and
                    position.get("symbol") == self.config.symbol and
                    abs(float(position.get("contracts", 0))) > 0 and
                    position.get("side") == self.config.side.value
                ):
                    self.position = position
                    self.average_entry_price = float(position.get("entryPrice", 0))
                    self.entry_time = datetime.now()

                    logger.info(
                        f"Position updated: Entry Price: ${self.average_entry_price}, "
                        f"Contracts: {position.get('contracts', 0)}, "
                        f"Side: {position.get('side', 'unknown')}, "
                        f"Leverage: {position.get('leverage', 'N/A')}"
                    )
                    return True

            logger.warning("No open position found")
            return False

        except Exception as e:
            logger.error(f"Error updating position info: {e}")
            return False
        
    async def set_leverage(self):
        """Set leverage for the symbol with error handling for already-set values"""
        try:
            # Check if exchange supports leverage setting
            if not self.exchange.exchange.has.get("setLeverage", False):
                logger.info(
                    f"Exchange doesn't support API leverage setting. Using default leverage."
                )
                return

            # First, try to get current leverage to avoid unnecessary calls
            try:
                positions = await self.exchange.safe_request(
                    "fetch_positions", [self.config.symbol]
                )
                for position in positions:
                    if position["symbol"] == self.config.symbol:
                        current_leverage = float(position.get("leverage", 1))
                        if current_leverage == self.config.leverage:
                            logger.info(
                                f"Leverage already set to {self.config.leverage}x for {self.config.symbol}"
                            )
                            return
                        break
            except:
                # If we can't get current leverage, proceed with setting it
                pass

            # Set leverage with retry logic
            for attempt in range(3):
                try:
                    result = await self.exchange.safe_request(
                        "set_leverage", self.config.leverage, self.config.symbol
                    )
                    logger.info(
                        f"Leverage set to {self.config.leverage}x for {self.config.symbol}: {result}"
                    )
                    return
                except ExchangeError as e:
                    error_msg = str(e)
                    # Handle specific Bybit leverage errors
                    if "110043" in error_msg or "leverage not modified" in error_msg:
                        logger.info(
                            f"Leverage already set to {self.config.leverage}x or cannot be modified at this time"
                        )
                        return
                    elif "110026" in error_msg or "insufficient margin" in error_msg:
                        logger.warning(
                            f"Cannot set leverage due to insufficient margin or open positions"
                        )
                        return
                    elif attempt == 2:  # Final attempt
                        logger.error(
                            f"Failed to set leverage after {attempt + 1} attempts: {e}"
                        )
                        raise
                    else:
                        logger.warning(
                            f"Leverage setting failed (attempt {attempt + 1}), retrying: {e}"
                        )
                        await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Error in leverage setting process: {e}")
            # Don't fail the whole process for leverage errors

    async def place_tp_orders(self):
        """Place take-profit orders based on current average price"""
        if not self.average_entry_price or not self.position:
            logger.error("No average entry price or position available for TP orders")
            return False
        if "precision" not in self.market_info or "price" not in self.market_info["precision"]:
            logger.error("Market info missing precision data")
            return False


        symbol = self.config.symbol
        position_size = abs(float(self.position["contracts"]))

        # Cancel any existing TP orders first
        await self.cancel_tp_orders()

        for tp_config in self.config.tp_orders:
            # Calculate TP price
            if self.config.side == PositionSide.LONG:
                tp_price = self.average_entry_price * (
                    1 + tp_config.price_percent / 100
                )
            else:
                tp_price = self.average_entry_price * (
                    1 - tp_config.price_percent / 100
                )

            # Apply price precision
            price_precision = self.market_info["precision"]["price"]
            tp_price = float(round(tp_price / price_precision) * price_precision)

            # Calculate TP size
            tp_size = position_size * (tp_config.quantity_percent / 100)

            # Apply amount precision
            amount_precision = self.market_info["precision"]["amount"]
            tp_size = float(round(tp_size / amount_precision) * amount_precision)

            # Ensure minimum size
            tp_size = max(tp_size, self.market_info["limits"]["amount"]["min"])

            # Determine order side (opposite to position)
            order_side = "sell" if self.config.side == PositionSide.LONG else "buy"

            try:
                # Place limit order for TP
                order = await self.exchange.safe_request(
                    "create_order",
                    symbol,
                    OrderType.LIMIT,
                    order_side,
                    tp_size,
                    tp_price,
                    params={"reduceOnly": True},
                )

                self.open_orders.append(order)
                logger.info(
                    f"TP order placed: {order['id']} - {tp_size} contracts at ${tp_price}"
                )

            except Exception as e:
                logger.error(f"Error placing TP order: {e}")
                return False

        return True

    async def cancel_tp_orders(self):
        """Cancel all TP orders"""
        tp_orders = [
            o
            for o in self.open_orders
            if o.get("reduceOnly")
            or (o.get("info") and "reduce_only" in o.get("info", {}))
        ]

        for order in tp_orders:
            try:
                await self.exchange.safe_request(
                    "cancel_order", order["id"], self.config.symbol
                )
                logger.info(f"Cancelled TP order: {order['id']}")
            except (OrderNotFound, ExchangeError) as e:
                logger.warning(
                    f"TP order {order['id']} not found or already cancelled: {e}"
                )
            except Exception as e:
                logger.error(f"Error cancelling TP order {order['id']}: {e}")

        # Remove TP orders from tracking
        self.open_orders = [o for o in self.open_orders if o not in tp_orders]

    async def place_averaging_orders(self):
        """Place limit orders for position averaging"""
        if not self.average_entry_price:
            logger.error("No average entry price available for averaging orders")
            return False

        symbol = self.config.symbol
        current_price = self.average_entry_price
        range_percent = self.config.limit_orders.range_percent
        orders_count = self.config.limit_orders.orders_count
        amount_per_order = self.config.limit_orders.amount_per_order

        # Calculate order prices
        if self.config.side == PositionSide.LONG:
            # For long positions, place buy orders below current price
            start_price = current_price * (1 - range_percent / 100)
            end_price = current_price * (1 - range_percent / 200)
        else:
            # For short positions, place sell orders above current price
            start_price = current_price * (1 + range_percent / 200)
            end_price = current_price * (1 + range_percent / 100)

        # Create evenly spaced prices
        if orders_count > 1:
            price_step = (end_price - start_price) / (orders_count - 1)
            prices = [start_price + i * price_step for i in range(orders_count)]
        else:
            prices = [start_price]

        # Determine order side
        order_side = "buy" if self.config.side == PositionSide.LONG else "sell"

        for i, price in enumerate(prices):
            try:
                # Apply price precision
                price_precision = self.market_info["precision"]["price"]
                price = float(round(price / price_precision) * price_precision)

                # Calculate contract size
                contract_size = await self.calculate_contract_size(
                    amount_per_order, price
                )

                # Place limit order
                order = await self.exchange.safe_request(
                    "create_limit_order", symbol, order_side, contract_size, price
                )

                self.open_orders.append(order)
                logger.info(
                    f"Averaging order {i+1}/{orders_count} placed: {order['id']} - {contract_size} contracts at ${price}"
                )

            except Exception as e:
                logger.error(f"Error placing averaging order {i+1}: {e}")

        return True

    async def check_orders(self):
        """Check order status and update position if orders are filled"""
        try:
            # Get open orders
            open_orders = await self.exchange.safe_request(
                "fetch_open_orders", self.config.symbol
            )

            # Process orders in batches for better performance
            orders_to_remove = []
            position_updated = False

            for order in self.open_orders[:]:  # Create a copy for iteration
                order_id = order["id"]

                # Find current status of this order
                current_order = next(
                    (o for o in open_orders if o["id"] == order_id), None
                )

                if not current_order:  # Order is no longer open
                    try:
                        order_history = await self.exchange.safe_request(
                            "fetch_order", order_id, self.config.symbol
                        )

                        if (
                            order_history["status"] == OrderStatus.CLOSED
                        ):  # Order was filled
                            logger.info(
                                f"Order {order_id} filled: {order_history['filled']}"
                            )

                            # Update position information if this was an averaging order
                            if not order.get("reduceOnly"):
                                position_updated = True

                            orders_to_remove.append(order_id)
                        elif order_history["status"] == OrderStatus.CANCELED:
                            logger.info(f"Order {order_id} was cancelled")
                            orders_to_remove.append(order_id)

                    except OrderNotFound:
                        logger.warning(
                            f"Order {order_id} not found, assuming cancelled"
                        )
                        orders_to_remove.append(order_id)
                    except Exception as e:
                        logger.error(f"Error checking order {order_id}: {e}")

            # Remove processed orders
            self.open_orders = [
                o for o in self.open_orders if o["id"] not in orders_to_remove
            ]

            # Update position and TP orders if averaging orders were filled
            if position_updated:
                await self.update_position_info()
                await self.update_tp_orders()

        except Exception as e:
            logger.error(f"Error checking orders: {e}")

    async def update_tp_orders(self):
        """Update TP orders based on new average entry price"""
        if not self.average_entry_price:
            return

        logger.info(
            f"Updating TP orders based on new average price: ${self.average_entry_price}"
        )
        await self.place_tp_orders()

    async def monitor_position(self):
        """Monitor position and adjust orders as needed"""
        self._running = True
        check_counter = 0

        logger.info("Starting position monitoring")

        while self._running:
            try:
                # Check order status every loop
                await self.check_orders()

                # Update position info periodically (every 5 checks)
                if check_counter % 5 == 0:
                    position_open = await self.update_position_info()
                    if not position_open:
                        logger.info("Position closed, stopping monitoring")
                        break

                # Check if we need to move SL to breakeven (every 3 checks)
                if (
                    check_counter % 3 == 0
                    and self.config.move_sl_to_breakeven
                    and self.position
                ):
                    await self.check_breakeven_sl()

                # Increment counter and wait before next check
                check_counter += 1
                await asyncio.sleep(10)  # Check every 10 seconds

            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")
                await asyncio.sleep(15)

        self._running = False
        logger.info("Position monitoring stopped")

    async def stop_monitoring(self):
        """Stop the monitoring loop"""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                logger.info("Monitoring task cancelled")

    async def check_breakeven_sl(self):
        """Check if we should move stop loss to breakeven"""
        if not self.position or not self.average_entry_price:
            return

        try:
            # Get current price
            ticker = await self.exchange.safe_request(
                "fetch_ticker", self.config.symbol
            )
            current_price = ticker["last"]

            # Calculate profit percentage
            if self.config.side == PositionSide.LONG:
                profit_pct = (
                    (current_price - self.average_entry_price)
                    / self.average_entry_price
                    * 100
                )
                # Move SL to breakeven if profit reaches trailing SL offset
                if profit_pct >= self.config.trailing_sl_offset_percent:
                    await self.update_stop_loss(self.average_entry_price)
            else:
                profit_pct = (
                    (self.average_entry_price - current_price)
                    / self.average_entry_price
                    * 100
                )
                # Move SL to breakeven if profit reaches trailing SL offset
                if profit_pct >= self.config.trailing_sl_offset_percent:
                    await self.update_stop_loss(self.average_entry_price)

        except Exception as e:
            logger.error(f"Error checking breakeven SL: {e}")

    async def update_stop_loss(self, stop_price):
        """Update stop loss price"""
        try:
            # Cancel existing stop loss orders
            sl_orders = [o for o in self.open_orders if o.get("stopPrice")]

            for order in sl_orders:
                try:
                    await self.exchange.safe_request(
                        "cancel_order", order["id"], self.config.symbol
                    )
                    logger.info(f"Cancelled SL order: {order['id']}")
                except Exception as e:
                    logger.error(f"Error cancelling SL order {order['id']}: {e}")

            # Remove SL orders from tracking
            self.open_orders = [o for o in self.open_orders if not o.get("stopPrice")]

            # Place new stop loss order
            position_size = abs(float(self.position["contracts"]))
            order_side = "sell" if self.config.side == PositionSide.LONG else "buy"

            # Apply price precision
            price_precision = self.market_info["precision"]["price"]
            stop_price = float(round(stop_price / price_precision) * price_precision)

            # Place stop order
            order = await self.exchange.safe_request(
                "create_order",
                self.config.symbol,
                OrderType.STOP,
                order_side,
                position_size,
                stop_price,
                params={"reduceOnly": True, "stopPrice": stop_price},
            )

            self.open_orders.append(order)
            logger.info(f"Stop loss updated to {stop_price}")

        except Exception as e:
            logger.error(f"Error updating stop loss: {e}")

    async def close_all_orders(self):
        """Cancel all open orders"""
        try:
            orders = await self.exchange.safe_request(
                "fetch_open_orders", self.config.symbol
            )
            for order in orders:
                try:
                    await self.exchange.safe_request(
                        "cancel_order", order["id"], self.config.symbol
                    )
                    logger.info(f"Cancelled order: {order['id']}")
                except Exception as e:
                    logger.error(f"Error cancelling order {order['id']}: {e}")

            self.open_orders = []
        except Exception as e:
            logger.error(f"Error closing all orders: {e}")


class TradingEngine:
    def __init__(self, config: TradingConfig):
        self.config = config
        self.exchange = None
        self.position_manager = None
        self._fast_exchange = None

    async def initialize(self):
        """Initialize trading engine"""
        await self.init_exchange()

    async def init_exchange(self):
        try:
            exchange_config = {
                "apiKey": "",
                "secret": "",
                "timeout": self.config.api_timeout * 1000,
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            }

            account_type = self.config.account.lower()
            testnet = False  # Initialize testnet variable

            if "bybit" in account_type:
                exchange_id = "bybit"

                # Get credentials based on account type
                if "testnet" in account_type:
                    # Bybit Testnet credentials
                    api_key = getattr(self.config, "bybit_testnet_api_key", None)
                    api_secret = getattr(self.config, "bybit_testnet_api_secret", None)

                    # Fall back to environment variables
                    if not api_key or not api_secret:
                        api_key = os.getenv("BYBIT_TESTNET_API_KEY")
                        api_secret = os.getenv("BYBIT_TESTNET_API_SECRET")

                    testnet = True
                else:
                    # Bybit Mainnet credentials
                    api_key = getattr(self.config, "bybit_mainnet_api_key", None)
                    api_secret = getattr(self.config, "bybit_mainnet_api_secret", None)

                    # Fall back to environment variables
                    if not api_key or not api_secret:
                        api_key = os.getenv("BYBIT_API_KEY")
                        api_secret = os.getenv("BYBIT_API_SECRET")

                    testnet = False

                if not api_key or not api_secret:
                    raise ValueError(
                        f"Bybit {('Testnet' if testnet else 'Mainnet')} API credentials not found. Please configure in UI or environment variables"
                    )

                exchange_config.update(
                    {
                        "apiKey": api_key,
                        "secret": api_secret,
                        "sandbox": testnet,
                    }
                )

                exchange = ccxt.bybit(exchange_config)

            elif "gate" in account_type:
                exchange_id = "gateio"

                # Get credentials based on account type
                if "testnet" in account_type:
                    # Gate.io Testnet credentials
                    api_key = getattr(self.config, "gate_testnet_api_key", None)
                    api_secret = getattr(self.config, "gate_testnet_api_secret", None)

                    # Fall back to environment variables - try both naming conventions
                    if not api_key or not api_secret:
                        api_key = os.getenv("GATEIO_TESTNET_API_KEY") or os.getenv(
                            "GATE_TESTNET_API_KEY"
                        )
                        api_secret = os.getenv(
                            "GATEIO_TESTNET_API_SECRET"
                        ) or os.getenv("GATE_TESTNET_API_SECRET")

                    testnet = True  # Set testnet for Gate.io testnet
                else:
                    # Gate.io Mainnet credentials
                    api_key = getattr(self.config, "gate_mainnet_api_key", None)
                    api_secret = getattr(self.config, "gate_mainnet_api_secret", None)

                    # Fall back to environment variables - try both naming conventions
                    if not api_key or not api_secret:
                        api_key = os.getenv("GATEIO_API_KEY") or os.getenv(
                            "GATE_API_KEY"
                        )
                        api_secret = os.getenv("GATEIO_API_SECRET") or os.getenv(
                            "GATE_API_SECRET"
                        )

                    testnet = False  # Set testnet for Gate.io mainnet

                if not api_key or not api_secret:
                    raise ValueError(
                        f"Gate.io {('Testnet' if testnet else 'Mainnet')} API credentials not found. Please configure in UI or environment variables"
                    )

                exchange_config.update(
                    {
                        "apiKey": api_key,
                        "secret": api_secret,
                    }
                )

                exchange = ccxt.gateio(exchange_config)
            else:
                raise ValueError(
                    f"Unsupported exchange specified in config: {self.config.account}"
                )

            # Wrap with FastExchange for better timeout handling
            self._fast_exchange = FastExchange(
                exchange,
                max_retries=self.config.max_retries,
                timeout=self.config.api_timeout,
            )
            self.exchange = self._fast_exchange.exchange

            logger.info(
                f"Initialized {exchange_id} {'Testnet' if testnet else 'Mainnet'} exchange connection"
            )

            # Test connection
            await self.test_connection()

        except Exception as e:
            logger.error(f"Error initializing exchange: {e}")
            raise

    async def test_connection(self):
        """Test exchange connection"""
        try:
            # Try to fetch balance to test connection
            balance = await self._fast_exchange.fetch_balance()
            logger.info(
                f"Exchange connection test successful. Available balance: {balance.get('USDT', {}).get('free', 0):.2f} USDT"
            )
            return True
        except ExchangeError as e:
            # Handle Gate.io specific error for unfunded futures account
            error_msg = str(e)
            if "USER_NOT_FOUND" in error_msg and "transfer funds first" in error_msg:
                logger.warning(
                    "Gate.io connection successful but futures account not funded. Please deposit funds."
                )
                return True  # Still consider it a successful connection
            else:
                logger.error(f"Exchange connection test failed: {e}")
                raise
        except Exception as e:
            logger.error(f"Exchange connection test failed: {e}")
            raise

    async def run(self):
        """Run the trading engine"""
        try:
            # Initialize position manager
            self.position_manager = PositionManager(self._fast_exchange, self.config)
            await self.position_manager.initialize()

            # Open market position
            success = await self.position_manager.open_market_position()

            if success:
                # Start monitoring in background
                self.position_manager._monitor_task = asyncio.create_task(
                    self.position_manager.monitor_position()
                )

                # Wait for monitoring to complete
                try:
                    await self.position_manager._monitor_task
                except asyncio.CancelledError:
                    logger.info("Trading engine stopped")
            else:
                logger.error("Failed to open market position")

        except Exception as e:
            logger.error(f"Error in trading engine: {e}")
        finally:
            # Close exchange connection
            if self._fast_exchange:
                await self._fast_exchange.close()

    async def shutdown(self):
        """Gracefully shutdown the trading engine"""
        logger.info("Shutting down trading engine...")

        if self.position_manager:
            await self.position_manager.stop_monitoring()
            await self.position_manager.close_all_orders()

        if self._fast_exchange:
            await self._fast_exchange.close()


async def main():
    """Main function"""
    # Load config from file
    config = TradingConfig.from_file("config.json")

    # Initialize trading engine with config
    engine = TradingEngine(config)

    try:
        await engine.initialize()
        await engine.run()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        await engine.shutdown()


if __name__ == "__main__":
    asyncio.run(main())