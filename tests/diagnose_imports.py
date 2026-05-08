#!/usr/bin/env python3
"""Diagnostic script to check what code is actually being used."""

import sys
import os

print("=== Python Path Diagnostics ===")
print(f"Python executable: {sys.executable}")
print(f"Python version: {sys.version}")
print(f"Current directory: {os.getcwd()}")

# Add project to path
sys.path.insert(0, '/home/pi/linux-voice-assistant')

print("\n=== Import Path Check ===")
import linux_voice_assistant.util
print(f"util.py location: {linux_voice_assistant.util.__file__}")
print(f"util.py mtime: {os.path.getmtime(linux_voice_assistant.util.__file__)}")

print("\n=== format_mac Source Code ===")
import inspect
source = inspect.getsource(linux_voice_assistant.util.format_mac)
print(source)

print("\n=== format_mac Execution Test ===")
test_input = "aa:bb:cc:dd:ee:ff"
result = linux_voice_assistant.util.format_mac(test_input)
print(f"Input:    '{test_input}'")
print(f"Result:   '{result}'")
print(f"Expected: 'aa:bb:cc:dd:ee:ff'")
print(f"Match:    {result == 'aa:bb:cc:dd:ee:ff'}")

print("\n=== Check for .pyc files ===")
util_pyc = '/home/pi/linux-voice-assistant/linux_voice_assistant/util.pyc'
if os.path.exists(util_pyc):
    print(f"Found bytecode cache: {util_pyc}")
    print(f"Bytecode mtime: {os.path.getmtime(util_pyc)}")
else:
    print("No util.pyc found (good!)")

cache_dir = '/home/pi/linux-voice-assistant/linux_voice_assistant/__pycache__'
if os.path.exists(cache_dir):
    print(f"Found __pycache__ directory: {cache_dir}")
    cache_files = os.listdir(cache_dir)
    print(f"Cache files: {cache_files}")
else:
    print("No __pycache__ directory found (good!)")

print("\n=== Volume Parsing Check ===")
import linux_voice_assistant.audio_volume

# Check get_wpctl_sink_volume source
wpctl_source = inspect.getsource(linux_voice_assistant.audio_volume.get_wpctl_sink_volume)
# Check if bytes handling is present
has_bytes_check = "isinstance(out, bytes)" in wpctl_source
print(f"get_wpctl_sink_volume has bytes handling: {has_bytes_check}")

if not has_bytes_check:
    print("ERROR: Bytes handling code NOT FOUND in get_wpctl_sink_volume!")
    print("This means old code is still running.")
