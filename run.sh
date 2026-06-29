#!/bin/bash
cd "$(dirname "$0")"

while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting jztz_v17..."
    python3 web_app.py
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Process exited, restarting in 10s..."
    sleep 10
done
