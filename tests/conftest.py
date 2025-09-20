# tests/conftest.py
import pytest
import json
from unittest.mock import patch, MagicMock

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
        {"price_percent": 2.0, "quantity_percent": 50.0}
    ],
    "limit_orders": {
        "range_percent": 2.0,
        "orders_count": 3,
        "amount_per_order": 25.0
    },
    "api_timeout": 10,
    "max_retries": 3
}

@pytest.fixture
def mock_config_file():
    """Mock config file reading"""
    with patch('builtins.open', MagicMock()) as mock_open:
        mock_file = MagicMock()
        mock_file.__enter__.return_value.read.return_value = json.dumps(MOCK_CONFIG)
        mock_open.return_value = mock_file
        yield