#!/usr/bin/env python3
"""Comprehensive diagnostic for test failures."""

import os
import sys
import subprocess

print("=== Repository Status ===")
result = subprocess.run(
    ["git", "log", "-1", "--oneline"],
    cwd="/home/pi/linux-voice-assistant",
    capture_output=True,
    text=True
)
print(f"Latest commit: {result.stdout.strip()}")

print("\n=== File Verification ===")
# Read the actual file on disk
util_path = "/home/pi/linux-voice-assistant/linux_voice_assistant/util.py"
with open(util_path, 'r') as f:
    content = f.read()

# Find format_mac function
start = content.find("def format_mac(")
if start == -1:
    print("ERROR: format_mac function not found in util.py!")
else:
    end = content.find("\ndef ", start + 1)
    if end == -1:
        end = content.find("\nclass ", start + 1)
    actual_code = content[start:end].strip()

    print("Actual format_mac code on disk:")
    print(actual_code)

    # Check for the critical parts
    if 'range(0, 12, 2)' in actual_code:
        print("✓ Contains correct range(0, 12, 2)")
    else:
        print("✗ MISSING correct range(0, 12, 2)")
        if 'range(0, 12)' in actual_code:
            print("  Found range(0, 12) instead - this is the bug!")

    if '.replace(":", "")' in actual_code:
        print("✓ Contains replace call")
    else:
        print("✗ MISSING replace call")

print("\n=== Import Test ===")
sys.path.insert(0, '/home/pi/linux-voice-assistant')
from linux_voice_assistant.util import format_mac

test_cases = [
    ("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff"),
    ("aabbccddeeff", "aa:bb:cc:dd:ee:ff"),
]

all_passed = True
for inp, expected in test_cases:
    result = format_mac(inp)
    passed = result == expected
    all_passed = all_passed and passed
    status = "✓" if passed else "✗"
    print(f"{status} format_mac('{inp}') = '{result}' (expected '{expected}')")

if not all_passed:
    print("\n=== PROBLEM DETECTED ===")
    print("The function is not working correctly even though the code looks right!")
    print("This suggests Python bytecode cache is still being used.")
    print("\nRun: bash clear_all_cache.sh")
else:
    print("\n=== ALL TESTS PASSED ===")
    print("The function works correctly!")
    print("If pytest still fails, the issue is elsewhere.")
