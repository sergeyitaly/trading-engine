from app.main import TPOrderConfig

MOCK_CONFIG = {
    "account": {
        "apiKey": "test_key",
        "secret": "test_secret",
        "password": "test_password",
    },
    "symbol": "BTC/USDT:USDT",
    "side": "long",
    "market_order_amount": 100.0,
    "stop_loss_percent": 2.0,
    "trailing_sl_offset_percent": 1.0,
    "limit_orders_amount": 300.0,
    "leverage": 10,
    "move_sl_to_breakeven": True,
    "tp_orders": [
        TPOrderConfig(price_percent=1.01, quantity_percent=50.0),
        TPOrderConfig(price_percent=1.02, quantity_percent=50.0),
    ],
    "limit_orders": {"range_percent": 0.02, "orders_count": 3, "amount_percent": 0.5},
    "api_timeout": 30000,
    "max_retries": 3,
}
