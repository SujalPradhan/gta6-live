#!/bin/bash
# Double-click to start the GTA VI live newsroom.
cd "$(dirname "$0")"
( sleep 2; open "http://localhost:8765/live.html" ) &
python3 tracker.py
