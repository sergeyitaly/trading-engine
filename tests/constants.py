# tests/constants.py
MOCK_CONFIG = {
    "account": "bybit_testnet",
    "symbol": "BTC/USDT:USDT",
    "side": "long",
    "market_order_amount": 100.0,
    "stop_loss_percent": 2.0,
    "trailing_sl_offset_percent": 1.0,
    "limit_orders_amount": 50.0,
    "leverage": 10,
    "move_sl_to_breakeven": True,
    "tp_orders": [
        {"price_percent": 1.0, "quantity_percent": 50.0},
        {"price_percent": 2.0, "quantity_percent": 50.0},
    ],
    "limit_orders": {"range_percent": 2.0, "orders_count": 3, "amount_per_order": 25.0},
    "api_timeout": 10,
    "max_retries": 3,
}
