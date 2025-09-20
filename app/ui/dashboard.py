# app/ui/dashboard.py
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import json
import requests
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import os
import asyncio
import websockets
import threading
import queue
import time
from pathlib import Path
import random
import re
from datetime import datetime
from streamlit_local_storage import LocalStorage
from localstorage import BrowserLocalStorage
# Set page config
st.set_page_config(
    page_title="Trading Engine Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Load external CSS - FIXED VERSION
def load_css(file_name):
    """Load external CSS file with proper path handling"""
    try:
        # Get the absolute path to the CSS file
        current_dir = Path(__file__).parent
        css_path = current_dir / file_name
        
        if css_path.exists():
            with open(css_path, 'r') as f:
                st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)
        else:
            # Try one level up (in case running from different directory)
            parent_css_path = current_dir.parent / file_name
            if parent_css_path.exists():
                with open(parent_css_path, 'r') as f:
                    st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)
            else:
                # Create a default CSS if file doesn't exist
                default_css = """
                <style>
                .stApp {
                    background-color: #f0f2f6;
                }
                .stButton>button {
                    background-color: #4CAF50;
                    color: white;
                    border-radius: 5px;
                }
                .position-long {
                    background-color: #e6f7e6;
                    padding: 15px;
                    border-radius: 5px;
                    border-left: 5px solid #4CAF50;
                }
                .position-short {
                    background-color: #ffe6e6;
                    padding: 15px;
                    border-radius: 5px;
                    border-left: 5px solid #ff4d4d;
                }
                .api-warning {
                    background-color: #fff3cd;
                    border: 1px solid #ffeaa7;
                    border-radius: 5px;
                    padding: 10px;
                    margin: 10px 0;
                }
                .credential-success {
                    background-color: #d4edda;
                    border: 1px solid #c3e6cb;
                    border-radius: 5px;
                    padding: 10px;
                    margin: 10px 0;
                }
                .credential-warning {
                    background-color: #fff3cd;
                    border: 1px solid #ffeaa7;
                    border-radius: 5px;
                    padding: 10px;
                    margin: 10px 0;
                }
                .credential-error {
                    background-color: #f8d7da;
                    border: 1px solid #f5c6cb;
                    border-radius: 5px;
                    padding: 10px;
                    margin: 10px 0;
                }
                </style>
                """
                st.markdown(default_css, unsafe_allow_html=True)
                
    except Exception as e:
        st.error(f"Error loading CSS: {e}")
        # Fallback to default CSS
        default_css = """
        <style>
        .stApp {
            background-color: #f0f2f6;
        }
        </style>
        """
        st.markdown(default_css, unsafe_allow_html=True)


def add_theme_toggle():
    """Add a theme toggle button to the dashboard"""
    st.markdown("""
    <button class="theme-toggle" onclick="toggleTheme()">🌓</button>
    
    <script>
    function toggleTheme() {
        const app = document.querySelector('.stApp');
        const currentTheme = app.getAttribute('data-theme') || 
                            (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
        
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
        app.setAttribute('data-theme', newTheme);
        
        // Save preference to localStorage
        localStorage.setItem('dashboard-theme', newTheme);
    }
    
    // Load saved theme preference
    document.addEventListener('DOMContentLoaded', function() {
        const savedTheme = localStorage.getItem('dashboard-theme');
        if (savedTheme) {
            document.querySelector('.stApp').setAttribute('data-theme', savedTheme);
        }
    });
    </script>
    """, unsafe_allow_html=True)


# Load the CSS file
load_css('style.css')

uri_host = os.getenv('API_HOST', 'localhost')

class WebSocketClient:
    def __init__(self, uri_host):
        self.uri = f"ws://{uri_host}:8000/ws"
        self.websocket = None
        self.message_queue = queue.Queue(maxsize=50)  
        self.connected = False
        self.running = False
        self.thread = None
        self.last_processed_time = 0
        self.process_interval = 0.2  # 200ms between processing batches
        self.connection_retries = 0
        self.max_retries = 5
        
    def start(self):
        """Start WebSocket connection in a separate thread"""
        if self.running:
            return
            
        self.running = True
        self.thread = threading.Thread(target=self._run_websocket, name="WebSocketThread")
        self.thread.daemon = True
        self.thread.start()
        
    def stop(self):
        """Stop WebSocket connection gracefully"""
        self.running = False
        if self.websocket:
            try:
                # Run in event loop for proper cleanup
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.websocket.close())
                loop.close()
            except Exception as e:
                print(f"Error closing WebSocket: {e}")
        
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)  # Wait up to 2 seconds
            
        self.connected = False
        if 'websocket_connected' in st.session_state:
            st.session_state.websocket_connected = False
            
    def _run_websocket(self):
        """Run WebSocket client in a separate thread with proper event loop handling"""
        try:
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._websocket_loop())
        except Exception as e:
            print(f"WebSocket thread error: {e}")
        finally:
            self.connected = False
            self.running = False
            if 'websocket_connected' in st.session_state:
                st.session_state.websocket_connected = False
        
    async def _websocket_loop(self):
        """WebSocket connection loop with improved error handling"""
        while self.running and self.connection_retries < self.max_retries:
            try:
                print(f"Attempting to connect to {self.uri} (attempt {self.connection_retries + 1}/{self.max_retries})")
                
                async with websockets.connect(
                    self.uri, 
                    ping_interval=30,    # Send ping every 30 seconds
                    ping_timeout=10,     # Wait 10 seconds for pong
                    close_timeout=3      # Wait 3 seconds for close
                ) as websocket:
                    
                    self.websocket = websocket
                    self.connected = True
                    self.connection_retries = 0
                    self.last_activity = time.time()
                    print("WebSocket connection established")
                    
                    # Send subscriptions
                    subscriptions = [
                        {"type": "subscribe", "channel": "status"},
                        {"type": "subscribe", "channel": "orders"}
                    ]
                    
                    for sub in subscriptions:
                        try:
                            await websocket.send(json.dumps(sub))
                            await asyncio.sleep(0.05)  # Small delay
                        except Exception as e:
                            print(f"Failed to send subscription: {e}")
                    
                    # Main message receiving loop
                    while self.running and self.connected:
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                            self.last_activity = time.time()
                            
                            # Add to queue with timeout
                            try:
                                self.message_queue.put(message, timeout=0.1)
                            except queue.Full:
                                # Queue full, remove oldest message
                                try:
                                    self.message_queue.get_nowait()
                                    self.message_queue.put(message, timeout=0.1)
                                except queue.Empty:
                                    pass
                                    
                        except asyncio.TimeoutError:
                            # Check if we should send a ping
                            if time.time() - self.last_activity > 25:
                                try:
                                    await websocket.ping()
                                    self.last_activity = time.time()
                                except:
                                    break
                            continue
                            
                        except websockets.exceptions.ConnectionClosed:
                            print("WebSocket connection closed by server")
                            break
                        except Exception as e:
                            print(f"Error receiving message: {e}")
                            break
                            
            except (ConnectionRefusedError, OSError) as e:
                print(f"Connection failed: {e}")
            except asyncio.TimeoutError:
                print("Connection timeout")
            except Exception as e:
                print(f"Unexpected error: {e}")
            
            # Connection failed or closed
            self.connected = False
            self.connection_retries += 1
            
            if self.connection_retries < self.max_retries:
                # Exponential backoff with jitter
                backoff = min(2 ** self.connection_retries, 10) + (random.random() * 2)
                print(f"Reconnecting in {backoff:.1f} seconds...")
                await asyncio.sleep(backoff)
            else:
                print("Max connection retries reached")
                break

    def get_messages(self):
        """Get messages from the queue with rate limiting"""
        current_time = time.time()
        if current_time - self.last_processed_time < self.process_interval:
            return []  # Rate limited
            
        self.last_processed_time = current_time
        
        messages = []
        max_messages = 20  # Process max 20 messages per batch
        
        while not self.message_queue.empty() and len(messages) < max_messages:
            try:
                message = self.message_queue.get_nowait()
                messages.append(message)
            except queue.Empty:
                break
                
        return messages
    
    def get_connection_status(self):
        """Get detailed connection status"""
        return {
            "connected": self.connected,
            "running": self.running,
            "retries": self.connection_retries,
            "max_retries": self.max_retries,
            "queue_size": self.message_queue.qsize(),
            "uri": self.uri
        }

class TradingDashboard:
    def __init__(self):
        api_host = os.getenv('API_HOST', 'localhost')
        self.api_url = f"http://{api_host}:8000"
        self.ws_uri = f"ws://{api_host}:8000/ws"
        self.websocket_client = WebSocketClient(uri_host=api_host)        
        self.session = self._create_session()
        self.local_storage = LocalStorage()
        
        # Initialize session state first
        if 'websocket_connected' not in st.session_state:
            st.session_state.websocket_connected = False
        if 'last_update' not in st.session_state:
            st.session_state.last_update = time.time()
        if 'connection_status' not in st.session_state:
            st.session_state.connection_status = "disconnected"
        
        # Initialize log-related session state
        if 'log_data' not in st.session_state:
            st.session_state.log_data = []
        if 'log_filter' not in st.session_state:
            st.session_state.log_filter = "all"
        if 'log_search' not in st.session_state:
            st.session_state.log_search = ""
        if 'connection_filter' not in st.session_state:
            st.session_state.connection_filter = "all"
            
        # Initialize credential storage - FIXED: Load credentials before getting config
        self.init_credential_storage()
        
        # Get config after credentials are initialized
        self.config = self.get_config()
        
    def init_credential_storage(self):
        """Initialize credential storage system with local storage - SIMPLIFIED VERSION"""
        # Use our custom implementation
        self.local_storage = BrowserLocalStorage()
        
        # Initialize saved_credentials
        if 'saved_credentials' not in st.session_state:
            # Try to load from local storage
            stored_creds = self.local_storage._getViaQueryParams("saved_credentials")
            
            if stored_creds and stored_creds.get('value'):
                try:
                    st.session_state.saved_credentials = json.loads(stored_creds['value'])
                    print("✅ Loaded credentials from local storage")
                except Exception as e:
                    print(f"Error parsing stored credentials: {e}")
                    st.session_state.saved_credentials = self.get_default_credentials()
            else:
                # Check if we have credentials in query params (from JavaScript redirect)
                query_params = st.query_params
                if "saved_credentials" in query_params:
                    try:
                        creds_json = query_params["saved_credentials"]
                        st.session_state.saved_credentials = json.loads(creds_json)
                        print("✅ Loaded credentials from query params")
                    except:
                        st.session_state.saved_credentials = self.get_default_credentials()
                else:
                    st.session_state.saved_credentials = self.get_default_credentials()
        
        # Initialize api_credentials
        if 'api_credentials' not in st.session_state:
            st.session_state.api_credentials = st.session_state.saved_credentials.copy()
                    
    def get_default_credentials(self):
        """Return default empty credentials structure"""
        return {
            "bybit_testnet": {"api_key": "", "api_secret": "", "source": "manual"},
            "bybit_mainnet": {"api_key": "", "api_secret": "", "source": "manual"},
            "gate_testnet": {"api_key": "", "api_secret": "", "source": "manual"},
            "gate_mainnet": {"api_key": "", "api_secret": "", "source": "manual"}
        }

    def save_credentials_to_storage(self):
        """Save credentials to browser local storage - SIMPLIFIED"""
        try:
            # Save to session state
            st.session_state.saved_credentials = st.session_state.api_credentials.copy()
            
            # Convert to JSON string for storage
            credentials_json = json.dumps(st.session_state.api_credentials)
            
            # Save to local storage using our custom method
            success = self.local_storage.setItem("saved_credentials", credentials_json)
            
            # Also store in query params as a backup
            st.query_params["saved_credentials"] = credentials_json
            
            print(f"✅ Saved credentials to storage: {success}")
            return success
            
        except Exception as e:
            print(f"Error saving credentials: {e}")
            return False
        
        
    def load_credentials_from_storage(self):
        """Load credentials from browser local storage"""
        try:
            stored_creds = self.local_storage.getItem("saved_credentials")
            if stored_creds and stored_creds.get('value'):
                st.session_state.api_credentials = json.loads(stored_creds['value'])
                st.session_state.saved_credentials = st.session_state.api_credentials.copy()
                return True
        except Exception as e:
            print(f"Error loading credentials from local storage: {e}")
        return False
        
    def load_credentials_from_env(self):
        """Load credentials from environment variables"""
        env_credentials = {}
        
        # Check for Bybit credentials in env
        bybit_testnet_key = os.getenv("BYBIT_TESTNET_API_KEY")
        bybit_testnet_secret = os.getenv("BYBIT_TESTNET_API_SECRET")
        bybit_mainnet_key = os.getenv("BYBIT_API_KEY")
        bybit_mainnet_secret = os.getenv("BYBIT_API_SECRET")
        
        # Check for Gate.io credentials in env - use correct variable names
        gate_testnet_key = os.getenv("GATEIO_TESTNET_API_KEY") or os.getenv("GATE_TESTNET_API_KEY")
        gate_testnet_secret = os.getenv("GATEIO_TESTNET_API_SECRET") or os.getenv("GATE_TESTNET_API_SECRET")
        gate_mainnet_key = os.getenv("GATEIO_API_KEY") or os.getenv("GATE_API_KEY")
        gate_mainnet_secret = os.getenv("GATEIO_API_SECRET") or os.getenv("GATE_API_SECRET")
        
        if bybit_testnet_key and bybit_testnet_secret:
            env_credentials["bybit_testnet"] = {
                "api_key": bybit_testnet_key,
                "api_secret": bybit_testnet_secret,
                "source": "env"
            }
        
        if bybit_mainnet_key and bybit_mainnet_secret:
            env_credentials["bybit_mainnet"] = {
                "api_key": bybit_mainnet_key,
                "api_secret": bybit_mainnet_secret,
                "source": "env"
            }
        
        if gate_testnet_key and gate_testnet_secret:
            env_credentials["gate_testnet"] = {
                "api_key": gate_testnet_key,
                "api_secret": gate_testnet_secret,
                "source": "env"
            }
        
        if gate_mainnet_key and gate_mainnet_secret:
            env_credentials["gate_mainnet"] = {
                "api_key": gate_mainnet_key,
                "api_secret": gate_mainnet_secret,
                "source": "env"
            }
        
        return env_credentials
    
    def get_current_credentials(self, account_type):
        """Get credentials for current account, preferring manual over env"""
        # Get manual credentials if available
        manual_creds = st.session_state.api_credentials.get(account_type, {})
        
        if manual_creds.get("api_key") and manual_creds.get("api_secret"):
            return manual_creds  # Prefer manual credentials
        
        # Fall back to env credentials
        env_creds = self.load_credentials_from_env().get(account_type, {})
        if env_creds.get("api_key") and env_creds.get("api_secret"):
            return env_creds
        
        return {"api_key": "", "api_secret": "", "source": "none"}
    
    def _create_session(self):
        """Create a session with retry logic and timeouts"""
        session = requests.Session()
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        
        # Mount adapter with retry strategy
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
            
    def load_credentials_from_env(self):
        """Load credentials from environment variables"""
        env_credentials = {}
        
        # Check for Bybit credentials in env
        bybit_testnet_key = os.getenv('BYBIT_TESTNET_API_KEY')
        bybit_testnet_secret = os.getenv('BYBIT_TESTNET_API_SECRET')
        bybit_mainnet_key = os.getenv('BYBIT_API_KEY')
        bybit_mainnet_secret = os.getenv('BYBIT_API_SECRET')
        
        # Check for Gate.io credentials in env
        gate_testnet_key = os.getenv('GATE_TESTNET_API_KEY')
        gate_testnet_secret = os.getenv('GATE_TESTNET_API_SECRET')
        gate_mainnet_key = os.getenv('GATE_API_KEY')
        gate_mainnet_secret = os.getenv('GATE_API_SECRET')
        
        if bybit_testnet_key and bybit_testnet_secret:
            env_credentials["bybit_testnet"] = {
                "api_key": bybit_testnet_key,
                "api_secret": bybit_testnet_secret,
                "source": "env"
            }
        
        if bybit_mainnet_key and bybit_mainnet_secret:
            env_credentials["bybit_mainnet"] = {
                "api_key": bybit_mainnet_key,
                "api_secret": bybit_mainnet_secret,
                "source": "env"
            }
        
        if gate_testnet_key and gate_testnet_secret:
            env_credentials["gate_testnet"] = {
                "api_key": gate_testnet_key,
                "api_secret": gate_testnet_secret,
                "source": "env"
            }
        
        if gate_mainnet_key and gate_mainnet_secret:
            env_credentials["gate_mainnet"] = {
                "api_key": gate_mainnet_key,
                "api_secret": gate_mainnet_secret,
                "source": "env"
            }
        
        return env_credentials
    
    def _make_request(self, endpoint, method="GET", json_data=None, timeout=5):
        """Make API request with timeout handling and better error reporting"""
        try:
            url = f"{self.api_url}/{endpoint}"
            
            # Add debug logging
            print(f"DEBUG - Making {method} request to {url}")
            if json_data:
                print(f"DEBUG - Request payload: {json.dumps(json_data, indent=2)}")
            
            if method == "GET":
                response = self.session.get(url, timeout=timeout)
            elif method == "POST":
                response = self.session.post(url, json=json_data, timeout=timeout)
            
            # Log response details for debugging
            print(f"DEBUG - Response status: {response.status_code}")
            print(f"DEBUG - Response text: {response.text[:500]}...")  # First 500 chars
            
            response.raise_for_status()
            
            try:
                return response.json()
            except json.JSONDecodeError:
                return {"status": "error", "message": f"Invalid JSON response: {response.text}"}
                
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 500:
                # Try to get more details from 500 errors
                try:
                    error_detail = e.response.json()
                    error_msg = error_detail.get('detail', error_detail.get('message', str(e)))
                except:
                    error_msg = f"Server error (500): {str(e)}"
                
                return {"status": "error", "message": error_msg}
            else:
                error_msg = f"HTTP error {e.response.status_code} from {endpoint}: {str(e)}"
                return {"status": "error", "message": error_msg}
                
        except requests.exceptions.ConnectionError:
            error_msg = f"Cannot connect to trading engine at {self.api_url}. Please make sure the server is running."
            return {"status": "error", "message": error_msg}
            
        except requests.exceptions.Timeout:
            error_msg = f"Request to {endpoint} timed out after {timeout}s"
            return {"status": "error", "message": error_msg}
            
        except Exception as e:
            error_msg = f"Unexpected error with {endpoint}: {str(e)}"
            return {"status": "error", "message": error_msg}
                
    def init_websocket(self):
        """Initialize WebSocket connection"""
        if self.websocket_client is None:
            api_host = os.getenv('API_HOST', 'localhost')
            self.websocket_client = WebSocketClient(uri_host=api_host)
            
        if not self.websocket_client.running:
            self.websocket_client.start()
            # Wait a moment for connection attempt
            time.sleep(0.5)
            
            if self.websocket_client.connected:
                st.session_state.websocket_connected = True
                st.session_state.connection_status = "connected"
            else:
                st.session_state.connection_status = "connecting"
                
            st.rerun()
            
    def close_websocket(self):
        """Close WebSocket connection"""
        if self.websocket_client:
            self.websocket_client.stop()
            self.websocket_client = None
            st.session_state.websocket_connected = False
    
    def process_websocket_messages(self):
        """Process WebSocket messages and update session state"""
        if self.websocket_client and self.websocket_client.connected:
            messages = self.websocket_client.get_messages()
            for message in messages:
                try:
                    data = json.loads(message)
                    channel = data.get('channel')
                    
                    if channel == 'status':
                        st.session_state.last_status = data.get('data', {})
                        st.session_state.last_update = time.time()
                    elif channel == 'orders':
                        st.session_state.last_orders = data.get('data', {}).get('orders', [])
                        st.session_state.last_update = time.time()
                    elif channel == 'price':
                        st.session_state.last_price = data.get('data', {})
                        st.session_state.last_update = time.time()
                        
                except json.JSONDecodeError:
                    print(f"Invalid JSON message: {message}")
    
    def get_status(self):
        """Get current trading status - use WebSocket if available, fallback to HTTP"""
        # Check if we have recent WebSocket data
        current_time = time.time()
        if (hasattr(st.session_state, 'last_status') and 
            hasattr(st.session_state, 'last_update') and 
            current_time - st.session_state.last_update < 5):  # 5 second cache
            status_data = st.session_state.last_status
            print(f"DEBUG - Using cached status: {status_data}")
            return status_data
        
        # Fallback to HTTP request
        status = self._make_request("status")
        print(f"DEBUG - HTTP status response: {status}")
        return status or {"status": "disconnected"}
    
    def get_orders(self):
        """Get current orders - use WebSocket if available, fallback to HTTP"""
        # Check if we have recent WebSocket data
        current_time = time.time()
        if (hasattr(st.session_state, 'last_orders') and 
            hasattr(st.session_state, 'last_update') and 
            current_time - st.session_state.last_update < 5):  # 5 second cache
            return {"orders": st.session_state.last_orders}
        
        # Fallback to HTTP request
        orders = self._make_request("orders")
        return orders or {"orders": []}
    
    def get_config(self):
        """Get configuration from API"""
        config = self._make_request("config")
        if config is not None:
            account = config.get("account", "").lower()
            symbol = config.get("symbol", "")
            if account and symbol:
                # Store both display and API formats
                symbol_info = self.validate_symbol_format(symbol, account)
                config["symbol_display"] = symbol_info["display"]
                config["symbol_api"] = symbol_info["api"]
            return config
            
    def save_config(self, config):
        """Save configuration via API - handle symbol format conversion"""
        # Create a copy for sending to API
        config_to_save = config.copy()
        
        # Convert symbol to API format for the specific exchange
        account = config_to_save.get("account", "").lower()
        symbol = config_to_save.get("symbol", "")
        
        if account and symbol:
            symbol_info = self.validate_symbol_format(symbol, account)
            config_to_save["symbol"] = symbol_info["api"]  # Send API format to backend
            
        result = self._make_request("config", method="POST", json_data=config_to_save)
        return result is not None
   
    def validate_symbol_format(self, symbol, exchange):
        """Validate and format symbol based on exchange - keep display format but convert for API"""
        symbol = symbol.upper().strip()
        
        # For display purposes, keep the original format
        display_symbol = symbol
        
        # For API calls, convert to exchange-specific format
        api_symbol = symbol
        if "bybit" in exchange.lower():
            # Bybit uses format like BTCUSDT (no slash)
            api_symbol = symbol.replace("/", "").replace("_", "")
        elif "gate" in exchange.lower():
            # Gate.io uses format like BTC_USDT (with underscore)
            if "/" in symbol:
                api_symbol = symbol.replace("/", "_")
            elif len(symbol) == 6 and "_" not in symbol:
                api_symbol = f"{symbol[:3]}_{symbol[3:]}"
        
        return {
            "display": display_symbol,  # For UI: "BTC/USDT"
            "api": api_symbol           # For API: "BTCUSDT" 
        }
    
    def validate_credentials(self):
        """Validate that credentials are available for the current account"""
        account_type = self.get_account_type(self.config["account"])
        current_creds = self.get_current_credentials(account_type)
        
        if not current_creds.get("api_key") or not current_creds.get("api_secret"):
            return False, f"Please configure API credentials for {self.config['account']} in the sidebar"
        
        return True, f"Credentials from {current_creds.get('source', 'unknown')} source"

    def prepare_credentials_payload(self):
        """Prepare credentials payload for API requests"""
        account_type = self.get_account_type(self.config["account"])
        current_creds = self.get_current_credentials(account_type)
        
        payload = {
            "api_key": current_creds.get("api_key", ""),
            "api_secret": current_creds.get("api_secret", "")
        }
        
        # Add testnet flag for Bybit
        if "bybit" in account_type:
            payload["testnet"] = "testnet" in account_type
        
        return payload

    def test_connection(self):
        """Test exchange connection by trying to start trading with validation"""
        # First validate credentials
        credentials_valid, error_msg = self.validate_credentials()
        if not credentials_valid:
            return {"status": "error", "message": f"❌ {error_msg}"}
        
        # Get current credentials
        credentials_payload = self.prepare_credentials_payload()
        
        symbol_for_api = self.config.get("symbol_api", self.config["symbol"].replace("/", ""))
        
        # Create config copy with only the necessary fields
        config_copy = {
            "account": self.config["account"],
            "symbol": symbol_for_api,
            "side": self.config["side"],
            "market_order_amount": self.config["market_order_amount"],
            "leverage": self.config["leverage"]
        }
        
        # Try to start trading with validation only
        payload = {
            "config": config_copy,
            "credentials": credentials_payload,
            "validate_only": True
        }
        
        result = self._make_request("start", method="POST", json_data=payload)
        
        if result:
            if result.get("status") == "success":
                return {"status": "success", "message": "✅ Exchange connection successful!"}
            else:
                error_msg = result.get('message', 'Connection test failed')
                
                # Handle Gate.io futures account error specifically
                if "USER_NOT_FOUND" in error_msg and "transfer funds first" in error_msg:
                    return {
                        "status": "error", 
                        "message": "❌ Gate.io Futures Account Not Found. Please deposit and transfer funds to futures wallet first."
                    }
                
                user_friendly_error = self.handle_order_error(error_msg)
                return {"status": "error", "message": user_friendly_error}
        else:
            return {"status": "error", "message": "❌ Failed to connect to trading engine. Please check if the server is running."}
        
    def handle_order_error(self, error_message):
        """Handle order errors and display user-friendly messages"""
        # Check if the error message is already formatted with emoji
        if error_message.startswith("❌") or error_message.startswith("✅"):
            return error_message  # Already formatted, return as-is
        
        if "USER_NOT_FOUND" in error_message and "transfer funds first to create futures account" in error_message:
            return "❌ Gate.io Futures Account Not Found. Please deposit funds into your Gate.io futures account first."
        
        # Check for API credentials error
        if "API credentials not found" in error_message or "credentials not found" in error_message.lower():
            return "❌ API credentials not configured. Please save your API credentials in the sidebar first."
        
        # Check for insufficient balance errors
        if ("Insufficient balance" in error_message or 
            "not enough for new order" in error_message or
            "ab not enough" in error_message):
            return "❌ Insufficient balance. Please check your account funds and reduce order size."
        
        elif "retCode" in error_message:
            # Extract the error code for more specific messaging
            try:
                # Try to parse the JSON error response
                if error_message.startswith("bybit "):
                    error_data = json.loads(error_message.replace("bybit ", ""))
                else:
                    error_data = json.loads(error_message)
                    
                error_code = error_data.get("retCode", "")
                error_msg = error_data.get("retMsg", "")
                
                # Map common Bybit error codes to user-friendly messages
                error_mapping = {
                    110007: "❌ Insufficient balance. Please deposit more funds or reduce order size.",
                    110043: "❌ Leverage not modified. The leverage may already be set to this value.",
                    170131: "❌ Insufficient balance. Please deposit more funds or reduce order size.",
                    10001: "❌ API key invalid. Please check your API credentials.",
                    10002: "❌ API key expired. Please generate new API keys.",
                    10003: "❌ IP restriction. Please add your IP to the API whitelist.",
                    10004: "❌ Permission denied. Check API key permissions.",
                    10005: "❌ Rate limit exceeded. Please wait before trying again.",
                    10006: "❌ Invalid request parameters. Please check your configuration.",
                    130021: "❌ Order price is out of permissible range.",
                    130006: "❌ Order quantity is too small.",
                    130010: "❌ Order quantity is too large.",
                }
                
                if error_code in error_mapping:
                    return error_mapping[error_code]
                else:
                    return f"❌ Exchange error ({error_code}): {error_msg}"
                    
            except json.JSONDecodeError:
                # If it's not JSON, check for common error patterns
                if "leverage not modified" in error_message:
                    return "❌ Leverage not modified. The leverage may already be set to this value."
                elif "not enough" in error_message.lower():
                    return "❌ Insufficient balance. Please check your account funds."
                elif "trading already in progress" in error_message.lower():
                    return "❌ Trading is already in progress. Please stop trading first before starting again."
                else:
                    return f"❌ Error: {error_message}"
        else:
            # Handle non-JSON error messages
            if "leverage not modified" in error_message:
                return "❌ Leverage not modified. The leverage may already be set to this value."
            elif "not enough" in error_message.lower():
                return "❌ Insufficient balance. Please check your account funds."
            elif "trading already in progress" in error_message.lower():
                return "❌ Trading is already in progress. Please stop trading first before starting again."
            else:
                return f"❌ Error: {error_message}"
                           
    def stop_trading(self):
        """Stop trading through API"""
        result = self._make_request("stop", method="POST")
        return result or {"status": "error", "message": "Failed to stop trading"}

    def start_trading(self):
        """Start trading through API with better error handling"""
        # First validate credentials
        credentials_valid, error_msg = self.validate_credentials()
        if not credentials_valid:
            return {"status": "error", "message": f"❌ {error_msg}"}
        
        # Get current credentials for the selected account
        credentials_payload = self.prepare_credentials_payload()
        
        # Only send credentials, config will be loaded from URL/local file by the API
        payload = {
            "credentials": credentials_payload
        }
        
        print(f"DEBUG - Sending start payload: {json.dumps(payload, indent=2)}")
        
        # Make the request with longer timeout and better error handling
        try:
            response = self.session.post(
                f"{self.api_url}/start",
                json=payload,
                timeout=10  # Longer timeout for start operation
            )
            
            print(f"DEBUG - Start response status: {response.status_code}")
            print(f"DEBUG - Start response text: {response.text}")
            
            response.raise_for_status()
            result = response.json()
            
            if result and result.get("status") == "success":
                # Give the engine a moment to initialize
                time.sleep(2)
                
                # Check status again to see if trading actually started successfully
                post_start_status = self.get_status()
                print(f"DEBUG - Status after starting: {post_start_status}")
                
                return {"status": "success", "message": "Trading started successfully!"}
            else:
                error_msg = result.get('message', 'Unknown error') if result else 'No response from server'
                user_friendly_error = self.handle_order_error(error_msg)
                return {"status": "error", "message": user_friendly_error}
                
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 500:
                # Try to get more details from the 500 error
                try:
                    error_detail = e.response.json()
                    error_msg = error_detail.get('detail', str(e))
                except:
                    error_msg = f"Server error (500): {str(e)}"
                
                return {"status": "error", "message": f"❌ Server error: {error_msg}"}
            else:
                error_msg = f"HTTP error {e.response.status_code}: {str(e)}"
                return {"status": "error", "message": f"❌ {error_msg}"}
                
        except requests.exceptions.RequestException as e:
            error_msg = f"Failed to connect to trading engine: {str(e)}"
            return {"status": "error", "message": f"❌ {error_msg}"}
                
    def check_server_status(self):
        """Check if the server is running and responsive"""
        try:
            response = self.session.get(f"{self.api_url}/status", timeout=3)
            return response.status_code == 200
        except:
            return False

    def get_server_logs(self):
        """Get recent server logs for debugging"""
        try:
            response = self.session.get(f"{self.api_url}/logs", timeout=5)
            if response.status_code == 200:
                return response.json()
        except:
            pass
        return {"logs": [], "error": "Cannot fetch logs"}

    def get_account_type(self, account_name):
        """Get the internal account type key from the display name"""
        account_name = account_name.lower()
        if "bybit" in account_name and "testnet" in account_name:
            return "bybit_testnet"
        elif "bybit" in account_name:
            return "bybit_mainnet"
        elif "gate" in account_name and "testnet" in account_name:
            return "gate_testnet"
        elif "gate" in account_name:
            return "gate_mainnet"
        return "bybit_testnet"  # default

    def render_sidebar(self):
        """Render the sidebar with configuration options"""
        with st.sidebar:
            st.title("Trading Configuration")
            
            # Server status indicator
            server_online = self.check_server_status()
            server_status = "🟢 Online" if server_online else "🔴 Offline"
            st.caption(f"Server: {server_status}")
            
            if not server_online:
                st.error("Server not responding. Check if trading engine is running.")
                return
            
            # Account selection FIRST - this determines where credentials are saved
            accounts = ["Bybit/Testnet", "Bybit/Mainnet", "Gate.io/Testnet", "Gate.io/Mainnet"]
            
            # Get current account from session state or config
            current_account = st.session_state.get('current_account', self.config.get("account", "Bybit/Testnet"))
            if current_account not in accounts:
                current_account = accounts[0]
            
            # Account selector with callback to update session state
            def update_account():
                st.session_state.current_account = st.session_state.account_selector
            
            selected_account = st.selectbox(
                "Account",
                options=accounts,
                index=accounts.index(current_account),
                key="account_selector",
                on_change=update_account
            )
            
            # Update config with selected account
            self.config["account"] = selected_account
            
            # Determine account type for credential management
            account_type = self.get_account_type(selected_account)
            current_creds = self.get_current_credentials(account_type)
            cred_source = current_creds.get("source", "none")
            
            # Display credential status - FIXED LOGIC
            if cred_source == "env":
                st.success("🔐 Using credentials from .env file")
            elif cred_source == "manual":
                # Check if we have actual credentials, not just empty strings
                if current_creds.get("api_key") and current_creds.get("api_secret"):
                    st.success("🔐 Using saved credentials")
                else:
                    st.error("❌ No credentials configured")
            else:
                st.error("❌ No credentials configured")
            
            if "gate" in selected_account.lower() and "testnet" not in selected_account.lower():
                with st.expander("⚠️ Gate.io Funding Required", expanded=True):
                    st.error("""
                    **Gate.io Futures Account Funding Required**
                    
                    Before trading on Gate.io Mainnet, you must:
                    
                    1. **Deposit funds** into your Gate.io account
                    2. **Transfer to Futures Wallet**: 
                    - Go to Gate.io website/app
                    - Navigate to "Futures" → "USDT-M Futures"
                    - Click "Transfer" and move funds from Spot to Futures wallet
                    3. **Minimum requirement**: At least $10 USDT for testing
                    
                    Without funded futures account, trading will fail with "USER_NOT_FOUND" error.
                    """)
                    
                    if st.button("Open Gate.io Futures", key="open_gate_futures"):
                        st.markdown("[👉 Open Gate.io Futures](https://www.gate.io/futures_trade/USDTM/BTCUSDT)")

            # WebSocket status
            ws_status = "🟢 Connected" if st.session_state.websocket_connected else "🔴 Disconnected"
            st.caption(f"WebSocket: {ws_status}")
            
            if not st.session_state.websocket_connected:
                if st.button("Connect Real-time Updates"):
                    self.init_websocket()
                    st.rerun()
            else:
                if st.button("Disconnect Real-time Updates"):
                    self.close_websocket()
                    st.rerun()
            
            # API Credentials Section
            with st.expander("🔑 API Credentials Management", expanded=not (current_creds.get("api_key") and current_creds.get("api_secret"))):
                st.write(f"Configure credentials for: {selected_account}")
                
                if cred_source != "none":
                    source_text = "Environment variables" if cred_source == "env" else "Manual input"
                    st.info(f"Current source: {source_text}")
                
                with st.form("api_credentials_form"):
                    # Pre-fill form with current credentials if available
                    prefilled_key = current_creds.get("api_key", "") if current_creds.get("api_key") else ""
                    prefilled_secret = current_creds.get("api_secret", "") if current_creds.get("api_secret") else ""
                    
                    api_key = st.text_input(
                        "API Key",
                        value=prefilled_key,
                        type="password",
                        placeholder="e.g., 2ABCDeFgH1JkLmN..."
                    )
                    
                    api_secret = st.text_input(
                        "API Secret",
                        value=prefilled_secret,
                        type="password",
                        placeholder="e.g., ABCDEFGH12345678..."
                    )
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        save_btn = st.form_submit_button("💾 Save Credentials")
                    with col2:
                        clear_btn = st.form_submit_button("🗑️ Clear Credentials")
                    
                    if save_btn:
                        if api_key and api_secret:
                            # Save to the CORRECT account type based on current selection
                            st.session_state.api_credentials[account_type] = {
                                "api_key": api_key,
                                "api_secret": api_secret,
                                "source": "manual"
                            }
                            self.save_credentials_to_storage()
                            st.success(f"✅ Credentials saved for {selected_account}!")
                            st.rerun()
                        else:
                            st.error("Please enter both API Key and API Secret")
                    
                    if clear_btn:
                        st.session_state.api_credentials[account_type] = {
                            "api_key": "",
                            "api_secret": "",
                            "source": "manual"
                        }
                        self.save_credentials_to_storage()
                        st.info(f"🗑️ Credentials cleared for {selected_account}")
                        st.rerun()
                                            
                env_creds = self.load_credentials_from_env().get(account_type)
                if env_creds:
                    st.success("✅ .env credentials detected!")
                    if st.button("🔄 Use .env Credentials", key="use_env_creds"):
                        # Clear manual credentials to force use of env
                        st.session_state.api_credentials[account_type] = {
                            "api_key": "",
                            "api_secret": "",
                            "source": "manual"
                        }
                        self.save_credentials_to_storage()
                        st.rerun()
                else:
                    st.warning("No .env credentials found for this account type")
            
            st.divider()
            
            # Symbol input
            symbol_help = "For Bybit: BTCUSDT (no slash). For Gate.io: BTC/USDT (with slash)"
            
            current_symbol = st.text_input(
                "Trading Pair", 
                value=self.config.get("symbol_display", self.config.get("symbol", "")), 
                help=symbol_help,
                key="symbol_input"
            )
            
            symbol_info = self.validate_symbol_format(current_symbol, selected_account)
            self.config["symbol"] = symbol_info["display"]
            self.config["symbol_api"] = symbol_info["api"]

            if "bybit" in selected_account.lower():
                st.caption("Bybit format: BTCUSDT (no slash)")
                if "/" in self.config["symbol"]:
                    st.warning("Remove slash from symbol for Bybit. Using: " + self.config["symbol"].replace("/", ""))
            elif "gate" in selected_account.lower():
                st.caption("Gate.io format: BTC/USDT (with slash)")
            
            
            # Market type selection
            market_types = ["linear", "inverse", "spot"]
            market_type_index = 0  # Default to linear
            if "market_type" in self.config:
                market_type_index = market_types.index(self.config["market_type"]) if self.config["market_type"] in market_types else 0
            
            market_type = st.selectbox(
                "Market Type",
                options=market_types,
                index=market_type_index,
                help="Select 'linear' for USDT futures, 'inverse' for coin futures"
            )
            
            # Add market type to config
            self.config["market_type"] = market_type
            
            # Category selection for Bybit
            if "bybit" in self.config["account"].lower():
                categories = ["linear", "inverse", "spot"]
                category_index = 0  # Default to linear
                if "category" in self.config:
                    category_index = categories.index(self.config["category"]) if self.config["category"] in categories else 0
                
                self.config["category"] = st.selectbox(
                    "Bybit Category",
                    options=categories,
                    index=category_index,
                    help="Select category for Bybit API"
                )
            
            # Leverage (only show for futures markets)
            if market_type in ["linear", "inverse"]:
                self.config["leverage"] = st.slider(
                    "Leverage",
                    min_value=1,
                    max_value=100,
                    value=int(self.config["leverage"]),
                    step=1
                )
                
                # Show margin requirement calculation
                order_amount = float(self.config["market_order_amount"])
                leverage = int(self.config["leverage"])
                margin_required = order_amount / leverage
                st.info(f"💰 Margin required: ${margin_required:.2f} USDT")
            else:
                self.config["leverage"] = 1
                st.info("Leverage is only available for linear/inverse markets")
            
            # Position side switch
            self.config["side"] = st.radio(
                "Position Side",
                options=["long", "short"],
                index=0 if self.config["side"] == "long" else 1,
                horizontal=True
            )
            
            # Market order amount
            self.config["market_order_amount"] = st.number_input(
                "Market Order Amount (USDT)",
                min_value=10.0,
                max_value=100000.0,
                value=float(self.config["market_order_amount"]),
                step=100.0
            )
            
            # Stop loss percent
            self.config["stop_loss_percent"] = st.number_input(
                "Stop Loss (%)",
                min_value=0.1,
                max_value=50.0,
                value=float(self.config["stop_loss_percent"]),
                step=0.5
            )
            
            # Trailing SL offset percent
            self.config["trailing_sl_offset_percent"] = st.number_input(
                "Trailing SL Offset (%)",
                min_value=0.1,
                max_value=20.0,
                value=float(self.config["trailing_sl_offset_percent"]),
                step=0.5
            )
            
            # Move SL to breakeven
            self.config["move_sl_to_breakeven"] = st.checkbox(
                "Move SL to Breakeven",
                value=self.config["move_sl_to_breakeven"]
            )
            
            # Limit orders amount
            self.config["limit_orders_amount"] = st.number_input(
                "Limit Orders Amount (USDT)",
                min_value=10.0,
                max_value=100000.0,
                value=float(self.config["limit_orders_amount"]),
                step=100.0
            )
            
            # TP Orders configuration
            st.subheader("Take Profit Orders")
            for i, tp_order in enumerate(self.config["tp_orders"]):
                col1, col2 = st.columns(2)
                with col1:
                    tp_order["price_percent"] = st.number_input(
                        f"TP {i+1} Price %",
                        min_value=0.1,
                        max_value=100.0,
                        value=float(tp_order["price_percent"]),
                        step=0.5,
                        key=f"tp_price_{i}"
                    )
                with col2:
                    tp_order["quantity_percent"] = st.number_input(
                        f"TP {i+1} Quantity %",
                        min_value=1.0,
                        max_value=100.0,
                        value=float(tp_order["quantity_percent"]),
                        step=1.0,
                        key=f"tp_qty_{i}"
                    )
            
            # Limit orders configuration
            st.subheader("Limit Orders Configuration")
            self.config["limit_orders"]["range_percent"] = st.number_input(
                "Range Percent",
                min_value=0.1,
                max_value=20.0,
                value=float(self.config["limit_orders"]["range_percent"]),
                step=0.5
            )
            
            self.config["limit_orders"]["orders_count"] = st.slider(
                "Orders Count",
                min_value=1,
                max_value=20,
                value=int(self.config["limit_orders"]["orders_count"]),
                step=1
            )
            
            self.config["limit_orders"]["engine_deal_duration_minutes"] = st.number_input(
                "Engine Deal Duration (minutes)",
                min_value=1,
                max_value=1440,
                value=int(self.config["limit_orders"]["engine_deal_duration_minutes"]),
                step=10
            )
            
            # Save configuration button
            if st.button("Save Configuration"):
                if self.save_config(self.config):
                    st.success("Configuration saved!")
                else:
                    st.error("Failed to save configuration")
            
            st.divider()
            
            # Trading controls
            st.subheader("Trading Controls")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Start Trading", type="primary"):
                    result = self.start_trading()
                    if result and result.get("status") == "success":
                        st.success("Trading started!")
                        # Auto-connect WebSocket when trading starts successfully
                        if not st.session_state.websocket_connected:
                            st.info("🔄 Auto-connecting WebSocket for real-time updates...")
                            self.init_websocket()
                            st.experimental_rerun()
                    elif result and result.get("status") == "warning":
                        st.warning(result.get('message', 'Trading started with warnings'))
                        # Show specific guidance for common warning scenarios
                        st.info("💡 This usually means: 1) Insufficient balance 2) Leverage issues 3) API permissions. Check your exchange account.")
                        # Auto-connect WebSocket even with warnings
                        if not st.session_state.websocket_connected:
                            st.info("🔄 Auto-connecting WebSocket for real-time updates...")
                            self.init_websocket()
                            st.experimental_rerun()
                    else:
                        error_msg = result.get('message', 'Unknown error') if result else 'Failed to start trading'
                        st.error(f"Error: {error_msg}")
                        # Show additional help for common errors
                        if "Insufficient balance" in error_msg:
                            st.info("💡 Please: 1) Check your account balance 2) Reduce order size 3) Consider lowering leverage")
                        elif "Trading session active but encountering errors" in error_msg:
                            st.info("💡 The trading engine started but encountered errors. Please: 1) Check account balance 2) Verify API permissions 3) Stop and restart trading")
                        elif "Trading is already in progress" in error_msg:
                            st.info("💡 Please stop the current trading session before starting a new one")

            with col2:
                if st.button("Stop Trading"):
                    result = self.stop_trading()
                    if result and result.get("status") == "success":
                        st.info("Trading stopped!")
                    else:
                        error_msg = result.get('message', 'Unknown error') if result else 'Failed to stop trading'
                        st.error(f"Error: {error_msg}")

            # Test connection button
            if st.button("Test Exchange Connection", type="secondary"):
                result = self.test_connection()
                if result and result.get("status") == "success":
                    st.success("✅ Exchange connection successful!")
                    
                    # Auto-connect WebSocket after successful connection test
                    if not st.session_state.websocket_connected:
                        st.info("🔄 Auto-connecting WebSocket for real-time updates...")
                        self.init_websocket()
                        st.experimental_rerun()
                else:
                    error_msg = result.get('message', 'Unknown error') if result else 'Connection test failed'
                    st.error(f"❌ Connection failed: {error_msg}")
                    st.info("Check: 1) API key permissions 2) Testnet vs Mainnet 3) IP restrictions")
                    
    def render_metrics(self, status):
        """Render metrics cards with specific handling for failed position opening"""
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            status_text = status.get("status", "unknown").upper()
            color = "green" if status_text == "ACTIVE" else "red" if status_text == "ERROR" else "orange" if status_text == "WARNING" else "gray"
            st.markdown(f'<div style="color: {color}; font-weight: bold;">Status: {status_text}</div>', unsafe_allow_html=True)
        
        with col2:
            # Credential status
            credentials_valid, cred_msg = self.validate_credentials()
            cred_color = "green" if credentials_valid else "red"
            cred_icon = "🔐" if credentials_valid else "❌"
            account_display = self.config["account"].split("/")[0]  # Show just "Bybit" or "Gate.io"
            st.markdown(f'<div style="color: {cred_color}; font-weight: bold;">{account_display}: {cred_icon}</div>', unsafe_allow_html=True)
            
        with col3:
            if "position" in status and status["position"]:
                side = status["position"].get("side", "N/A")
                entry_price = status["position"].get("average_entry_price")
                
                if side and side != "none" and entry_price is not None:
                    # Valid position with entry price
                    side_display = side.upper()
                    color = "green" if side == "long" else "red" if side == "short" else "gray"
                    st.markdown(f'<div style="color: {color}; font-weight: bold;">Position: {side_display}</div>', unsafe_allow_html=True)
                elif side and side != "none" and entry_price is None:
                    # Position intended but not opened (insufficient balance)
                    st.markdown(f'<div style="color: orange; font-weight: bold;">Position: {side.upper()} (FAILED)</div>', unsafe_allow_html=True)
                else:
                    # No position intended
                    st.markdown('<div style="color: gray; font-weight: bold;">Position: NONE</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div style="color: gray; font-weight: bold;">Position: NONE</div>', unsafe_allow_html=True)
        
        with col4:
            if "position" in status and status["position"]:
                entry_price = status["position"].get("average_entry_price")
                if entry_price is not None:
                    st.metric("Entry Price", f"${entry_price:.2f}")
                else:
                    st.metric("Entry Price", "N/A")
                    # Show specific message for failed position opening
                    st.caption("❌ Position opening failed")
            else:
                st.metric("Entry Price", "N/A")
                
        # Show compact error message for failed position opening due to insufficient balance
        if (status.get("status") == "active" and 
            "position" in status and status["position"] and 
            status["position"].get("side") not in ["none", None] and
            status["position"].get("average_entry_price") is None):
            
            order_amount = float(self.config.get("market_order_amount", 0))
            leverage = int(self.config.get("leverage", 1))
            required_margin = order_amount / leverage if leverage > 0 else order_amount
            
            # Compact expandable error message
            with st.expander("⚠️ POSITION OPENING FAILED - Click for details", expanded=False):
                st.error("""
                **Insufficient Balance Error**
                
                The trading engine failed to open a position due to insufficient balance.
                
                **Required:** ${required_margin:,.2f} USDT margin (${order_amount:,.2f} order ÷ {leverage}x leverage)
                """.format(required_margin=required_margin, order_amount=order_amount, leverage=leverage))
                
                st.write("**Quick Fixes:**")
                col_fix1, col_fix2 = st.columns(2)
                with col_fix1:
                    if st.button("🛑 Stop Trading", key="stop_failed_trade"):
                        result = self.stop_trading()
                        if result and result.get("status") == "success":
                            st.success("Trading stopped!")
                        else:
                            st.error("Failed to stop trading")
                with col_fix2:
                    if st.button("📉 Reduce Size 50%", key="reduce_size"):
                        # Auto-reduce order size by 50%
                        new_amount = order_amount * 0.5
                        self.config["market_order_amount"] = new_amount
                        if self.save_config(self.config):
                            st.success(f"Order size reduced to ${new_amount:,.2f}")
                        else:
                            st.error("Failed to update config")
                
        # Last update time
        if hasattr(st.session_state, 'last_update'):
            update_time = datetime.fromtimestamp(st.session_state.last_update).strftime('%H:%M:%S')
            st.caption(f"Last update: {update_time}")
                                                
    def render_position_info(self, status):
        """Render position information"""
        if "position" not in status or not status["position"]:
            return
        
        position = status["position"]
        position_side = position.get("side", "none")
        
        # Handle None values for entry price
        entry_price = position.get('average_entry_price', 0)
        if entry_price is None:
            entry_price = 0
        
        if position_side == "long":
            st.markdown(f"""
            <div class="position-long">
                <h3>LONG POSITION</h3>
                <p><strong>Symbol:</strong> {position.get('symbol', 'N/A')}</p>
                <p><strong>Entry Price:</strong> ${entry_price:.2f}</p>
                <p><strong>Entry Time:</strong> {position.get('entry_time', 'N/A')}</p>
                <p><strong>Open Orders:</strong> {position.get('open_orders', 0)}</p>
            </div>
            """, unsafe_allow_html=True)
        elif position_side == "short":
            st.markdown(f"""
            <div class="position-short">
                <h3>SHORT POSITION</h3>
                <p><strong>Symbol:</strong> {position.get('symbol', 'N/A')}</p>
                <p><strong>Entry Price:</strong> ${entry_price:.2f}</p>
                <p><strong>Entry Time:</strong> {position.get('entry_time', 'N/A')}</p>
                <p><strong>Open Orders:</strong> {position.get('open_orders', 0)}</p>
            </div>
            """, unsafe_allow_html=True)
                    
    def render_tp_orders_table(self):
        """Render TP orders table"""
        st.subheader("Take Profit Orders")
        tp_data = []
        for i, tp in enumerate(self.config["tp_orders"]):
            tp_data.append({
                "Order": i+1,
                "Price %": tp["price_percent"],
                "Quantity %": tp["quantity_percent"]
            })
        
        st.dataframe(pd.DataFrame(tp_data), width='stretch')
    
    def render_orders_table(self):
        """Render current orders table"""
        orders_data = self.get_orders()
        if orders_data and "orders" in orders_data and orders_data["orders"]:
            st.subheader("Current Orders")
            orders_df = pd.DataFrame(orders_data["orders"])
            
            # Add color coding based on order status
            def color_status(val):
                color = 'green' if val == 'closed' else 'orange' if val == 'open' else 'red'
                return f'color: {color}'
            
            styled_df = orders_df.style.applymap(color_status, subset=['status'])
            st.dataframe(styled_df, width='stretch')
        else:
            st.info("No active orders")
    
    def render_price_chart(self):
        """Render price chart with entry and TP levels"""
        # This would normally fetch real price data from the exchange
        # For demo purposes, we'll generate sample data
        
        # Sample price data
        dates = pd.date_range(end=datetime.now(), periods=100, freq='h')
        prices = [50000 + i * 100 + (i % 10) * 500 for i in range(100)]
        
        # Create figure
        fig = go.Figure()
        
        # Add price line
        fig.add_trace(go.Scatter(
            x=dates, y=prices,
            mode='lines',
            name='Price',
            line=dict(color='#1f77b4', width=2)
        ))
        
        # Add entry price if we have a position
        status = self.get_status()
        if "position" in status and status["position"]:
            entry_price = status["position"].get("average_entry_price")
            if entry_price is not None:
                fig.add_hline(
                    y=entry_price, 
                    line_dash="dash", 
                    line_color="green" if status["position"].get("side") == "long" else "red",
                    annotation_text="Entry Price"
                )
                
                # Add TP levels for long position
                if status["position"].get("side") == "long":
                    for i, tp in enumerate(self.config["tp_orders"]):
                        tp_price = entry_price * (1 + tp["price_percent"] / 100)
                        fig.add_hline(
                            y=tp_price,
                            line_dash="dot",
                            line_color="orange",
                            annotation_text=f"TP {i+1} ({tp['price_percent']}%)"
                        )
                
                # Add TP levels for short position
                elif status["position"].get("side") == "short":
                    for i, tp in enumerate(self.config["tp_orders"]):
                        tp_price = entry_price * (1 - tp["price_percent"] / 100)
                        fig.add_hline(
                            y=tp_price,
                            line_dash="dot",
                            line_color="orange",
                            annotation_text=f"TP {i+1} ({tp['price_percent']}%)"
                        )
        
        # Update layout
        fig.update_layout(
            title="Price Chart with Entry and TP Levels",
            xaxis_title="Time",
            yaxis_title="Price",
            height=400,
            showlegend=True
        )
        
        st.plotly_chart(fig, use_container_width=True)
            
    def render_order_book(self):
        st.subheader("Order Book")
        
        bids = [(50000 - i*10, 1 + i*0.5) for i in range(10)]
        asks = [(50000 + i*10, 1 + i*0.5) for i in range(10)]
        
        # Create DataFrames
        bids_df = pd.DataFrame(bids, columns=['Price', 'Quantity'])
        asks_df = pd.DataFrame(asks, columns=['Price', 'Quantity'])
        
        # Display order book
        col1, col2 = st.columns(2)
        
        with col1:
            st.write("Bids")
            st.dataframe(bids_df, width='stretch')  
        
        with col2:
            st.write("Asks")
            st.dataframe(asks_df, width='stretch') 
                
    def get_logs(self):
        """Read and parse log files"""
        log_files = [
            "trading_engine.log",
            "api_calls.log"
        ]
        
        all_logs = []
        
        for log_file in log_files:
            try:
                if os.path.exists(log_file):
                    with open(log_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            # Parse log line format: "2025-09-20 01:00:43,040 - app.main - ERROR - Error message"
                            log_match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - (.*?) - (.*?) - (.*)', line)
                            if log_match:
                                timestamp, logger_name, level, message = log_match.groups()
                                all_logs.append({
                                    'timestamp': timestamp,
                                    'logger': logger_name,
                                    'level': level.strip(),
                                    'message': message.strip(),
                                    'file': log_file
                                })
                            else:
                                # Handle lines that don't match the pattern
                                all_logs.append({
                                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S,%f')[:-3],
                                    'logger': 'unknown',
                                    'level': 'INFO',
                                    'message': line.strip(),
                                    'file': log_file
                                })
            except Exception as e:
                st.error(f"Error reading log file {log_file}: {e}")
        
        # Sort logs by timestamp (newest first)
        all_logs.sort(key=lambda x: x['timestamp'], reverse=True)
        return all_logs
    
    def filter_logs(self, logs, level_filter="all", search_text="", connection_filter="all"):
        """Filter logs by level, search text, and connection status"""
        filtered_logs = logs
        
        if level_filter != "all":
            filtered_logs = [log for log in filtered_logs if log['level'] == level_filter.upper()]
        
        if search_text:
            search_lower = search_text.lower()
            filtered_logs = [
                log for log in filtered_logs 
                if (search_lower in log['message'].lower() or 
                    search_lower in log['logger'].lower() or
                    search_lower in log['level'].lower())
            ]
        
        # WebSocket connection status filtering
        if connection_filter != "all":
            if connection_filter == "websocket_connected":
                filtered_logs = [
                    log for log in filtered_logs 
                    if any(keyword in log['message'].lower() for keyword in [
                        'websocket connected', 'ws connected', 'socket connected',
                        'connection established', 'connected to ws', 
                        'websocket connection successful', 'ws connection established'
                    ]) and 'disconnected' not in log['message'].lower()
                ]
            elif connection_filter == "websocket_disconnected":
                filtered_logs = [
                    log for log in filtered_logs 
                    if any(keyword in log['message'].lower() for keyword in [
                        'websocket disconnected', 'ws disconnected', 'socket disconnected',
                        'connection lost', 'disconnected from ws', 
                        'websocket connection closed', 'ws connection failed',
                        'connection error', 'socket error'
                    ])
                ]
            elif connection_filter == "websocket_events":
                filtered_logs = [
                    log for log in filtered_logs 
                    if any(keyword in log['message'].lower() for keyword in [
                        'websocket', 'ws ', 'socket', 'connection', 'connected', 'disconnected'
                    ])
                ]
        
        return filtered_logs

    def render_logs_tab(self):
        """Render the logs tab with WebSocket connection tracking"""
        st.subheader("Trading Logs")
        
        # Log controls
        col1, col2, col3, col4 = st.columns([2, 2, 2, 3])
        
        with col1:
            # Log level filter
            log_levels = ["all", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
            st.session_state.log_filter = st.selectbox(
                "Filter by Level",
                options=log_levels,
                index=log_levels.index(st.session_state.get('log_filter', 'all'))
            )
        
        with col2:
            # WebSocket connection filter
            ws_filters = ["all", "websocket_connected", "websocket_disconnected", "websocket_events"]
            ws_labels = ["All Events", "WS Connected", "WS Disconnected", "All WS Events"]
            st.session_state.connection_filter = st.selectbox(
                "WebSocket Events",
                options=ws_filters,
                index=ws_filters.index(st.session_state.get('connection_filter', 'all')),
                format_func=lambda x: dict(zip(ws_filters, ws_labels))[x]
            )
        
        with col3:
            # Refresh button
            if st.button("🔄 Refresh Logs", key="refresh_logs"):
                st.session_state.log_data = self.get_logs()
                st.rerun()
        
        with col4:
            # Search box
            st.session_state.log_search = st.text_input(
                "Search Logs",
                value=st.session_state.get('log_search', ''),
                placeholder="Search log messages..."
            )
        
        # Get and filter logs
        if not st.session_state.get('log_data'):
            st.session_state.log_data = self.get_logs()
        
        filtered_logs = self.filter_logs(
            st.session_state.log_data,
            st.session_state.log_filter,
            st.session_state.log_search,
            st.session_state.connection_filter
        )
        
        # Show WebSocket connection status
        self.render_websocket_status_summary()
        
        # Show log statistics
        total_logs = len(st.session_state.log_data)
        filtered_count = len(filtered_logs)
        
        st.info(f"Showing {filtered_count} of {total_logs} log entries")
        
        # Display logs in an expandable format
        for log in filtered_logs[:200]:
            # Determine color based on log level
            level_color = {
                "DEBUG": "gray",
                "INFO": "blue",
                "WARNING": "orange",
                "ERROR": "red",
                "CRITICAL": "purple"
            }.get(log['level'], "black")
            
            # Highlight WebSocket events
            bg_color = "transparent"
            if any(keyword in log['message'].lower() for keyword in ['websocket connected', 'ws connected', 'connection established']):
                bg_color = "#e8f5e8"  # Light green for connections
            elif any(keyword in log['message'].lower() for keyword in ['websocket disconnected', 'ws disconnected', 'connection lost']):
                bg_color = "#ffebee"  # Light red for disconnections
            elif 'websocket' in log['message'].lower() or 'ws ' in log['message'].lower():
                bg_color = "#e3f2fd"  # Light blue for other WS events
            
            # Create expandable log entry
            with st.expander(f"{log['timestamp']} - {log['level']} - {log['message'][:100]}...", expanded=False):
                st.markdown(f"""
                <div style='background-color: {bg_color}; padding: 10px; border-radius: 5px;'>
                    <p><strong>Message:</strong> {log['message']}</p>
                    <p><strong>Logger:</strong> {log['logger']}</p>
                    <p><strong>File:</strong> {log['file']}</p>
                    <p><strong>Level:</strong> <span style='color: {level_color}; font-weight: bold;'>{log['level']}</span></p>
                    <p><strong>Time:</strong> {log['timestamp']}</p>
                </div>
                """, unsafe_allow_html=True)
        
        # Show message if no logs found
        if not filtered_logs:
            st.warning("No log entries found matching your filters.")
        
        # Auto-refresh option
        if st.session_state.get('websocket_connected', False):
            auto_refresh = st.checkbox("Auto-refresh logs every 10 seconds", value=True)
            if auto_refresh:
                time.sleep(10)
                st.rerun()
            
            st.caption("Real-time logs enabled")
            if hasattr(st.session_state, 'last_update'):
                st.caption(f"Last refresh: {datetime.fromtimestamp(st.session_state.last_update).strftime('%H:%M:%S')}")

    def render_websocket_status_summary(self):
        """Render WebSocket connection status summary"""
        if not st.session_state.get('log_data'):
            return
        
        # Extract WebSocket connection events
        ws_events = self.get_websocket_events()
        
        if ws_events:
            # Get latest connection state
            latest_event = ws_events[-1]
            is_connected = latest_event['type'] == 'connected'
            
            # Display current status
            status_color = "green" if is_connected else "red"
            status_icon = "✅" if is_connected else "❌"
            status_text = "CONNECTED" if is_connected else "DISCONNECTED"
            
            st.markdown(f"""
            <div style='background-color: #f8f9fa; padding: 10px; border-radius: 5px; border-left: 4px solid {status_color}; margin-bottom: 15px;'>
                <h4 style='color: {status_color}; margin: 0;'>
                    {status_icon} WebSocket Status: <strong>{status_text}</strong>
                </h4>
                <p style='margin: 5px 0 0 0; font-size: 0.9em;'>
                    Last event: {latest_event['timestamp']} - {latest_event['message']}
                </p>
            </div>
            """, unsafe_allow_html=True)
            
            # Show connection statistics
            connected_events = [e for e in ws_events if e['type'] == 'connected']
            disconnected_events = [e for e in ws_events if e['type'] == 'disconnected']
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total Connections", len(connected_events))
            with col2:
                st.metric("Total Disconnections", len(disconnected_events))
            with col3:
                if connected_events and disconnected_events:
                    # Calculate average connection duration (simplified)
                    avg_duration = self.calculate_avg_connection_duration(ws_events)
                    st.metric("Avg Connection Time", avg_duration)

    def get_websocket_events(self, logs=None):
        """Extract WebSocket connection events from logs"""
        if logs is None:
            logs = st.session_state.get('log_data', [])
        
        ws_events = []
        for log in logs:
            message_lower = log['message'].lower()
            
            # Check for WebSocket connection events
            if any(keyword in message_lower for keyword in [
                'websocket connected', 'ws connected', 'connection established',
                'connected to ws', 'websocket connection successful'
            ]) and 'disconnected' not in message_lower:
                ws_events.append({
                    'type': 'connected',
                    'timestamp': log['timestamp'],
                    'message': log['message'],
                    'level': log['level'],
                    'log': log
                })
            
            # Check for WebSocket disconnection events
            elif any(keyword in message_lower for keyword in [
                'websocket disconnected', 'ws disconnected', 'connection lost',
                'disconnected from ws', 'websocket connection closed',
                'ws connection failed', 'connection error'
            ]):
                ws_events.append({
                    'type': 'disconnected',
                    'timestamp': log['timestamp'],
                    'message': log['message'],
                    'level': log['level'],
                    'log': log
                })
        
        # Sort by timestamp
        ws_events.sort(key=lambda x: x['timestamp'])
        return ws_events

    def calculate_avg_connection_duration(self, ws_events):
        """Calculate average WebSocket connection duration"""
        connections = []
        current_connection = None
        
        for event in ws_events:
            if event['type'] == 'connected':
                current_connection = {
                    'start': event['timestamp'],
                    'end': None,
                    'duration': None
                }
            elif event['type'] == 'disconnected' and current_connection:
                current_connection['end'] = event['timestamp']
                
                # Parse timestamps and calculate duration
                try:
                    start_time = datetime.strptime(current_connection['start'], '%Y-%m-%d %H:%M:%S')
                    end_time = datetime.strptime(current_connection['end'], '%Y-%m-%d %H:%M:%S')
                    duration = (end_time - start_time).total_seconds()
                    current_connection['duration'] = duration
                    connections.append(current_connection)
                except (ValueError, TypeError):
                    pass
                
                current_connection = None
        
        if connections:
            avg_duration = sum(conn['duration'] for conn in connections) / len(connections)
            return f"{avg_duration:.1f}s"
        
        return "N/A"

    def render_websocket_timeline(self):
        """Render WebSocket connection timeline"""
        ws_events = self.get_websocket_events()
        
        if ws_events:
            st.subheader("WebSocket Connection Timeline")
            
            for event in ws_events[-10:]:  # Show last 10 events
                color = "green" if event['type'] == 'connected' else "red"
                icon = "🔗" if event['type'] == 'connected' else "🔌"
                
                st.markdown(f"""
                <div style='border-left: 4px solid {color}; padding: 10px; margin: 10px 0; background-color: #fafafa;'>
                    <strong>{icon} WebSocket {event['type'].upper()}</strong><br>
                    <small>📅 {event['timestamp']} | 🎚️ {event['level']}</small><br>
                    {event['message']}
                </div>
                """, unsafe_allow_html=True)
                
    def get_connection_timeline(self, logs=None):
        """Extract connection timeline from logs"""
        if logs is None:
            logs = st.session_state.get('log_data', [])
        
        connection_events = []
        for log in logs:
            message_lower = log['message'].lower()
            timestamp = log['timestamp']
            
            if 'connected' in message_lower and 'disconnected' not in message_lower:
                connection_events.append({
                    'type': 'connected',
                    'timestamp': timestamp,
                    'message': log['message'],
                    'log': log
                })
            elif 'disconnected' in message_lower:
                connection_events.append({
                    'type': 'disconnected',
                    'timestamp': timestamp,
                    'message': log['message'],
                    'log': log
                })
        
        # Sort by timestamp
        connection_events.sort(key=lambda x: x['timestamp'])
        return connection_events

    def render_connection_timeline(self):
        """Render a timeline of connection events"""
        connection_events = self.get_connection_timeline()
        
        if connection_events:
            st.subheader("Connection Timeline")
            
            for event in connection_events[-10:]:  # Show last 10 events
                color = "green" if event['type'] == 'connected' else "red"
                icon = "✅" if event['type'] == 'connected' else "❌"
                
                st.markdown(f"""
                <div style='border-left: 4px solid {color}; padding-left: 10px; margin: 10px 0;'>
                    <strong>{icon} {event['type'].upper()}</strong><br>
                    <small>{event['timestamp']}</small><br>
                    {event['message']}
                </div>
                """, unsafe_allow_html=True)

    def render_debug_section(self):
        """Render debug information for troubleshooting"""
        with st.expander("🔧 Debug Information", expanded=False):
            st.subheader("Server Connection Test")
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Test API Connection"):
                    try:
                        response = self.session.get(f"{self.api_url}/status", timeout=3)
                        if response.status_code == 200:
                            st.success("✅ API connection successful!")
                        else:
                            st.error(f"❌ API returned status: {response.status_code}")
                    except Exception as e:
                        st.error(f"❌ API connection failed: {str(e)}")
            
            with col2:
                if st.button("Check Server Logs"):
                    logs = self.get_server_logs()
                    if logs.get("logs"):
                        st.text_area("Recent Server Logs", "\n".join(logs["logs"][-20:]), height=200)
                    else:
                        st.info("No logs available")
            
            st.subheader("Request Details")
            st.code(f"API URL: {self.api_url}")
            st.code(f"Config: {json.dumps(self.config, indent=2)}")
            
            # Show credentials (masked)
            masked_creds = {}
            for account_type, creds in st.session_state.api_credentials.items():
                masked_creds[account_type] = {
                    "api_key": creds.get("api_key", "")[:4] + "..." if creds.get("api_key") else "Not set",
                    "api_secret": creds.get("api_secret", "")[:4] + "..." if creds.get("api_secret") else "Not set",
                    "source": creds.get("source", "unknown")
                }
            st.code(f"Credentials: {json.dumps(masked_creds, indent=2)}")
            
            # Show environment credentials status
            env_creds = self.load_credentials_from_env()
            st.code(f"Env Credentials Available: {list(env_creds.keys()) if env_creds else 'None'}")
                    
    def render(self):
        """Render the complete dashboard"""
        st.title("Trading Engine Dashboard")
        add_theme_toggle()

        # Check server status first
        server_online = self.check_server_status()
        
        if not server_online:
            st.error("""
            ❌ Trading Engine Server is offline or not responding!
            
            **Please make sure:**
            1. The trading engine server is running on localhost:8000
            2. You've started the server with: `python -m app.main`
            3. There are no port conflicts
            """)
            
            # Show server logs if available
            if st.button("Check Server Status"):
                server_logs = self.get_server_logs()
                if server_logs.get("logs"):
                    st.subheader("Server Logs")
                    for log in server_logs["logs"][-10:]:
                        st.text(log)
                else:
                    st.info("Cannot retrieve server logs. Make sure the server is running.")
            
            return  # Don't render the rest if server is offline
        
        # Initialize WebSocket connection
        if st.session_state.websocket_connected and self.websocket_client is None:
            self.init_websocket()
        
        # Process WebSocket messages
        if self.websocket_client:
            self.process_websocket_messages()
        
        # Render sidebar
        self.render_sidebar()
        
        # Get current status
        status = self.get_status()
        
        # Render metrics
        self.render_metrics(status)
        
        # Render position info
        if status.get("status") == "active":
            self.render_position_info(status)
        
        # Main content area
        tab1, tab2, tab3, tab4, tab5 = st.tabs(["Chart", "Orders", "Configuration", "Logs", "Debug"])
        
        with tab1:
            self.render_price_chart()
            self.render_order_book()
        
        with tab2:
            self.render_tp_orders_table()
            self.render_orders_table()
        
        with tab3:
            st.subheader("Current Configuration")
            st.json(self.config, expanded=False)
        
        with tab4:
            self.render_logs_tab()
        
        with tab5:
            self.render_debug_section()

# Run the dashboard
if __name__ == "__main__":
    dashboard = TradingDashboard()
    dashboard.render()