#!/bin/bash
# F5 Network Map Pro startup script

cd "$(dirname "$0")/backend"

echo "Installing dependencies..."
pip install -r requirements.txt -q

echo ""
echo "Starting F5 Network Map Pro..."
echo "Buka browser: http://localhost:8000"
echo ""

uvicorn main:app --host 0.0.0.0 --port 8000 --reload
