#!/bin/bash

# Function to start a process and wait for it to be ready
start_and_wait() {
    local name=$1
    local command=$2
    local port=$3
    local health_endpoint=$4
    local timeout=30
    local interval=2
    
    echo "Starting $name..."
    $command &
    local pid=$!
    echo "$name started with PID $pid"
    
    # Wait for the process to be ready by checking the health endpoint
    echo "Waiting for $name to be ready on port $port..."
    local elapsed=0
    while [ $elapsed -lt $timeout ]; do
        if curl -f "http://localhost:${port}${health_endpoint}" >/dev/null 2>&1; then
            echo "$name is ready on port $port"
            return 0
        fi
        sleep $interval
        elapsed=$((elapsed + interval))
        
        # Check if process is still running
        if ! kill -0 $pid 2>/dev/null; then
            echo "ERROR: $name process died during startup"
            return 1
        fi
    done
    
    echo "ERROR: $name failed to start within $timeout seconds"
    return 1
}

# Start FastAPI server first and wait for it to be ready
start_and_wait "FastAPI" "uvicorn app.api:app --host 0.0.0.0 --port 8000" 8000 "/health"
if [ $? -ne 0 ]; then
    exit 1
fi

# Start Streamlit dashboard after API is ready
start_and_wait "Streamlit" "streamlit run app/ui/dashboard.py --server.port 8501 --server.address 0.0.0.0" 8501 "/"
if [ $? -ne 0 ]; then
    exit 1
fi

echo "Both services started successfully. Monitoring processes..."

# Monitor both processes to keep container running
while true; do
    # Check if uvicorn is still running
    if ! pgrep -f "uvicorn app.api:app" >/dev/null; then
        echo "ERROR: FastAPI process died"
        exit 1
    fi
    
    # Check if streamlit is still running
    if ! pgrep -f "streamlit run app/ui/dashboard.py" >/dev/null; then
        echo "ERROR: Streamlit process died"
        exit 1
    fi
    
    sleep 5
done