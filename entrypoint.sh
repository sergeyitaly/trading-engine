#!/bin/bash

# Start FastAPI server in background
uvicorn app.api:app --host 0.0.0.0 --port 8000 &

# Start Streamlit dashboard
streamlit run app/ui/dashboard.py --server.port 8501 --server.address 0.0.0.0