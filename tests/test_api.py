# tests/test_api.py
import pytest
from unittest.mock import patch, MagicMock
import httpx
from app.api import app

@pytest.mark.asyncio
async def test_start_trading_endpoint():
    """Test the /start endpoint"""
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        with patch('app.api.engine') as mock_engine:
            mock_engine.start_trading = MagicMock(return_value={"status": "success"})
            
            response = await client.post("/start", json={
                "credentials": {
                    "api_key": "test",
                    "api_secret": "test"
                }
            })
            
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"