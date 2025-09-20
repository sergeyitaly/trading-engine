import os
import ccxt
from dotenv import load_dotenv

load_dotenv()  # Load .env file

def test_bybit_connection():
    """Test Bybit connection with detailed diagnostics"""
    api_key = os.getenv("BYBIT_API_KEY")
    api_secret = os.getenv("BYBIT_API_SECRET")
    testnet = os.getenv("BYBIT_TESTNET", "true").lower() == "true"

    print("=" * 50)
    print("TESTING BYBIT CONNECTION")
    print("=" * 50)
    
    if not api_key:
        print("❌ BYBIT_API_KEY not found in environment variables")
        return False
    if not api_secret:
        print("❌ BYBIT_API_SECRET not found in environment variables")
        return False
        
    print(f"✅ API Key: {api_key[:10]}...{api_key[-4:]}")
    print(f"✅ Testnet mode: {testnet}")
    
    try:
        # Initialize exchange with proper options
        exchange = ccxt.bybit({
            'apiKey': api_key,
            'secret': api_secret,
            'sandbox': testnet,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',  # Try both spot and future
            }
        })
        
        print("🔄 Testing connection...")
        
        # Test 1: Fetch balance
        print("\n1. Testing balance fetch...")
        try:
            balance = exchange.fetch_balance()
            print(f"✅ Balance test successful")
            print(f"   Total USDT: {balance.get('USDT', {}).get('total', 0)}")
        except Exception as e:
            print(f"❌ Balance fetch failed: {e}")
            
        # Test 2: Fetch symbols
        print("\n2. Testing symbol fetch...")
        try:
            exchange.load_markets()
            symbols = exchange.symbols
            btc_symbols = [s for s in symbols if 'BTC' in s and 'USDT' in s]
            print(f"✅ Found {len(btc_symbols)} BTC/USDT symbols:")
            for symbol in btc_symbols[:5]:  # Show first 5
                print(f"   - {symbol}")
        except Exception as e:
            print(f"❌ Symbol fetch failed: {e}")
            
        # Test 3: Test specific symbol (BTCUSDT)
        print("\n3. Testing BTCUSDT symbol...")
        try:
            symbol = 'BTC/USDT' if not testnet else 'BTC/USDT:USDT'
            ticker = exchange.fetch_ticker(symbol)
            print(f"✅ Ticker for {symbol}:")
            print(f"   Bid: {ticker['bid']}, Ask: {ticker['ask']}")
        except Exception as e:
            print(f"❌ Ticker fetch failed for {symbol}: {e}")
            # Try alternative symbol format
            try:
                symbol = 'BTCUSDT'
                ticker = exchange.fetch_ticker(symbol)
                print(f"✅ Ticker for {symbol}:")
                print(f"   Bid: {ticker['bid']}, Ask: {ticker['ask']}")
            except Exception as e2:
                print(f"❌ Ticker fetch failed for {symbol}: {e2}")
                
        # Test 4: Check if we can set leverage (for futures)
        print("\n4. Testing leverage setting...")
        try:
            if testnet:
                # For testnet, use the testnet symbol format
                result = exchange.set_leverage(10, 'BTCUSDT')
                print(f"✅ Leverage set successfully: {result}")
            else:
                result = exchange.set_leverage(10, 'BTC/USDT:USDT')
                print(f"✅ Leverage set successfully: {result}")
        except Exception as e:
            print(f"❌ Leverage setting failed: {e}")
            
        return True
        
    except ccxt.AuthenticationError as e:
        print(f"❌ Authentication failed: {e}")
        print("   Please check:")
        print("   - API key and secret are correct")
        print("   - API key has trading permissions")
        print("   - IP restrictions (if any) allow your server's IP")
        return False
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False

def test_gateio_connection():
    """Test Gate.io connection with detailed diagnostics"""
    api_key = os.getenv("GATEIO_API_KEY")
    api_secret = os.getenv("GATEIO_API_SECRET")

    print("\n" + "=" * 50)
    print("TESTING GATE.IO CONNECTION")
    print("=" * 50)
    
    if not api_key:
        print("❌ GATEIO_API_KEY not found in environment variables")
        return False
    if not api_secret:
        print("❌ GATEIO_API_SECRET not found in environment variables")
        return False
        
    print(f"✅ API Key: {api_key[:10]}...{api_key[-4:]}")
    
    try:
        exchange = ccxt.gateio({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
        })
        
        print("🔄 Testing connection...")
        
        # Test 1: Fetch balance
        print("\n1. Testing balance fetch...")
        try:
            balance = exchange.fetch_balance()
            print(f"✅ Balance test successful")
            print(f"   Total USDT: {balance.get('USDT', {}).get('total', 0)}")
        except Exception as e:
            print(f"❌ Balance fetch failed: {e}")
            
        # Test 2: Fetch symbols
        print("\n2. Testing symbol fetch...")
        try:
            exchange.load_markets()
            symbols = exchange.symbols
            btc_symbols = [s for s in symbols if 'BTC' in s and 'USDT' in s]
            print(f"✅ Found {len(btc_symbols)} BTC/USDT symbols:")
            for symbol in btc_symbols[:5]:  # Show first 5
                print(f"   - {symbol}")
        except Exception as e:
            print(f"❌ Symbol fetch failed: {e}")
            
        return True
        
    except ccxt.AuthenticationError as e:
        print(f"❌ Authentication failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False

def check_environment_variables():
    """Check if all required environment variables are set"""
    print("CHECKING ENVIRONMENT VARIABLES")
    print("=" * 50)
    
    env_vars = {
        'BYBIT_API_KEY': os.getenv('BYBIT_API_KEY'),
        'BYBIT_API_SECRET': os.getenv('BYBIT_API_SECRET'),
        'BYBIT_TESTNET': os.getenv('BYBIT_TESTNET', 'true'),
        'GATEIO_API_KEY': os.getenv('GATEIO_API_KEY'),
        'GATEIO_API_SECRET': os.getenv('GATEIO_API_SECRET'),
    }
    
    all_set = True
    for var_name, var_value in env_vars.items():
        status = "✅" if var_value else "❌"
        print(f"{status} {var_name}: {'Set' if var_value else 'Not set'}")
        if not var_value and var_name.startswith('BYBIT_'):
            all_set = False
    
    return all_set

if __name__ == "__main__":
    print("API CONNECTION TESTER")
    print("=" * 60)
    
    # Check environment variables first
    env_ok = check_environment_variables()
    
    if env_ok:
        # Test Bybit connection
        bybit_ok = test_bybit_connection()
        
        # Test Gate.io connection (if credentials available)
        if os.getenv("GATEIO_API_KEY") and os.getenv("GATEIO_API_SECRET"):
            gateio_ok = test_gateio_connection()
        else:
            print("\nSkipping Gate.io test (credentials not set)")
    else:
        print("\n❌ Please set the required environment variables in your .env file")
        print("\nExample .env file content:")
        print("BYBIT_API_KEY=your_bybit_api_key_here")
        print("BYBIT_API_SECRET=your_bybit_api_secret_here")
        print("BYBIT_TESTNET=true")
        print("GATEIO_API_KEY=your_gateio_api_key_here")
        print("GATEIO_API_SECRET=your_gateio_api_secret_here")