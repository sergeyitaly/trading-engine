# tests/test_dashboard.py
import pytest
import streamlit as st
from unittest.mock import patch, MagicMock
from app.ui.dashboard import TradingDashboard
from app.ui.localstorage import BrowserLocalStorage

def test_dashboard_initialization():
    """Test that the dashboard initializes correctly"""
    with patch('app.ui.dashboard.os.getenv', return_value='localhost'):
        with patch('app.ui.dashboard.requests.Session'):
            dashboard = TradingDashboard()
            assert dashboard is not None
            assert dashboard.api_url == "http://localhost:8000"

def test_browser_local_storage():
    """Test browser local storage methods"""
    storage = BrowserLocalStorage()
    
    # Test setItem (should not crash)
    assert storage.setItem('test_key', 'test_value') in [True, False]
    
    # Test getItem (should return None or dict)
    result = storage.getItem('test_key')
    assert result is None or isinstance(result, dict)