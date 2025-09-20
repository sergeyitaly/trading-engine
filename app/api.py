# app/api.py
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
import asyncio
import json
from datetime import datetime
import logging
import time
from contextlib import asynccontextmanager
from .main import TradingEngine, TradingConfig, logger, get_session
from fastapi import WebSocket, WebSocketDisconnect
import os
import requests
from bs4 import BeautifulSoup
from app.config import load_config

# Lifespan management
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    get_session()  # Initialize session
    yield
    # Shutdown
    session = get_session()
    if not session.closed:
        await session.close()

app = FastAPI(
    title="Trading Engine API",
    lifespan=lifespan
)

# Add middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Global engine instance with thread safety
engine = None
engine_lock = asyncio.Lock()

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        disconnected_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                disconnected_connections.append(connection)
        
        for connection in disconnected_connections:
            self.disconnect(connection)

manager = ConnectionManager()


class Credentials(BaseModel):
    api_key: str
    api_secret: str
    testnet: bool = False

class TradeRequest(BaseModel):
    config: Dict[str, Any]
    credentials: Credentials
    validate_only: bool = False

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Handle subscription messages
            try:
                message = json.loads(data)
                if message.get('type') == 'subscribe':
                    # You can handle different subscription channels here
                    pass
            except:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.post("/start")
async def start_trading(request: Request):
    """Start trading with the configuration from URL/local file and credentials from request"""
    global engine
    
    try:
        data = await request.json()
        credentials = data.get("credentials", {})
        validate_only = data.get("validate_only", False)
        
        # Load configuration from URL or local file
        base_config = load_config()
        
        # Get account type from the loaded config
        account_type = base_config.get("account", "").lower()
        
        # Prepare config with account-specific credentials from the request
        config_with_creds = base_config.copy()
        
        # Add credentials to the config based on account type
        if "bybit" in account_type:
            if "testnet" in account_type:
                config_with_creds["bybit_testnet_api_key"] = credentials.get("api_key")
                config_with_creds["bybit_testnet_api_secret"] = credentials.get("api_secret")
            else:
                config_with_creds["bybit_mainnet_api_key"] = credentials.get("api_key")
                config_with_creds["bybit_mainnet_api_secret"] = credentials.get("api_secret")
        elif "gate" in account_type:
            if "testnet" in account_type:
                config_with_creds["gate_testnet_api_key"] = credentials.get("api_key")
                config_with_creds["gate_testnet_api_secret"] = credentials.get("api_secret")
            else:
                config_with_creds["gate_mainnet_api_key"] = credentials.get("api_key")
                config_with_creds["gate_mainnet_api_secret"] = credentials.get("api_secret")
        
        async with engine_lock:
            if engine and hasattr(engine, 'position_manager') and engine.position_manager:
                return JSONResponse(
                    status_code=400,
                    content={"status": "error", "message": "Trading already in progress"}
                )
            
            try:
                start_time = time.time()
                
                # Set environment variables for the current session
                if "bybit" in account_type:
                    if "testnet" in account_type:
                        os.environ["BYBIT_TESTNET_API_KEY"] = credentials.get("api_key", "")
                        os.environ["BYBIT_TESTNET_API_SECRET"] = credentials.get("api_secret", "")
                    else:
                        os.environ["BYBIT_API_KEY"] = credentials.get("api_key", "")
                        os.environ["BYBIT_API_SECRET"] = credentials.get("api_secret", "")
                elif "gate" in account_type:
                    if "testnet" in account_type:
                        # Set both naming conventions for Gate.io
                        os.environ["GATEIO_TESTNET_API_KEY"] = credentials.get("api_key", "")
                        os.environ["GATEIO_TESTNET_API_SECRET"] = credentials.get("api_secret", "")
                        os.environ["GATE_TESTNET_API_KEY"] = credentials.get("api_key", "")
                        os.environ["GATE_TESTNET_API_SECRET"] = credentials.get("api_secret", "")
                    else:
                        # Set both naming conventions for Gate.io
                        os.environ["GATEIO_API_KEY"] = credentials.get("api_key", "")
                        os.environ["GATEIO_API_SECRET"] = credentials.get("api_secret", "")
                        os.environ["GATE_API_KEY"] = credentials.get("api_key", "")
                        os.environ["GATE_API_SECRET"] = credentials.get("api_secret", "")
                
                # Create TradingConfig object directly from the merged config
                config_obj = TradingConfig.from_dict(config_with_creds)
                
                # Initialize trading engine with the config object directly
                engine = TradingEngine(config_obj)  # Pass the config object

                if validate_only:
                    # Only validate connection without starting trading
                    await engine.initialize()
                    await engine._fast_exchange.close()  # Close connection after validation
                    engine = None
                    
                    response_time = time.time() - start_time
                    return {"status": "success", "message": "Connection validated successfully", "response_time": response_time}
                else:
                    # Start actual trading
                    await engine.initialize()
                    
                    # Run trading in background with timeout
                    asyncio.create_task(run_engine_with_timeout())
                    
                    response_time = time.time() - start_time
                    return {"status": "success", "message": "Trading started", "response_time": response_time}
                    
            except asyncio.TimeoutError:
                raise HTTPException(status_code=504, detail="Start operation timed out")
            except Exception as e:
                # Add detailed error logging
                logger.error(f"Error in start_trading: {str(e)}")
                logger.error(f"Error type: {type(e).__name__}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
                raise HTTPException(status_code=500, detail=str(e))
                
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}")
    
async def run_engine_with_timeout():
    """Run engine with timeout protection"""
    global engine
    try:
        async with asyncio.timeout(30):  # 30 second timeout for engine start
            await engine.run()
    except asyncio.TimeoutError:
        logger.error("Engine start timed out")
        engine = None
    except Exception as e:
        logger.error(f"Engine error: {e}")
        engine = None

@app.get("/status")
async def get_status():
    """Get current trading status with fast response"""
    start_time = time.time()
    
    if not engine or not hasattr(engine, 'position_manager') or not engine.position_manager:
        response_time = time.time() - start_time
        return {"status": "idle", "response_time": response_time}
    
    try:
        # Fast status check with timeout
        async with asyncio.timeout(2):  # 2 second timeout for status check
            position_info = {
                "symbol": engine.config.symbol,
                "side": engine.config.side,
                "average_entry_price": engine.position_manager.average_entry_price,
                "entry_time": engine.position_manager.entry_time.isoformat() if engine.position_manager.entry_time else None,
                "open_orders": len(engine.position_manager.open_orders),
                "monitoring": getattr(engine.position_manager, '_running', False)
            }
        
        response_time = time.time() - start_time

        await manager.broadcast({
            "channel": "status",
            "data": position_info,
            "timestamp": datetime.now().isoformat()
        })
        
        return {
            "status": "active",
            "position": position_info,
            "response_time": response_time
        }
    except asyncio.TimeoutError:
        response_time = time.time() - start_time
        return {
            "status": "active",
            "position": {"error": "Status check timeout"},
            "response_time": response_time
        }
    except Exception as e:
        response_time = time.time() - start_time
        raise HTTPException(
            status_code=500,
            detail={"error": str(e), "response_time": response_time}
        )

@app.post("/stop")
async def stop_trading():
    """Stop current trading activity"""
    global engine
    
    start_time = time.time()
    
    async with engine_lock:
        if not engine or not hasattr(engine, 'position_manager') or not engine.position_manager:
            response_time = time.time() - start_time
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "No active trading session", "response_time": response_time}
            )
        
        try:
            # Stop monitoring with timeout
            async with asyncio.timeout(10):
                if hasattr(engine.position_manager, 'stop_monitoring'):
                    await engine.position_manager.stop_monitoring()
                
                # Close exchange connection
                if hasattr(engine, '_fast_exchange'):
                    await engine._fast_exchange.close()
            
            engine = None
            response_time = time.time() - start_time
            return {"status": "success", "message": "Trading stopped", "response_time": response_time}
        except asyncio.TimeoutError:
            response_time = time.time() - start_time
            raise HTTPException(status_code=504, detail="Stop operation timed out")
        except Exception as e:
            response_time = time.time() - start_time
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/config")
async def get_config():
    """Get current configuration with caching"""
    start_time = time.time()
    
    try:
        async with asyncio.timeout(5):  # Increased timeout for remote fetch
            config = load_config()
            
            if config is None:
                raise HTTPException(status_code=404, detail="Configuration not found")
        
        response_time = time.time() - start_time
        return {**config, "response_time": response_time}
    except asyncio.TimeoutError:
        response_time = time.time() - start_time
        raise HTTPException(status_code=504, detail="Config fetch timeout")
    except HTTPException:
        raise
    except Exception as e:
        response_time = time.time() - start_time
        raise HTTPException(status_code=500, detail=f"Error loading config: {str(e)}")
    
@app.post("/config")
async def update_config(request: Request):
    """Update trading configuration"""
    start_time = time.time()
    
    try:
        data = await request.json()
        
        async with asyncio.timeout(2):  # 2 second timeout
            with open("config.json", "w") as f:
                json.dump(data, f, indent=4)
        
        response_time = time.time() - start_time
        return {"status": "success", "message": "Configuration updated", "response_time": response_time}
    except asyncio.TimeoutError:
        response_time = time.time() - start_time
        raise HTTPException(status_code=504, detail="Config write timeout")
    except Exception as e:
        response_time = time.time() - start_time
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint with fast response"""
    start_time = time.time()
    response_time = time.time() - start_time
    return {
        "status": "healthy", 
        "timestamp": datetime.now().isoformat(),
        "response_time": response_time
    }

@app.get("/orders")
async def get_orders():
    """Get current open orders with fast response"""
    start_time = time.time()
    
    if not engine or not hasattr(engine, 'position_manager') or not engine.position_manager:
        response_time = time.time() - start_time
        return {"orders": [], "response_time": response_time}
    
    try:
        async with asyncio.timeout(3):  # 3 second timeout
            # Format orders for API response
            orders = []
            for order in engine.position_manager.open_orders:
                orders.append({
                    "id": order.get('id'),
                    "symbol": order.get('symbol'),
                    "type": order.get('type'),
                    "side": order.get('side'),
                    "price": order.get('price'),
                    "amount": order.get('amount'),
                    "filled": order.get('filled'),
                    "status": order.get('status'),
                    "timestamp": order.get('timestamp')
                })
        
        await manager.broadcast({
            "channel": "orders",
            "data": orders,
            "timestamp": datetime.now().isoformat()
        })
        
        response_time = time.time() - start_time
        return {"orders": orders, "response_time": response_time}
    except asyncio.TimeoutError:
        response_time = time.time() - start_time
        return {"orders": [], "error": "Timeout fetching orders", "response_time": response_time}
    except Exception as e:
        response_time = time.time() - start_time
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/performance")
async def get_performance():
    """Get performance metrics"""
    return {
        "timestamp": datetime.now().isoformat(),
        "active_tasks": len(asyncio.all_tasks()),
        "engine_status": "active" if engine else "inactive"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        timeout_keep_alive=30,
        limit_concurrency=100,
        backlog=2048
    )