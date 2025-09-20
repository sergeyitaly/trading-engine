#!/bin/bash

# Start FastAPI server in background
uvicorn app.api:app --host 0.0.0.0 --port 8000 &

# Store the PID of the background process
UVICORN_PID=$!

# Start Streamlit dashboard
streamlit run app/ui/dashboard.py --server.port 8501 --server.address 0.0.0.0

# Wait for the background process to finish (though it shouldn't unless there's an error)
wait $UVICORN_PID