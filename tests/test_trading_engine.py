# tests/test_trading_engine.py
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal
import json
from datetime import datetime
from app.main import (
    TradingEngine,
    PositionManager,
    FastExchange,
    TradingConfig,
    OrderType,
    OrderStatus,
    PositionSide,
)
import pytest_asyncio

# Import MOCK_CONFIG from conftest
from tests.conftest import MOCK_CONFIG

# Mock data for testing
MOCK_MARKET_INFO = {
    "symbol": "BTC/USDT:USDT",
    "type": "swap",
    "settle": "USDT",
    "precision": {"amount": 0.0001, "price": 0.1},
    "limits": {"amount": {"min": 0.001, "max": 1000}},
}

MOCK_POSITION = {
    "symbol": "BTC/USDT:USDT",
    "contracts": 0.01,
    "entryPrice": 50000.0,
    "side": "long",
    "leverage": 10,
}

MOCK_TICKER = {"last": 50000.0, "bid": 49999.0, "ask": 50001.0}


class TestTradingEngine:
    """Test the main TradingEngine functionality"""

    @pytest_asyncio.fixture
    async def mock_exchange(self):
        """Create a mock exchange instance"""
        mock_exchange = AsyncMock()
        mock_exchange.load_markets = AsyncMock(return_value={"symbol": "BTC/USDT:USDT"})
        mock_exchange.set_leverage = AsyncMock()
        mock_exchange.market = AsyncMock(
            return_value=MOCK_MARKET_INFO
        )  # Make this async
        mock_exchange.fetch_ticker = AsyncMock(return_value=MOCK_TICKER)
        mock_exchange.create_market_order = AsyncMock(
            return_value={"id": "order_1", "status": "closed"}
        )
        mock_exchange.fetch_order = AsyncMock(
            return_value={"id": "order_1", "status": "closed", "filled": 0.01}
        )
        mock_exchange.fetch_positions = AsyncMock(return_value=[MOCK_POSITION])
        mock_exchange.create_order = AsyncMock()
        mock_exchange.fetch_open_orders = AsyncMock(return_value=[])
        mock_exchange.cancel_order = AsyncMock()

        return mock_exchange

    @pytest_asyncio.fixture
    async def trading_engine(self, mock_exchange):
        """Create a TradingEngine instance with mocked dependencies"""
        with patch("app.main.ccxt.bybit", return_value=mock_exchange):
            with patch("app.main.FastExchange") as mock_fast_exchange:
                mock_fast_exchange_instance = AsyncMock()
                mock_fast_exchange_instance.exchange = mock_exchange

                # Fix the safe_request mock to properly await async methods
                async def safe_request_side_effect(method, *args, **kwargs):
                    method_func = getattr(mock_exchange, method)
                    # If it's an AsyncMock, await it
                    if asyncio.iscoroutinefunction(method_func):
                        return await method_func(*args, **kwargs)
                    else:
                        # For regular methods, just call them
                        return method_func(*args, **kwargs)

                mock_fast_exchange_instance.safe_request = AsyncMock(
                    side_effect=safe_request_side_effect
                )
                mock_fast_exchange.return_value = mock_fast_exchange_instance

                engine = TradingEngine("./config.json")
                engine.config = TradingConfig(
                    account=MOCK_CONFIG["account"],
                    symbol=MOCK_CONFIG["symbol"],
                    side=MOCK_CONFIG["side"],
                    market_order_amount=MOCK_CONFIG["market_order_amount"],
                    stop_loss_percent=MOCK_CONFIG["stop_loss_percent"],
                    trailing_sl_offset_percent=MOCK_CONFIG[
                        "trailing_sl_offset_percent"
                    ],
                    limit_orders_amount=MOCK_CONFIG["limit_orders_amount"],
                    leverage=MOCK_CONFIG["leverage"],
                    move_sl_to_breakeven=MOCK_CONFIG["move_sl_to_breakeven"],
                    tp_orders=MOCK_CONFIG["tp_orders"],
                    limit_orders=MOCK_CONFIG["limit_orders"],
                    api_timeout=MOCK_CONFIG["api_timeout"],
                    max_retries=MOCK_CONFIG["max_retries"],
                )

                yield engine, mock_exchange, mock_fast_exchange_instance

    @pytest.mark.asyncio
    async def test_engine_initialization(self, trading_engine):
        engine, mock_exchange, mock_fast_exchange = trading_engine
        assert engine is not None
        assert mock_exchange is not None
        assert mock_fast_exchange is not None

    @pytest.mark.asyncio
    async def test_market_position_opening(self, trading_engine):
        """Test opening a market position with correct parameters"""
        engine, mock_exchange, mock_fast_exchange = trading_engine
        engine.init_exchange = AsyncMock()

        await engine.initialize()
        assert engine is not None

        engine.position_manager = PositionManager(mock_fast_exchange, engine.config)
        # Make sure to await the initialize method
        await engine.position_manager.initialize()

        # Test market order execution
        success = await engine.position_manager.open_market_position()

        assert success == True
        mock_exchange.create_market_order.assert_called_once()
        mock_exchange.fetch_order.assert_called_with("order_1", "BTC/USDT:USDT")
        mock_exchange.fetch_positions.assert_called_with(["BTC/USDT:USDT"])

    @pytest.mark.asyncio
    async def test_tp_orders_placement(self, trading_engine):
        """Test TP orders are placed with correct parameters"""
        engine, mock_exchange, mock_fast_exchange = await trading_engine

        await engine.initialize()
        engine.position_manager = PositionManager(mock_fast_exchange, engine.config)
        await engine.position_manager.initialize()

        # Set up position
        engine.position_manager.position = MOCK_POSITION
        engine.position_manager.average_entry_price = 50000.0

        # Test TP orders placement
        success = await engine.position_manager.place_tp_orders()

        assert success == True
        assert mock_exchange.create_order.call_count == 2  # Two TP orders

        # Verify TP order parameters
        calls = mock_exchange.create_order.call_args_list
        for i, call in enumerate(calls):
            args, kwargs = call
            symbol, order_type, side, amount, price = args
            params = kwargs.get("params", {})

            assert symbol == "BTC/USDT:USDT"
            assert order_type == OrderType.LIMIT
            assert params.get("reduceOnly") == True

            if i == 0:  # First TP order
                expected_price = 50000.0 * 1.01  # 1% TP
                assert abs(price - expected_price) < 0.1
            else:  # Second TP order
                expected_price = 50000.0 * 1.02  # 2% TP
                assert abs(price - expected_price) < 0.1


class TestPositionManager:
    """Test PositionManager specific functionality"""

    @pytest_asyncio.fixture
    async def position_manager(self, mock_exchange):
        """Create a PositionManager instance"""
        with patch("app.main.FastExchange") as mock_fast_exchange:
            mock_fast_exchange_instance = AsyncMock()
            mock_fast_exchange_instance.exchange = mock_exchange
            mock_fast_exchange_instance.safe_request = AsyncMock(
                side_effect=lambda method, *args, **kwargs: getattr(
                    mock_exchange, method
                )(*args, **kwargs)
            )
            mock_fast_exchange.return_value = mock_fast_exchange_instance

            config = TradingConfig(
                account=MOCK_CONFIG["account"],
                symbol=MOCK_CONFIG["symbol"],
                side=MOCK_CONFIG["side"],
                market_order_amount=MOCK_CONFIG["market_order_amount"],
                stop_loss_percent=MOCK_CONFIG["stop_loss_percent"],
                trailing_sl_offset_percent=MOCK_CONFIG["trailing_sl_offset_percent"],
                limit_orders_amount=MOCK_CONFIG["limit_orders_amount"],
                leverage=MOCK_CONFIG["leverage"],
                move_sl_to_breakeven=MOCK_CONFIG["move_sl_to_breakeven"],
                tp_orders=MOCK_CONFIG["tp_orders"],
                limit_orders=MOCK_CONFIG["limit_orders"],
                api_timeout=MOCK_CONFIG["api_timeout"],
                max_retries=MOCK_CONFIG["max_retries"],
            )
            manager = PositionManager(mock_fast_exchange_instance, config)

            yield manager, mock_exchange

    @pytest.mark.asyncio
    async def test_averaging_orders_placement(self, position_manager):
        """Test averaging orders are placed in correct range"""
        manager, mock_exchange = await position_manager

        manager.average_entry_price = 50000.0
        manager.market_info = MOCK_MARKET_INFO

        success = await manager.place_averaging_orders()

        assert success == True
        assert (
            mock_exchange.create_limit_order.call_count == 3
        )  # Three averaging orders

        # Verify order prices are within expected range
        calls = mock_exchange.create_limit_order.call_args_list
        prices = [call[0][4] for call in calls]  # Price is the 5th argument

        # For long position, orders should be below current price
        expected_min_price = 50000.0 * 0.98  # 2% below
        expected_max_price = 50000.0 * 0.99  # 1% below (middle of range)

        for price in prices:
            assert expected_min_price <= price <= expected_max_price

    @pytest.mark.asyncio
    async def test_tp_orders_recalculation(self, position_manager):
        """Test TP orders are recalculated after averaging"""
        manager, mock_exchange = await position_manager

        # Initial position
        manager.position = MOCK_POSITION
        manager.average_entry_price = 50000.0
        manager.market_info = MOCK_MARKET_INFO

        # Place initial TP orders
        await manager.place_tp_orders()
        initial_calls = len(mock_exchange.create_order.call_args_list)

        # Simulate averaging - new average price
        manager.average_entry_price = 49000.0  # Lower average price

        # Recalculate TP orders
        await manager.update_tp_orders()

        # Should cancel old orders and place new ones
        mock_exchange.cancel_order.assert_called()
        assert mock_exchange.create_order.call_count > initial_calls

        # Verify new TP prices are based on new average
        calls = mock_exchange.create_order.call_args_list
        recent_calls = calls[-2:]  # Last two calls (new TP orders)

        for call in recent_calls:
            args, kwargs = call
            price = args[4]  # Price is the 5th argument

            # Should be based on 49000, not 50000
            if "1.01" in str(call):  # First TP order
                expected_price = 49000.0 * 1.01
                assert abs(price - expected_price) < 0.1
            else:  # Second TP order
                expected_price = 49000.0 * 1.02
                assert abs(price - expected_price) < 0.1

    @pytest.mark.asyncio
    async def test_order_monitoring(self, position_manager):
        """Test order monitoring detects filled orders"""
        manager, mock_exchange = await position_manager

        # Mock open orders
        mock_order = {
            "id": "avg_order_1",
            "symbol": "BTC/USDT:USDT",
            "reduceOnly": False,
            "status": "open",
        }
        manager.open_orders = [mock_order]

        # Mock that order was filled (not in open orders anymore)
        mock_exchange.fetch_open_orders.return_value = []
        mock_exchange.fetch_order.return_value = {
            "id": "avg_order_1",
            "status": "closed",
            "filled": 0.005,
        }

        # Mock position update
        manager.update_position_info = AsyncMock(return_value=True)
        manager.update_tp_orders = AsyncMock()

        await manager.check_orders()

        # Should detect filled order and update position/TP
        manager.update_position_info.assert_called_once()
        manager.update_tp_orders.assert_called_once()
        assert len(manager.open_orders) == 0  # Order should be removed


class TestConfigurationHandling:
    """Test configuration validation and handling"""

    def test_config_validation(self):
        """Test that configuration validation works correctly"""
        # Valid config
        valid_config = MOCK_CONFIG.copy()
        config = TradingConfig(
            account=valid_config["account"],
            symbol=valid_config["symbol"],
            side=valid_config["side"],
            market_order_amount=valid_config["market_order_amount"],
            stop_loss_percent=valid_config["stop_loss_percent"],
            trailing_sl_offset_percent=valid_config["trailing_sl_offset_percent"],
            limit_orders_amount=valid_config["limit_orders_amount"],
            leverage=valid_config["leverage"],
            move_sl_to_breakeven=valid_config["move_sl_to_breakeven"],
            tp_orders=valid_config["tp_orders"],
            limit_orders=valid_config["limit_orders"],
            api_timeout=valid_config["api_timeout"],
            max_retries=valid_config["max_retries"],
        )
        assert config.symbol == "BTC/USDT:USDT"

        # Invalid TP orders (total > 100%)
        invalid_config = MOCK_CONFIG.copy()
        invalid_config["tp_orders"] = [
            {"price_percent": 1.0, "quantity_percent": 60.0},
            {"price_percent": 2.0, "quantity_percent": 60.0},  # Total 120%
        ]

        with pytest.raises(
            ValueError, match="Total TP quantity percentage cannot exceed 100%"
        ):
            TradingConfig(
                account=invalid_config["account"],
                symbol=invalid_config["symbol"],
                side=invalid_config["side"],
                market_order_amount=invalid_config["market_order_amount"],
                stop_loss_percent=invalid_config["stop_loss_percent"],
                trailing_sl_offset_percent=invalid_config["trailing_sl_offset_percent"],
                limit_orders_amount=invalid_config["limit_orders_amount"],
                leverage=invalid_config["leverage"],
                move_sl_to_breakeven=invalid_config["move_sl_to_breakeven"],
                tp_orders=invalid_config["tp_orders"],
                limit_orders=invalid_config["limit_orders"],
                api_timeout=invalid_config["api_timeout"],
                max_retries=invalid_config["max_retries"],
            )


@pytest.mark.asyncio
async def test_contract_size_calculation():
    """Test contract size calculation for different market types"""
    manager = PositionManager(
        None,
        TradingConfig(
            account=MOCK_CONFIG["account"],
            symbol=MOCK_CONFIG["symbol"],
            side=MOCK_CONFIG["side"],
            market_order_amount=MOCK_CONFIG["market_order_amount"],
            stop_loss_percent=MOCK_CONFIG["stop_loss_percent"],
            trailing_sl_offset_percent=MOCK_CONFIG["trailing_sl_offset_percent"],
            limit_orders_amount=MOCK_CONFIG["limit_orders_amount"],
            leverage=MOCK_CONFIG["leverage"],
            move_sl_to_breakeven=MOCK_CONFIG["move_sl_to_breakeven"],
            tp_orders=MOCK_CONFIG["tp_orders"],
            limit_orders=MOCK_CONFIG["limit_orders"],
            api_timeout=MOCK_CONFIG["api_timeout"],
            max_retries=MOCK_CONFIG["max_retries"],
        ),
    )
    manager.market_info = MOCK_MARKET_INFO

    # USDT perpetual calculation
    amount_usdt = 100.0
    price = 50000.0
    contract_size = await manager.calculate_contract_size(amount_usdt, price)

    expected_size = amount_usdt / price  # 100 / 50000 = 0.002
    assert abs(contract_size - expected_size) < 0.0001

    # Test precision rounding
    manager.market_info["precision"]["amount"] = 0.001
    contract_size = await manager.calculate_contract_size(amount_usdt, price)
    assert contract_size == 0.002  # Rounded to 0.001 precision


# Test API endpoints
class TestAPIEndpoints:
    """Test FastAPI endpoints"""

    @pytest.fixture
    async def client(self):
        """Create test client"""
        from app.api import app
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            yield client

    def test_health_endpoint(self, client):
        """Test health endpoint returns correct response"""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_status_endpoint_no_engine(self, client):
        """Test status endpoint when no engine is running"""
        response = client.get("/status")
        assert response.status_code == 200
        assert response.json()["status"] == "idle"

    @patch("app.api.engine")
    def test_status_endpoint_with_engine(self, mock_engine, client):
        """Test status endpoint with active engine"""
        # Mock active engine
        mock_engine.position_manager.average_entry_price = 50000.0
        mock_engine.position_manager.entry_time = datetime.now()
        mock_engine.config.symbol = "BTC/USDT:USDT"
        mock_engine.config.side = "long"
        mock_engine.position_manager.open_orders = []
        mock_engine.position_manager._running = True

        response = client.get("/status")
        assert response.status_code == 200
        assert response.json()["status"] == "active"
        assert response.json()["position"]["symbol"] == "BTC/USDT:USDT"


# Integration test
@pytest.mark.integration
class TestIntegration:
    """Integration tests (run with --run-integration flag)"""

    @pytest.mark.asyncio
    async def test_full_trading_cycle(self, trading_engine):
        """Test complete trading cycle from start to monitoring"""
        engine, mock_exchange, mock_fast_exchange = await trading_engine

        # Mock all necessary methods for full cycle
        mock_exchange.fetch_balance.return_value = {"USDT": {"free": 1000.0}}

        await engine.initialize()

        # Start trading
        await engine.run()

        # Verify all expected methods were called
        mock_exchange.load_markets.assert_called()
        mock_exchange.set_leverage.assert_called()
        mock_exchange.create_market_order.assert_called()
        mock_exchange.fetch_order.assert_called()
        mock_exchange.fetch_positions.assert_called()

        # Should have started monitoring
        assert hasattr(engine.position_manager, "_monitor_task")
