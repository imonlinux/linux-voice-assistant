#!/usr/bin/env python3
"""Quick test to verify format_mac function works correctly."""

import sys
sys.path.insert(0, '/home/pi/linux-voice-assistant')

from linux_voice_assistant.util import format_mac

# Test cases
test_cases = [
    ("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff"),
    ("aabbccddeeff", "aa:bb:cc:dd:ee:ff"),
    ("aa-bb-cc-dd-ee-ff", "aa:bb:cc:dd:ee:ff"),
    ("aabb.ccdd.eeff", "aa:bb:cc:dd:ee:ff"),
]

print("Testing format_mac function:")
for input_mac, expected in test_cases:
    result = format_mac(input_mac)
    status = "✓" if result == expected else "✗"
    print(f"{status} format_mac('{input_mac}') = '{result}' (expected '{expected}')")
    if result != expected:
        print(f"  ERROR: Got {len(result)} chars, expected {len(expected)} chars")
        print(f"  Input: {repr(input_mac)}")
        print(f"  Output: {repr(result)}")
