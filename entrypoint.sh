#!/bin/bash

# Start FastAPI server in background
echo "Starting FastAPI..."
uvicorn app.api:app --host 0.0.0.0 --port 8000 &
UVICORN_PID=$!

# Wait a moment for FastAPI to start
sleep 3

# Start Streamlit dashboard in background
echo "Starting Streamlit..."
streamlit run app/ui/dashboard.py --server.port 8501 --server.address 0.0.0.0 &
STREAMLIT_PID=$!

# Function to check if process is running
check_process() {
    local pid=$1
    local name=$2
    if kill -0 $pid 2>/dev/null; then
        echo "$name is running (PID: $pid)"
        return 0
    else
        echo "$name is not running"
        return 1
    fi
}

# Check both processes
check_process $UVICORN_PID "FastAPI" || exit 1
check_process $STREAMLIT_PID "Streamlit" || exit 1

echo "Both services started successfully. Monitoring processes..."

# Monitor both processes
while true; do
    if ! check_process $UVICORN_PID "FastAPI"; then
        echo "ERROR: FastAPI process died"
        exit 1
    fi
    
    if ! check_process $STREAMLIT_PID "Streamlit"; then
        echo "ERROR: Streamlit process died"
        exit 1
    fi
    
    sleep 5
done