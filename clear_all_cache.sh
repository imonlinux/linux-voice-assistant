#!/bin/bash
# Aggressive Python cache clearing for linux-voice-assistant

echo "=== Clearing all Python caches ==="

# Kill any running Python processes that might hold files open
echo "Stopping any running pytest processes..."
pkill -9 pytest 2>/dev/null || true

# Clear all .pyc files
echo "Clearing .pyc files..."
find /home/pi/linux-voice-assistant -type f -name "*.pyc" -delete

# Clear all __pycache__ directories
echo "Clearing __pycache__ directories..."
find /home/pi/linux-voice-assistant -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Clear .pytest_cache
echo "Clearing pytest cache..."
rm -rf /home/pi/linux-voice-assistant/.pytest_cache 2>/dev/null || true

# Clear any .mypy_cache
echo "Clearing mypy cache..."
rm -rf /home/pi/linux-voice-assistant/.mypy_cache 2>/dev/null || true

echo "=== Verification ==="
echo "Remaining .pyc files:"
find /home/pi/linux-voice-assistant -name "*.pyc" | wc -l
echo "Remaining __pycache__ dirs:"
find /home/pi/linux-voice-assistant -name "__pycache__" | wc -l

echo "=== Done ==="
echo "Now run: pytest tests/test_state_management.py::TestMacAddressHandling::test_mac_address_format -v"
